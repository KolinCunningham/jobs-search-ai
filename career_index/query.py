"""
Step 7 -- query interface.

    .venv/bin/python query.py "what did I tell Deloitte about the Bedrock gap"
    .venv/bin/python query.py "AI role in energy sector" --filter country=Australia --n 5
    .venv/bin/python query.py --company "Deloitte"          # dedup check before a new application

A CLI is enough to start -- this can become a Claude Code skill later, the
same way tailor.py already is one.
"""

import argparse

import embed
import store


def distinct_values(field):
    """Every real, non-null value the store currently holds for one
    metadata field -- used to populate dropdown filters from the actual
    data instead of a hardcoded guess that goes stale."""
    coll = store.get_collection()
    all_meta = store.retry_read(lambda: coll.get(include=["metadatas"]))["metadatas"]
    return sorted({m[field] for m in all_meta if m.get(field)})


def search(text, n=5, where=None):
    coll = store.get_collection()
    q_vec = embed.embed_texts([text])[0]
    kwargs = {"query_embeddings": [q_vec], "n_results": n}
    if where:
        kwargs["where"] = where if len(where) == 1 else {"$and": [{k: v} for k, v in where.items()]}
    hits = store.retry_read(lambda: coll.query(**kwargs))
    results = []
    for doc, meta, dist, cid in zip(hits["documents"][0], hits["metadatas"][0],
                                     hits["distances"][0], hits["ids"][0]):
        results.append({"score": 1 - dist, "doc": doc, "meta": meta, "id": cid})
    return results


def print_results(results):
    if not results:
        print("no results")
        return
    for r in results:
        m = r["meta"]
        tag = " ".join(f"{k}={m[k]}" for k in ("company", "country", "category", "status") if m.get(k))
        print(f"[{r['score']:.3f}] {m.get('path')} ({m.get('doc_type')})  {tag}")
        preview = r["doc"].strip().replace("\n", " ")
        print(f"    {preview[:160]}{'...' if len(preview) > 160 else ''}")


def dedup_check(company_query, n=8):
    """Has this company already been applied to, under this or another req?
    Searches company metadata AND raw text directly (substring,
    case-insensitive) rather than semantically -- this needs an exact
    identity match, not a similar one.

    Deliberately searches BOTH tracker_row and tracker_note, not just
    tracker_row: found live while testing this, a company can be mentioned
    only in _tracker.md's PROSE sections ("Open, needs applicant personally")
    with no table row at all -- Deloitte_Lead_FDE_Anthropic is exactly this
    case. A tracker_row-only search silently missed it."""
    coll = store.get_collection()
    all_chunks = store.retry_read(lambda: coll.get(
        where={"doc_type": {"$in": ["tracker_row", "tracker_note"]}},
        include=["documents", "metadatas"],
    ))
    q = company_query.lower()
    hits = []
    for doc, meta, cid in zip(all_chunks["documents"], all_chunks["metadatas"], all_chunks["ids"]):
        if q in (meta.get("company") or "").lower() or q in doc.lower():
            hits.append((doc, meta, cid))
    if not hits:
        print(f"No tracker mention of '{company_query}' -- looks like a new company.")
        return []
    print(f"{len(hits)} tracker mention(s) of '{company_query}':")
    for doc, meta, _cid in hits:
        print(f"  [{meta.get('doc_type')}] status={meta.get('status')!r}  {doc[:140]}")
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?", help="semantic search text")
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--filter", action="append", default=[], metavar="key=value",
                     help="metadata filter, repeatable, e.g. --filter country=Australia")
    ap.add_argument("--company", help="dedup check: has this company already been applied to?")
    args = ap.parse_args()

    if args.company:
        dedup_check(args.company)
        return

    if not args.query:
        ap.error("give a query, or use --company for a dedup check")

    where = {}
    for f in args.filter:
        k, _, v = f.partition("=")
        where[k] = v

    results = search(args.query, n=args.n, where=where or None)
    print_results(results)


if __name__ == "__main__":
    main()
