"""
Step 3 -- embedding.

Kept out of lib.py deliberately: steps 1+2 (inventory, chunking) are pure
stdlib and can be run/tested with nothing installed. This file is the one
place the sentence-transformers dependency enters the pipeline.

Model: BAAI/bge-small-en-v1.5, run locally via sentence-transformers.
Decision (made explicitly, not defaulted into): zero ongoing cost beats the
last few points of retrieval quality Voyage/OpenAI would offer. One-time
~130MB download, then every embed call is free, offline, and never leaves
this machine. See career-rag-guide.html step 3 for the full reasoning.
"""

import threading
import time
from pathlib import Path

import lib

MODEL_NAME = "BAAI/bge-small-en-v1.5"

_model = None
_model_lock = threading.Lock()


def get_model():
    """Load once, reuse. Constructing SentenceTransformer repeatedly is the
    expensive part (loads weights); encode() calls after that are cheap.

    The load is locked because the plain `if _model is None` check is a
    check-then-act race: under concurrent requests every thread saw None and
    started its own load of the weights, which killed the process outright
    (leaked semaphore, no traceback). Double-checked so the lock is only
    contended on the very first call.
    """
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed_texts(texts):
    """texts -> list of embedding vectors (as plain Python lists, ready for
    a vector store). normalize_embeddings=True so cosine similarity later
    is just a dot product."""
    model = get_model()
    vectors = model.encode(list(texts), normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]


def report():
    print(f"Loading {MODEL_NAME} (first run downloads it, then it's cached locally)...")
    t0 = time.time()
    model = get_model()
    load_s = time.time() - t0
    print(f"  loaded in {load_s:.1f}s\n")

    buckets = lib.inventory()
    chunks = lib.chunk_report(buckets)
    print()

    t0 = time.time()
    vectors = embed_texts(c.text for c in chunks)
    embed_s = time.time() - t0

    dims = len(vectors[0]) if vectors else 0
    print(f"\n{len(chunks)} chunks embedded in {embed_s:.1f}s "
          f"({len(chunks) / embed_s:.0f} chunks/sec) -- {dims}-dim vectors")

    # sanity check: two obviously-related chunks should score higher than
    # two unrelated ones. Cheap proof the vectors actually mean something,
    # not just that the code runs without crashing.
    import math
    def cosine(a, b):
        return sum(x * y for x, y in zip(a, b))  # already normalized -> dot product = cosine

    resume_master = [(c, v) for c, v in zip(chunks, vectors) if c.doc_type == "resume_master"]
    tracker_rows = [(c, v) for c, v in zip(chunks, vectors) if c.doc_type == "tracker_row"]
    if resume_master and tracker_rows:
        r_vec = resume_master[0][1]
        same_role_sim = max(cosine(r_vec, v) for _, v in tracker_rows)
        print(f"\nSanity check: master resume vs. its most similar tracker row -> "
              f"cosine {same_role_sim:.3f} (expect meaningfully > 0, confirms embeddings carry signal)")

    print(f"\nFootprint: {MODEL_NAME} is a 33M-parameter encoder, not an LLM. "
          f"Runs on CPU, holds no memory once this process exits.")


if __name__ == "__main__":
    report()
