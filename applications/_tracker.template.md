# Application Tracker

One row per application, appended as each is submitted. The `#`, `Company`,
`Role`, and `Platform` columns are load-bearing — the search index and the
dedup checker parse them. Keep the column layout exactly as-is.

## Applied

| # | Company | Role | Salary | Platform | Materials | Hiring contact | Notes |
|---|---|---|---|---|---|---|---|

## Queued / blocked

Roles found but not yet submitted (login walls, questions needing a human
answer, CAPTCHAs) go here with a one-line reason each.

---
*This is the committed template. Copy it to `_tracker.md` (git-ignored) before
your first application — or Claude will do it on first run.*
