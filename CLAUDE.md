# Jobs Search AI — project instructions for Claude

An AI-driven job search system: find postings, check them against everything
already applied to, tailor a resume + cover letter per job in the applicant's
own voice, save everything for human review, and (with the human watching a
live browser view) submit applications.

## Read first, every session

1. `profile/config.json` — the applicant's name, target industries, roles,
   locations, salary floor, and voice notes. **All tailoring and searching is
   driven by this file.** If it is missing, stop and walk the user through
   `SETUP.md` instead of guessing.
2. `profile/resume_master.*` — the master resume, source of truth for tailoring.
3. `profile/cover_letters/` — the applicant's own past cover letters. Match
   their voice — rhythm, formality, phrasing — in every generated letter.
4. `applications/_tracker.md` — what has already been applied to.

## What's here

- `applications/` — one folder per application (`NOTES.md`, `outreach.md`,
  `job.json`, tailored resume + cover letter HTML/PDF per company), plus
  `_tracker.md` (application log) and `tailor.py` + the two `_template_*.html`
  files that render each application's PDFs.
- `career_index/` — local, free, offline vector search over every application
  ever made, plus scoring of new postings against the resume. Every script
  runs from inside this folder via `.venv/bin/python <script>.py`.
- `Career RAG.command` — double-click launcher; starts the query interface
  (5057) and the live job wall (5058), and closing the window stops both and
  auto-runs maintenance.
- `career-rag-guide.html` — full technical writeup of the pipeline.

## The pipeline scripts (all run from `career_index/`)

| Script | What it does |
|---|---|
| `webui.py` | Local web UI at http://127.0.0.1:5057 — Search, Memory graph, How-to tabs |
| `livewall.py` | Live job wall at http://127.0.0.1:5058 — every posting, score, tailored package and tracker row as it lands, with a per-card AI read |
| `query.py` | CLI search + dedup check (`--company "X"` — has this company come up before, what happened) |
| `rank_new.py` | Score a new posting against the resume, skip anything already tracked, record it as a searchable memory |
| `outreach_gaps.py` | Who has a drafted LinkedIn message that was never actually sent |
| `ingest.py` | Re-index after editing/adding application files — idempotent, safe to re-run |
| `maintenance.py` | `ingest.py` + `outreach_gaps.py` in one command |
| `lib.py`, `embed.py`, `store.py`, `meta.py`, `graph.py` | Internals — chunking, local bge-small embeddings, Chroma store, metadata joins, graph edges |

## The apply workflow

1. **Search** — use vibatchium (`vb explore <url>`, `vb research ...`) to find
   postings on the boards listed in `profile/config.json`, filtered to the
   configured industries/roles/locations.
2. **Dedup** — `rank_new.py` / `query.py --company` before writing anything.
3. **Tailor** — write `applications/<Company_Role>/job.json` in the applicant's
   voice (study `profile/cover_letters/`), run
   `python3 applications/tailor.py <folder>/job.json` to render the PDFs.
4. **Review gate** — everything is saved to the application folder for the
   human to read BEFORE submission. Never submit without an explicit go-ahead.
5. **Apply, watched** — before driving any application flow, start the live
   view so the human can watch the browser in real time:
   `vb --session jobs liveview start --takeover --port 9223`
   then open http://127.0.0.1:9223. The takeover flag lets the human grab the
   mouse at any point (CAPTCHAs, logins, screening questions).
6. **Record** — append the row to `applications/_tracker.md`, then run
   `career_index/maintenance.py` so the new application is searchable.

## Standing rules

- **Industry/category options** live in two places the user may ask you to
  tune: `profile/config.json` (`target_industries`, what to search for) and
  `career_index/meta.py` (`_CATEGORY_KEYWORDS`, `_COMPANY_CATEGORY_OVERRIDES` —
  how indexed applications get classified).
- **Never embed, print, or surface `_ats_credentials_*.md` content, or any
  text containing the word "password."** The pipeline has two independent
  safety nets for this (`lib.py` inventory exclusion + the content scan in
  `chunk_report()`) — don't route around them.
- **Never send an email or LinkedIn message.** Drafts only — the human presses
  send. Never submit an application without explicit approval.
- **Never fabricate data.** Every reported field (status, category, country,
  salary) is real structured data or an honestly-labeled guess — an explicit
  "Uncategorized", never an invented value. Never invent resume content the
  master resume doesn't support.
- **Verify against reality.** This codebase went through seven adversarial
  audit rounds; real bugs were found in nearly every one. If you modify
  pipeline code, run it against real data afterward.
