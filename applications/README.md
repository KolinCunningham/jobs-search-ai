# applications/ — one folder per job application

Every application gets its own folder, created by `tailor.py` (or by Claude),
named `Company_Role_Short/`. A finished application folder contains:

| File | What it is |
|---|---|
| `job.json` | The per-job tailoring input — tagline, profile paragraph, competency lists, current-role bullets, cover letter paragraphs |
| `Resume.html` + `<Your Name> - Resume - <Role>.pdf` | The tailored resume, rendered to PDF via headless Chrome |
| `Cover_Letter.html` + `<Your Name> - Cover Letter - <Company>.pdf` | The tailored cover letter |
| `NOTES.md` | Claude's fit analysis — real fit, real gaps, angle taken |
| `outreach.md` | Drafted LinkedIn message to the hiring contact (drafts only — you send them yourself) |

**Everything is saved for your review before anything is submitted.** The
workflow is: Claude drafts → writes the folder → you read the PDFs → you (or
Claude, with you watching the live browser view) submit.

## Files at this level

- `tailor.py` — renders `job.json` + the two templates into the HTML/PDF pair.
  Run as `python3 tailor.py <folder>/job.json`. Needs Google Chrome installed.
- `_template_resume.html` / `_template_cover.html` — your base templates.
  Do the one-time personalization described in `profile/README.md` first.
- `_tracker.md` — the hand-kept application log. The search index reads this.

## Never commit real applications

`.gitignore` excludes every application folder and the tracker from git —
they contain your personal history. Only the templates, `tailor.py`, and the
READMEs are in the repo.

## Credentials warning

If you save ATS/job-board passwords as files, name them
`_ats_credentials_<something>.md`. The indexing pipeline has two independent
safety nets that exclude those files — and any text containing the word
"password" — from the search index, so a search result can never surface a
credential. Do not rename them to something the filter won't catch.
