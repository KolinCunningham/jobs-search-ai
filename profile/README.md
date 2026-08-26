# profile/ — your personal data lives here (and only here)

This folder ships **empty on purpose**. The previous owner's resumes,
applications, and history were deliberately not copied — you supply your own.
Nothing in this folder is committed to git (see `.gitignore`).

## What to put here before your first run

1. **`config.json`** — copy `config.example.json` to `config.json` and fill in
   every field. This is where you set your name (used on generated PDF
   filenames), target industries, roles, locations, salary floor, and the
   job boards you want searched. Claude reads this file at the start of every
   job-search session.

2. **`resume_master.(pdf|docx|md)`** — your full master resume, everything
   you've ever done. This is the source of truth Claude tailors from, and the
   document the matching engine scores new job postings against.

3. **`cover_letters/`** — a folder with 2–5 cover letters you have actually
   written yourself, as `.pdf`, `.docx`, `.txt`, or `.md`. Claude studies these
   to learn *your* voice — sentence rhythm, formality, how you open and close —
   so generated letters sound like you, not like an AI.

## One-time template setup

After filling this folder, ask Claude:

> "Set up my resume and cover letter templates from my profile"

Claude will take your master resume and rewrite
`applications/_template_resume.html` and `applications/_template_cover.html` —
replacing every `YOUR NAME` / example block with your real fixed history while
leaving the `{{DOUBLE_BRACE}}` slots alone (those get filled per-job).
