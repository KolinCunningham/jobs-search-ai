"""
Step 8 -- live job matching. The other half of "a RAG database that also
searches for jobs": embed a freshly scraped posting, score it against the
resume, and dedupe it against everything already in the tracker before it
reaches a shortlist.

    .venv/bin/python rank_new.py postings.json

postings.json: a list of {"company", "role", "text", "url"} -- whatever a
vibatchium `vb explore`/`vb research` run scrapes. This script doesn't do
the scraping itself; that's a browser-automation concern, this is the
matching concern.

Output: new_matches.md in the project root, ranked highest fit first,
skipping anything already mentioned in the tracker. Every NEW (non-
duplicate) posting also gets embedded and upserted into the permanent
store as doc_type="scraped_posting" -- it becomes a real, searchable,
graph-visible memory from this point on, not just a line in a throwaway
report. Upsert is keyed by a stable hash of (company, role, url), so
re-running this on the same posting updates it in place rather than
duplicating it -- same guarantee steps 4/6 already rely on.
"""

import hashlib
import json
import sys
from pathlib import Path

import embed
import lib
import store


def _cosine(a, b):
    return sum(x * y for x, y in zip(a, b))  # both already normalized -> dot product = cosine


def _mean(vecs):
    dims = len(vecs[0])
    return [sum(v[i] for v in vecs) / len(vecs) for i in range(dims)]


def _resume_vector():
    """Average the two master resume variants (Principal FDE + Energy)
    equally, then renormalize -- NOT a flat average of every chunk.

    Third audit round found two compounding bugs in the naive version: (1)
    a flat per-chunk average isn't unit-length (measured norm 0.907, not
    1.0), so treating the dot product with it as cosine similarity silently
    deflated every fit_score ~9%; (2) it weighted by chunk COUNT, not by
    variant -- Principal FDE (2 chunks) got 40% weight, Energy (3 chunks)
    got 60%, contradicting the intent to compare against both equally.
    Averaging per-variant first, then the two variants, then renormalizing
    fixes both at once."""
    coll = store.get_collection()
    rows = coll.get(where={"doc_type": "resume_master"}, include=["embeddings", "metadatas"])
    vecs, metas = rows["embeddings"], rows["metadatas"]
    if len(vecs) == 0:
        raise RuntimeError("no resume_master chunks in the store -- run ingest.py first")

    by_variant = {}
    for vec, meta in zip(vecs, metas):
        by_variant.setdefault(meta["path"], []).append(vec)

    variant_means = [_mean(v) for v in by_variant.values()]
    combined = _mean(variant_means)
    norm = sum(x * x for x in combined) ** 0.5
    return [x / norm for x in combined]


def rank(postings, top_precedent=3):
    resume_vec = _resume_vector()
    coll = store.get_collection()

    # Fetched once, outside the loop -- neither of these changes per
    # posting. The original version re-fetched both from Chroma on every
    # single posting (real, if low-severity, waste flagged in the third
    # audit round); at 93 tracker chunks + 46 notes chunks this didn't
    # matter yet, but it doesn't scale and there's no reason to pay for it.
    tracker_chunks = coll.get(
        where={"doc_type": {"$in": ["tracker_row", "tracker_note"]}},
        include=["documents", "metadatas"],
    )
    notes = coll.get(where={"doc_type": "notes"}, include=["documents", "metadatas", "embeddings"])

    results = []
    for posting in postings:
        company = posting["company"]
        # dedup logic re-implemented inline rather than calling
        # query.dedup_check() directly -- that one prints straight to
        # stdout for interactive CLI use, which would interleave with this
        # script's own summary output. Kept behaviorally identical to it
        # (verified in the third audit round) so the two never disagree.
        q_low = company.lower()
        already = [
            (doc, meta) for doc, meta in zip(tracker_chunks["documents"], tracker_chunks["metadatas"])
            if q_low in (meta.get("company") or "").lower() or q_low in doc.lower()
        ]
        if already:
            results.append({"posting": posting, "status": "already_tracked", "matches": already})
            continue

        jd_vec = embed.embed_texts([posting["text"]])[0]
        fit_score = _cosine(jd_vec, resume_vec)

        scored_notes = sorted(
            zip(notes["documents"], notes["metadatas"], notes["embeddings"]),
            key=lambda t: -_cosine(jd_vec, t[2]),
        )[:top_precedent]

        record_scraped_posting(posting, jd_vec, fit_score, coll)

        results.append({
            "posting": posting, "status": "new", "fit_score": fit_score,
            "precedent": [(doc, meta) for doc, meta, _ in scored_notes],
        })

    if any(r["status"] == "new" for r in results):
        store.touch_generation()

    return results


