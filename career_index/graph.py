"""
Brain-graph data for the Search tab's visualization.

Every indexed chunk is a node. "Sectors of work" (the category field from
step 5 -- Finance, Energy, Consulting...) are the brain's regions. Edges
are REAL cosine-similarity nearest neighbors computed from the same
embeddings the rest of this pipeline already uses -- not decoration, not a
random layout. A chunk with no category (folder-level category coverage is
97% as of the _COMPANY_CATEGORY_OVERRIDES lookup in meta.py -- see that
file for the real, current number) goes in an explicit "Uncategorized"
region rather than being forced into a fake one.

Kept as its own file because it's the one place numpy enters -- the rest
of this pipeline does its (small) linear algebra by hand in plain Python.
At 547 nodes a 547x547 similarity matrix is trivial (~10ms), so this
computes fresh on every request rather than caching to disk.
"""

import numpy as np

import store

TOP_K = 4          # edges per node -- keeps it a graph, not a hairball
SIM_FLOOR = 0.55    # don't draw an edge for a "neighbor" that isn't actually similar


def build():
    coll = store.get_collection()
    data = store.retry_read(lambda: coll.get(include=["embeddings", "metadatas", "documents"]))
    ids = data["ids"]
    metadatas = data["metadatas"]
    documents = data["documents"]
    embeddings = np.array(data["embeddings"], dtype=np.float32)

    if len(ids) == 0:
        return {"nodes": [], "edges": [], "categories": []}

    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1
    unit = embeddings / norms
    sim = unit @ unit.T   # cosine similarity -- both sides unit-normalized

    nodes = []
    for meta, doc in zip(metadatas, documents):
        label = meta.get("company") or (meta.get("path") or "").split("/")[-1]
        nodes.append({
            "id": None,  # filled below, keeps this loop aligned with ids
            "label": (label or "")[:36],
            "doc_type": meta.get("doc_type", "?"),
            "category": meta.get("category") or "Uncategorized",
            "company": meta.get("company"),
            "path": meta.get("path"),
            "preview": doc[:180].strip(),
        })
    for node, cid in zip(nodes, ids):
        node["id"] = cid

    n = len(ids)
    edges = []
    seen_pairs = set()
    for i in range(n):
        row = sim[i]
        order = np.argsort(-row)
        added = 0
        for j in order:
            j = int(j)
            if j == i:
                continue
            score = float(row[j])
            if score < SIM_FLOOR:
                break   # sorted descending -- nothing past here clears the floor
            pair = (i, j) if i < j else (j, i)
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                edges.append({"source": ids[i], "target": ids[j], "weight": round(score, 3)})
            added += 1
            if added >= TOP_K:
                break

    categories = sorted({n["category"] for n in nodes})
    return {"nodes": nodes, "edges": edges, "categories": categories}


if __name__ == "__main__":
    import json
    g = build()
    print(f"{len(g['nodes'])} nodes, {len(g['edges'])} edges, "
          f"{len(g['categories'])} categories: {g['categories']}")
    isolated = len(g["nodes"]) - len({e["source"] for e in g["edges"]} | {e["target"] for e in g["edges"]})
    print(f"{isolated} node(s) with no edge above the {SIM_FLOOR} similarity floor")
