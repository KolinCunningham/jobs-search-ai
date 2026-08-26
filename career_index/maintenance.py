"""
Step 10 -- keeping it current.

The one thing to run after every batch, or on a schedule: re-index (pick up
new/edited files, upsert-safe per step 6) and refresh the outreach gap
report (step 9). Deliberately does NOT run rank_new.py here -- that needs
a fresh postings.json from an actual vibatchium scrape, which isn't
something to fire blindly on every close/open; run it by hand when there's
something new to rank.

Wired into Career_RAG.command: runs once when the app window closes (the
natural "I'm done for now" moment), not on every open -- keeps startup
fast, and there's nothing to refresh yet at the start of a session that
wasn't already current at the end of the last one.
"""

import time

import lib
import outreach_gaps
import store


def run():
    t0 = time.time()
    print("=== Maintenance run ===\n")

    print("-- Re-indexing (step 6) --")
    coll, chunks = store.build()
    print(f"{coll.count()} vectors in the store\n")

    print("-- Outreach gaps (step 9) --")
    categories = outreach_gaps.report()

    elapsed = time.time() - t0
    print(f"\n=== Maintenance done in {elapsed:.1f}s ===")
    return coll, categories


if __name__ == "__main__":
    run()
