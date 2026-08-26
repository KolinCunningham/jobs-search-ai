"""
Step 4 -- vector store.

Chroma, persistent, local. Chosen over LanceDB/Qdrant/pgvector: at a few
hundred chunks there's no server to run, no Postgres instance, nothing to
manage -- just a directory on disk. See career-rag-guide.html step 4 for
the full reasoning.

Kept in its own file for the same reason embed.py is separate from lib.py:
this is where the chromadb dependency enters, isolated in career_index/.venv.
"""

import hashlib
import time
from pathlib import Path

import chromadb

import lib
import embed
import meta

STORE_PATH = Path(__file__).resolve().parent / "store"
_GENERATION_MARKER = STORE_PATH / ".generation"


def retry_read(fn, attempts=3, delay=0.2):
    """Wrap a Chroma read (a lambda calling coll.get/coll.query) with a
    short retry. Seventh audit round reproduced a real, intermittent
    `chromadb.errors.InternalError: Error finding id` on a plain read
    immediately after a cross-process write (rank_new.py's
    record_scraped_posting, then a separate webui.py process reading
    /api/graph moments later) -- roughly 1-in-5, with nothing unusual
    going on, no heavy concurrent load required to trigger it. This isn't
    a bug in this codebase to fix; it's inside chromadb's own Rust
    bindings. Retrying against a fresh collection (get_collection() picks
    up the generation marker if it changed) is the practical mitigation,
    not a real fix -- if this starts happening on every call rather than
    occasionally, that's a different, worse problem and this retry would
    just be masking it."""
    last_exc = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(delay)
    raise last_exc


_client = None
_collection = None
_cached_generation = None


def _current_generation():
    """mtime of the generation marker `build()` touches after every ingest
    -- cheap to check every call (one stat), used to detect a rebuild that
    happened in a DIFFERENT process without needing a restart."""
    try:
        return _GENERATION_MARKER.stat().st_mtime
    except FileNotFoundError:
        return None


def get_collection():
    """Cached client/collection, reused across calls -- but re-created
    when the on-disk store's generation marker changes.

    Two audit rounds landed here in sequence. Third round: a fresh
    PersistentClient on EVERY call raced under concurrent requests (10 at
    once corrupted the running server's client state, needed a manual
    restart) -- fixed by caching. Fourth round found what that fix traded
    away: a long-running process (webui.py) kept its stale cached client
    even after a SEPARATE process ran `rm -rf store && ingest.py` -- no
    error, no crash, just silently wrong/missing results forever until
    manually restarted. The generation marker fixed that case: reused (no
    race) when nothing changed, refreshed (no staleness) the first call
    after it does.

    KNOWN REMAINING GAP, found live wiring up rank_new.py's store-recording:
    the marker-triggered refresh handles a full REBUILD cleanly (confirmed:
    a fresh document ingested by a separate `rm -rf store && ingest.py` run
    became visible here with no restart), but does NOT reliably handle a
    live incremental upsert from another process while this process is
    actively serving requests -- reproduced: after `rank_new.py` upserted a
    new posting mid-session, this process's `/api/graph` route started
    throwing `chromadb.errors.InternalError: Error finding id` on every
    call, and creating a fresh Python-level PersistentClient object here
    did NOT clear it (a new object was created, generation matched, and it
    was still broken) -- the failure survives past what this cache
    invalidation can reach, which points at state held underneath the
    Python client, in Chroma's Rust bindings, not at anything visible from
    here. A full process restart did fix it (confirmed the on-disk data was
    never wrong). Practical implication: if you run `rank_new.py` while
    `webui.py` is live, restart `webui.py` afterward -- don't rely on this
    function to self-heal that specific case the way it does for a
    `ingest.py` rebuild."""
    global _client, _collection, _cached_generation
    gen = _current_generation()
    if _collection is None or gen != _cached_generation:
        _client = chromadb.PersistentClient(path=str(STORE_PATH))
        _collection = _client.get_or_create_collection("career", metadata={"hnsw:space": "cosine"})
        _cached_generation = gen
    return _collection


