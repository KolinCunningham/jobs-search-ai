# Setup Guide — getting Jobs Search AI running on your machine

Written for a new user (hi Charlotte 👋). Follow top to bottom; ~30 minutes.
You'll do most of it by literally asking Claude — that's the point of the tool.

---

## 1. What this is

An AI job-search copilot you drive through **Claude Code**. It:

- searches job boards (LinkedIn / SEEK / Indeed) for roles in **your**
  industries and locations,
- checks every new posting against everything you've already applied to
  (no double-applying, ever),
- writes a **tailored resume + cover letter per job, in your voice**, rendered
  to clean PDFs, and **saves everything for your review before anything is
  sent**,
- can then fill in and submit the application in a real Chrome browser
  **while you watch it live on screen** and can grab the mouse at any moment,
- remembers every application in a local, offline search index you can query
  ("have I applied to Canva before?", "how did I describe my leadership
  experience last time?").

Nothing leaves your machine except the actual applications you approve.
The search index, embeddings, and all your documents are local and free.

## 2. Prerequisites (install once)

| Requirement | Why | How |
|---|---|---|
| **macOS + Google Chrome** | PDF rendering and the watched-browser applying both use Chrome | [google.com/chrome](https://www.google.com/chrome/) |
| **Claude Code** | The AI that drives everything | `npm install -g @anthropic-ai/claude-code`, or the desktop app — [claude.com/claude-code](https://claude.com/claude-code). Needs a Claude subscription. |
| **Python 3.11+** | The indexing pipeline | `brew install python` (or python.org) |
| **pipx** | Installs vibatchium cleanly | `brew install pipx && pipx ensurepath` |
| **vibatchium** (REQUIRED) | The stealth browser Claude uses to search and apply, with the live watch-screen | `pipx install vibatchium` — if already installed, update instead: `pipx upgrade vibatchium` |
| **engram** (optional, recommended) | Long-term memory for Claude — it remembers your search across sessions, what worked, your preferences | Private repo: `github.com/KolinCunningham/engram` — ask Kolin for access, then follow its README (Rust build + MCP config). The system works without it; Claude just starts each session colder. |

Check what you already have first — anything already installed and current can
be skipped:

```bash
python3 --version        # want 3.11+
vb --version             # vibatchium installed?
pipx upgrade vibatchium  # if yes, make sure it's current
```

## 3. Get the code and build the environment

```bash
git clone <this-repo-url> "Jobs Search AI"
cd "Jobs Search AI/career_index"
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # sentence-transformers, chromadb — few minutes first time
```

## 4. Add YOUR data (the part only you can do)

This copy ships **with no personal data** — the original owner's resumes,
history, and database were deliberately excluded. It needs yours:

1. `cd` into `profile/` and copy `config.example.json` → `config.json`.
   Fill in every field:
   - `name`, `location`, `phone`, `email`, `linkedin` — go on your documents.
   - `target_industries` — **your options live here.** The example lists the
     categories the classifier already understands (Technology, Consulting,
     Finance, Healthcare / Biotech, Energy, Construction, Media, Retail,
     Recruiting, Real Estate, Professional Services). Keep the ones you want,
     delete the rest, add your own.
   - `target_roles`, `locations`, `salary_floor`, `job_boards`.
   - `notes_for_claude` — one paragraph on how you write and how formal you
     want letters to be.
2. Drop your **master resume** in `profile/` (pdf/docx/md — everything you've
   ever done, not a tailored version).
3. Create `profile/cover_letters/` and put **2–5 cover letters you actually
   wrote** in it. This is how Claude learns to write like you and not like a
   robot.

Full details: `profile/README.md`.

## 5. One-time personalization (Claude does it)

Open Claude Code in the project folder and say:

> "Set up my resume and cover letter templates from my profile"

Claude rewrites the two `applications/_template_*.html` files with your fixed
history (name, contact, past roles, education, certs) while leaving the
`{{...}}` slots that get tailored per job. Then say:

> "Build the search index"

(which runs `career_index/ingest.py` — near-instant while you have no
applications yet, and re-run automatically after each one).

## 6. Daily use

Open Claude Code in the project folder and just talk to it:

- *"Find me new marketing roles posted this week and check them against my tracker"*
- *"Tailor an application for this posting: <url>"* — you'll get the resume
  PDF, cover letter PDF, fit notes, and a drafted outreach message **saved in
  `applications/<Company_Role>/` for you to review first**
- *"Looks good — apply, and let me watch"* — Claude starts the live browser
  view (`vb --session jobs liveview start --takeover --port 9223`, then open
  **http://127.0.0.1:9223**). You watch every click; the takeover button gives
  you the mouse for logins, CAPTCHAs, or screening questions
- *"Have I applied to <company> before?"*
- *"Who's ready to message on LinkedIn today?"*

Or double-click **`Career RAG.command`** for the point-and-click search UI
(search tab, memory graph, dedup box) at http://127.0.0.1:5057 — closing the
window runs maintenance automatically.

## 7. The rules the system enforces (worth knowing)

- **Review gate**: every resume, cover letter, and outreach message is saved
  to disk for your eyes before anything is submitted or sent. Claude never
  sends email or LinkedIn messages at all — drafts only.
- **Credential safety**: password files named `_ats_credentials_*.md` (and any
  indexed text containing the word "password") are excluded from the search
  index by two independent safety nets.
- **No fabrication**: tailored resumes only rearrange and re-angle what your
  master resume actually says. Unknown fields are reported as unknown.

## 8. Changing your industry options later

- What Claude **searches for**: edit `target_industries` / `target_roles` in
  `profile/config.json`.
- How past applications get **classified** in the index/graph: edit
  `_CATEGORY_KEYWORDS` and `_COMPANY_CATEGORY_OVERRIDES` in
  `career_index/meta.py` (or ask Claude to do it: *"add a 'Fashion' category
  that matches these kinds of companies"*).

## Troubleshooting

- **`vb: command not found`** — run `pipx ensurepath`, restart the terminal.
- **PDFs not rendering** — Chrome must be at `/Applications/Google Chrome.app`.
- **`Career RAG.command` won't open** — right-click → Open the first time
  (Gatekeeper); it may also ask for Accessibility permission (used to detect
  when you close the window).
- **Anything else** — paste the error into Claude Code inside this folder.
  `CLAUDE.md` teaches it the whole system.