def _posting_id(posting):
    key = f"{posting['company']}:{posting.get('role', '')}:{posting.get('url', '')}"
    return "scraped_" + hashlib.sha1(key.encode()).hexdigest()


def record_scraped_posting(posting, jd_vec, fit_score, coll):
    """Make a new, non-duplicate posting a permanent memory on disk -- but
    NOT reproducibly permanent the way the rest of this index is. Every
    other chunk here is derived from a real file in applications/, so
    `rm -rf store && ingest.py` always rebuilds the full picture. A scraped
    posting has no source file -- the vector store IS its only home. Found
    live: a `rm -rf store` rebuild during testing silently deleted two
    recorded postings with nothing to restore them from. Not a bug to fix
    (there's no file to re-derive them from, by design), but worth knowing
    before running that reset command -- from
    the next fresh process onward it's searchable via query.py, checkable
    via dedup_check, and visible in the Memory graph -- not just for this
    one run. NOT guaranteed instant in an already-running webui.py, though
    (see store.get_collection()'s docstring for the real, confirmed
    limitation there). Category left unset (shows as
    "Uncategorized" in the graph) rather than guessed -- this pipeline
    doesn't have a classifier confident enough to run unsupervised on a
    brand-new company it's never seen before; meta.py's own category
    detection needs a NOTES.md write-up that doesn't exist yet for a
    posting nobody has evaluated by hand."""
    coll.upsert(
        ids=[_posting_id(posting)],
        embeddings=[jd_vec],
        documents=[posting["text"]],
        metadatas=[{
            "doc_type": "scraped_posting",
            "company": posting["company"],
            "role": posting.get("role", ""),
            "path": posting.get("url", ""),
            "fit_score": round(fit_score, 4),
        }],
    )


def write_report(results, out_path):
    new_ranked = sorted([r for r in results if r["status"] == "new"], key=lambda r: -r["fit_score"])
    already = [r for r in results if r["status"] == "already_tracked"]

    lines = ["# New job matches\n"]
    lines.append(f"{len(new_ranked)} new, {len(already)} already tracked (skipped).\n")

    for r in new_ranked:
        p = r["posting"]
        lines.append(f"## {p['company']} — {p['role']}  (fit {r['fit_score']:.3f})")
        lines.append(f"{p['url']}\n")
        lines.append("Closest precedent (most similar past NOTES.md, the angle already used):")
        for doc, meta in r["precedent"]:
            lines.append(f"- `{meta.get('path')}`: {doc[:160].strip()}...")
        lines.append("")

    if already:
        lines.append("## Already tracked, skipped\n")
        for r in already:
            p = r["posting"]
            lines.append(f"- **{p['company']}** — {len(r['matches'])} tracker mention(s) already exist")

    out_path.write_text("\n".join(lines))
    return new_ranked, already


def main():
    if len(sys.argv) < 2:
        print("usage: rank_new.py postings.json")
        sys.exit(1)

    postings = json.loads(Path(sys.argv[1]).read_text())
    results = rank(postings)
    out_path = lib.ROOT / "new_matches.md"
    new_ranked, already = write_report(results, out_path)

    print(f"{len(new_ranked)} new postings ranked, {len(already)} already tracked (skipped)\n")
    for r in new_ranked:
        p = r["posting"]
        print(f"  fit {r['fit_score']:.3f}  {p['company']} — {p['role']}  (recorded in the store)")
    for r in already:
        p = r["posting"]
        print(f"  SKIP (tracked)  {p['company']} — {len(r['matches'])} existing mention(s)")
    print(f"\nWritten to {out_path.relative_to(lib.ROOT)}")
    if new_ranked:
        print(f"{len(new_ranked)} posting(s) also embedded and upserted into the store -- "
              f"searchable and dedup-checkable from a fresh `.venv/bin/python query.py` call right away.")
        print(f"If webui.py is currently running, restart it to see these in Search/Memory -- "
              f"a live server's cached connection doesn't reliably pick up an incremental write "
              f"from another process (confirmed limitation, see store.py's get_collection() docstring); "
              f"it only self-heals cleanly after a full ingest.py rebuild.")


if __name__ == "__main__":
    main()
