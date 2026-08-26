"""
Step 6 -- ingestion script.

The one command to run after every new application batch:

    .venv/bin/python ingest.py

Deliberately thin -- store.build() already does the real work (walk, chunk,
embed, attach metadata, upsert). This just makes that the one documented
entrypoint, instead of "run store.py and ignore the test-query part at the
bottom," which is what step 4/5 verification was actually doing.

Safe to re-run: chunk ids are stable (verified in the second audit round --
byte-identical across repeated runs on unchanged input), so Chroma's upsert
updates existing vectors and adds new ones without ever duplicating the
whole store. Proven below, not assumed -- see report().
"""

import store


def main():
    coll, chunks = store.build()
    print(f"\n{coll.count()} vectors in the store.")
    return coll.count()


if __name__ == "__main__":
    main()