def _chunk_ids(chunks):
    """Stable ids from path + doc_type + a per-(path,doc_type) occurrence
    counter, so re-running ingest after a new batch upserts changed chunks
    instead of duplicating the whole store.

    NOTE: `extra` alone (section/row/part) isn't guaranteed unique -- two
    different `_tracker.md` sections can carry the same title text (e.g. a
    repeated "## Notes" header across batches), which collided on the first
    real run (11 duplicate ids, chromadb.errors.DuplicateIDError). The
    occurrence counter is what actually guarantees uniqueness; `extra` is
    kept in the hash only so a chunk's id still changes if its position
    within its group shifts."""
    seen = {}
    ids = []
    for c in chunks:
        rel = c.path.relative_to(lib.ROOT)
        group_key = f"{rel}:{c.doc_type}"
        n = seen.get(group_key, 0)
        seen[group_key] = n + 1
        ids.append(hashlib.sha1(f"{group_key}:{n}:{c.extra}".encode()).hexdigest())
    return ids


def build():
    buckets = lib.inventory()
    chunks = lib.chunk_report(buckets)
    print()

    vectors = embed.embed_texts(c.text for c in chunks)
    ids = _chunk_ids(chunks)
    assert len(set(ids)) == len(ids), "chunk id collision -- would silently drop chunks on upsert"

    # step 5 -- attach folder-level metadata (company/role/platform/salary/
    # status/sector/country/category/contact) on top of each chunk's own
    # doc_type/section/row/part. Chroma metadata values must be str/int/
    # float/bool, which meta.attach() already guarantees.
    folder_index = meta.build_folder_index()
    metadatas = [meta.attach(c, folder_index) for c in chunks]

    coll = get_collection()
    # Chroma caps upsert batch size; this corpus is small enough to go in one call,
    # but chunk it anyway so this still works if the corpus grows.
    BATCH = 200
    try:
        for i in range(0, len(chunks), BATCH):
            coll.upsert(
                ids=ids[i:i + BATCH],
                embeddings=vectors[i:i + BATCH],
                documents=[c.text for c in chunks[i:i + BATCH]],
                metadatas=metadatas[i:i + BATCH],
            )
    except Exception:
        # Fifth audit round: a batch failing partway through leaves earlier
        # batches already committed to disk with no signal anything went
        # wrong -- the exception itself propagates (never silent), but
        # nothing said WHAT was left inconsistent. Upsert is idempotent by
        # id, so the actual fix is just "run ingest.py again" -- but the
        # user needs to be told that, not left staring at a bare traceback.
        print(f"\nINGEST FAILED partway through upserting -- some chunks may be "
              f"written, some not. The store is not corrupted, just incomplete. "
              f"Re-run ingest.py (or maintenance.py) to finish the job; upsert "
              f"overwrites by id, so re-running is always safe here.")
        raise

    touch_generation()
    return coll, chunks


def touch_generation():
    """Signal that the store changed -- see get_collection()'s docstring.
    Called after build()'s own upsert, and by anything else that writes to
    the collection directly (rank_new.py's scraped-posting recording) so a
    different long-running process (webui.py) picks up the change via
    _current_generation() on its next request, instead of staying stale
    until the next full rebuild."""
    STORE_PATH.mkdir(exist_ok=True)
    _GENERATION_MARKER.write_text(str(time.time()))


def report():
    coll, chunks = build()
    print(f"\nChroma collection 'career' at {STORE_PATH}")
    print(f"  {coll.count()} vectors stored")

    size_bytes = sum(f.stat().st_size for f in STORE_PATH.rglob("*") if f.is_file())
    print(f"  {size_bytes / 1024:.0f} KB on disk")

    # prove it actually retrieves, not just stores
    query = "AI engineering role in Sydney with AWS Bedrock experience needed"
    q_vec = embed.embed_texts([query])[0]
    hits = coll.query(query_embeddings=[q_vec], n_results=3)
    print(f"\nTest query: {query!r}")
    for doc, meta, dist in zip(hits["documents"][0], hits["metadatas"][0], hits["distances"][0]):
        print(f"  [{1 - dist:.3f}] {meta.get('path')} ({meta.get('doc_type')})")
        print(f"        {doc[:120]}...")


if __name__ == "__main__":
    report()
