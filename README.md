# Jobs Search AI

An AI job-search copilot for [Claude Code](https://claude.com/claude-code):
it finds roles in your target industries, tailors a resume + cover letter for
each one **in your own voice**, saves everything for your review, and — once
you approve — fills in and submits the application in a real browser **while
you watch it live and can take over the mouse at any time**. Every application
you ever make becomes part of a local, offline, searchable memory.

> **New here? Read [`SETUP.md`](SETUP.md)** — the full walk-through of what to
> install and what personal data (your resume, your cover letters, your
> industry choices) you need to add before first use. This repo ships with
> **no personal data**: you bring your own.

## How it works

```
profile/                    YOUR data (git-ignored): config.json — name,
                            target industries, roles, locations, job boards;
                            your master resume; your own past cover letters
                            (Claude studies these to learn your voice)
        │
        ▼
1. SEARCH    Claude drives vibatchium (a stealth Chrome) across LinkedIn /
             SEEK / Indeed for roles matching profile/config.json
        │
        ▼
2. DEDUP     career_index/rank_new.py scores each posting against your
             resume vector and skips anything already in your tracker
        │
        ▼
3. TAILOR    Claude writes applications/<Company_Role>/job.json in your
             voice; tailor.py renders it through your HTML templates into
             a per-job resume PDF + cover letter PDF, plus fit NOTES.md
             and a drafted outreach message
        │
        ▼
4. REVIEW    ⛔ human gate — everything sits in the application folder for
             you to read. Nothing is ever submitted or sent without your
             explicit go-ahead. (Emails/DMs are never sent by the AI at all.)
        │
        ▼
5. APPLY     vibatchium live view (http://127.0.0.1:9223) — you literally
   (WATCHED)  watch the browser as the AI fills the application with your
             data; a takeover button hands you the mouse for logins,
             CAPTCHAs, and screening questions
        │
        ▼
6. REMEMBER  the application is appended to _tracker.md and ingested into
             a local Chroma vector store (bge-small embeddings, fully
             offline & free) — searchable forever via web UI or CLI
```

## What's in the repo

| Path | What it is |
|---|---|
| `SETUP.md` | **Start here** — install + personal-data guide |
| `CLAUDE.md` | Instructions Claude Code reads automatically — the whole workflow and its standing rules |
| `profile/` | Your identity, industry options, master resume, voice samples (ships empty; git-ignored) |
| `applications/` | One folder per application + `tailor.py` (PDF renderer) + the two HTML templates you personalize once |
| `career_index/` | The local RAG pipeline: web UI (search / memory-graph / dedup at `:5057`), CLI query, new-posting ranker, outreach-gap report, idempotent ingest |
| `Career RAG.command` | Double-click launcher for the web UI; closing the window auto-runs maintenance |
| `career-rag-guide.html` | Full technical writeup — every design decision and every bug found across seven adversarial audit rounds |

## Requirements

- macOS with Google Chrome
- [Claude Code](https://claude.com/claude-code) (with a Claude subscription)
- Python 3.11+ (`career_index` uses a local venv: sentence-transformers + ChromaDB)
- [vibatchium](https://pypi.org/project/vibatchium/) — `pipx install vibatchium` (the stealth browser + live watch-screen)
- Optional: [engram](https://github.com/KolinCunningham/engram) — persistent memory for Claude across sessions (private repo; ask for access)

## Safety rails (built in, audited)

- **Human review gate** before any submission; the AI never sends emails or messages — drafts only.
- **Credential quarantine**: two independent safety nets keep `_ats_credentials_*.md` files and any "password"-containing text out of the search index.
- **No fabrication**: tailored resumes only re-angle what your master resume actually contains; unknown fields are reported as unknown, never invented.
- The pipeline survived **seven independent adversarial audit rounds** — the fixes are documented in `career-rag-guide.html`.

## Privacy

Everything — embeddings, index, documents, tracker — lives on your machine.
`.gitignore` excludes all personal data (`profile/`, real application folders,
the vector store, credentials) so a fork/clone of this repo never carries
anyone's history.
