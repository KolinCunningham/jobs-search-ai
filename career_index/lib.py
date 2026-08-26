"""
Shared plumbing for the career RAG pipeline.

Step 1 lives here: walk the project folder, decide what's in scope, and make
damn sure nothing that shouldn't be embedded ever reaches the chunker.

Step 2 lives here too: turn each in-scope file into one or more retrieval-
sized chunks, tagged with a doc_type. Chunking is about splitting TEXT into
units -- categories like industry/country/job-age are metadata (step 5), not
chunking, and get attached later without changing how a file is split.
"""

import fnmatch
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # .../jobs ai search program/

# Files/dirs that are structurally never embedded, no matter where they sit.
# Matched case-insensitively (fnmatch.fnmatch is case-sensitive on POSIX by
# default -- a differently-cased credentials file must not slip through).
EXCLUDE_GLOBS = [
    ".env",                    # secrets, once one exists
    ".DS_Store",
    "_template_resume.html",   # structure, not content
    "_template_cover.html",
]

# Credential files get their own dedicated, case- and separator-insensitive
# check -- deliberately not folded into EXCLUDE_GLOBS, because this is the
# one exclusion rule where "close enough" naming (different case, a dash
# instead of an underscore) must still be caught. Plaintext ATS passwords.
_CREDENTIALS_RE = re.compile(r"^_ats[_-]credentials.*\.md$", re.IGNORECASE)

# Top-level things in this folder that aren't job-search corpus at all --
# excluded by being out of scope, not by filename pattern.
OUT_OF_SCOPE_DIRS = {
    "Business_Cards",          # business card design assets
    "portfolio",                # separate portfolio-site project
    "career_index",            # this pipeline's own code/index, not a document
}
OUT_OF_SCOPE_FILES = {
    "Textbook_Reference.html",  # unrelated reference doc, not job-search material
    "career-rag-guide.html",    # this pipeline's own how-to guide, not corpus
    "CLAUDE.md",                # this pipeline's own Claude Code briefing, not corpus
}

# Code, not corpus -- tailor.py etc. Nothing to retrieve from a script.
SKIP_EXTS = {".py", ".command"}   # tooling, not corpus -- .command is the launcher script

# Extensions we can pull clean text from right now vs. ones that need a
# parser added later (step 2 territory) vs. ones we never read directly
# because an .html twin already carries the same text.
TEXT_READY_EXTS = {".md", ".json"}
HTML_READY_EXTS = {".html"}
NEEDS_PARSER_EXTS = {".pdf", ".docx"}


def _is_excluded(path: Path) -> bool:
    name = path.name
    if _CREDENTIALS_RE.match(name):
        return True
    return any(fnmatch.fnmatch(name.lower(), pattern.lower()) for pattern in EXCLUDE_GLOBS)


def _is_out_of_scope(path: Path) -> bool:
    rel_parts = path.relative_to(ROOT).parts
    if rel_parts and rel_parts[0] in OUT_OF_SCOPE_DIRS:
        return True
    if path.name in OUT_OF_SCOPE_FILES:
        return True
    return False


def inventory():
    """
    Walk ROOT once and classify every file. Returns a dict of category -> [Path].
    Pure stdlib, no embedding/chunking here -- this is the step-1 safety net,
    run and read this before step 2 (chunking) touches any of these files.
    """
    buckets = {
        "indexable_now": [],     # .md / .json -- ready for step 2 as-is
        "indexable_html": [],    # .html -- ready for step 2, strip tags first
        "needs_parser": [],      # .pdf / .docx -- no reader built yet
        "excluded_secret": [],   # matched EXCLUDE_GLOBS -- must never embed
        "out_of_scope": [],      # real files, just not job-search corpus
        "unclassified": [],      # anything else -- surfaced so it isn't silently skipped
    }

    for path in sorted(ROOT.rglob("*")):
        if path.is_dir():
            continue

        if _is_excluded(path):
            buckets["excluded_secret"].append(path)
            continue

        if _is_out_of_scope(path):
            buckets["out_of_scope"].append(path)
            continue

        ext = path.suffix.lower()
        if ext in SKIP_EXTS:
            buckets["out_of_scope"].append(path)
        elif ext in TEXT_READY_EXTS:
            buckets["indexable_now"].append(path)
        elif ext in HTML_READY_EXTS:
            buckets["indexable_html"].append(path)
        elif ext in NEEDS_PARSER_EXTS:
            buckets["needs_parser"].append(path)
        else:
            buckets["unclassified"].append(path)

    return buckets


def report(buckets):
    rel = lambda p: p.relative_to(ROOT)
    print(f"ROOT: {ROOT}\n")
    for key in ["indexable_now", "indexable_html", "needs_parser",
                "excluded_secret", "out_of_scope", "unclassified"]:
        files = buckets[key]
        print(f"[{key}] {len(files)}")
        for f in files[:12]:
            print(f"    {rel(f)}")
        if len(files) > 12:
            print(f"    ... +{len(files) - 12} more")
        print()

    leaked = [f for f in buckets["indexable_now"] + buckets["indexable_html"]
              if _CREDENTIALS_RE.match(f.name)]
    assert not leaked, f"CREDENTIAL FILE LEAKED INTO INDEXABLE SET: {leaked}"
    print("Safety check passed: no credential files in any indexable bucket.")


# ─────────────────────────────────────────────────────────────────────────
# STEP 2 -- CHUNKING
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class Chunk:
    path: Path
    doc_type: str
    text: str
    extra: dict = field(default_factory=dict)   # section title, row index, part number...


# Split-size guard: only for files with no natural boundary of their own
# (a long NOTES.md, a verbose cover letter). Tables and sections already
# split small; this only fires on the rare oversized whole-file chunk.
MAX_WORDS = 400
OVERLAP_WORDS = 60

_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_DASH_RE = re.compile(r"[—–]")   # em dash, en dash
_WS_RE = re.compile(r"[ \t]{2,}")


def normalize_text(text: str) -> str:
    """Strip markdown syntax that's formatting, not signal -- **bold**,
    [text](url) links (keep the label, drop the url), and stray em/en
    dashes -- before it ever reaches the embedder. Pure noise reduction:
    doesn't touch section titles, table structure, or the words themselves.

    Deliberately does NOT strip single-asterisk *italic* markers: unlike a
    paired ** delimiter, a lone asterisk is ambiguous in this corpus (salary
    footnotes like "$150k*", stray emphasis) and a regex-based stripper can
    pair two UNRELATED asterisks and silently delete everything between them
    -- a worse outcome than leaving a stray asterisk in the text."""
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _DASH_RE.sub("-", text)
    text = _WS_RE.sub(" ", text)
    return text


def _guarded_windows(text: str):
    text = normalize_text(text)
    words = text.split()
    if not words:
        return []
    if len(words) <= MAX_WORDS:
        return [text]

    bounds = []
    start = 0
    while True:
        end = min(start + MAX_WORDS, len(words))
        bounds.append([start, end])
        if end == len(words):
            break
        start += MAX_WORDS - OVERLAP_WORDS

    # A trailing window that adds fewer than OVERLAP_WORDS words of genuinely
    # new content (past the end of the window before it) is mostly a copy of
    # its predecessor -- merge it in rather than emit a near-duplicate chunk.
    if len(bounds) > 1:
        prev_end = bounds[-2][1]
        last_start, last_end = bounds[-1]
        if last_end - prev_end < OVERLAP_WORDS:
            bounds[-2][1] = last_end
            bounds.pop()

    return [" ".join(words[s:e]) for s, e in bounds]


class _TextExtractor(HTMLParser):
    """Strip tags, keep text -- stdlib only, no bs4 dependency.

    Every tag boundary inserts a space into the output. Without this, text
    from adjacent elements (<td>Acme</td><td>Corp</td>, or two sibling <li>s)
    concatenates directly with no separator -- "Acme" + "Corp" reads as one
    word "AcmeCorp" to the embedder. Over-inserting spaces is harmless since
    text() collapses runs of whitespace afterward; under-inserting silently
    fuses unrelated words, which is the worse failure."""
    def __init__(self):
        super().__init__()
        self._skip = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True
        self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False
        self.parts.append(" ")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

    def text(self):
        return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", "".join(self.parts))).strip()


def strip_html(raw: str) -> str:
    parser = _TextExtractor()
    parser.feed(raw)
    return parser.text()


_SECTION_RE = re.compile(r"^##\s+(.*)$", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$")
# Each cell must have >=3 dashes (standard GFM separator convention). This
# deliberately does NOT match a genuine data row like "| - | - | - |" -- this
# tracker uses a single "-" as its own blank-value convention, and a looser
# regex would misclassify that data row as the header separator and swallow it.
_TABLE_SEP_RE = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+\s*$")


def _split_sections(text: str):
    """Split a markdown doc on '## ' headers. Text before the first header
    (if any) comes back under title None."""
    matches = list(_SECTION_RE.finditer(text))
    if not matches:
        return [(None, text)]
    sections = []
    if matches[0].start() > 0:
        sections.append((None, text[:matches[0].start()]))
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((title, text[start:end]))
    return sections


def _extract_tables(body: str):
    """Within one section's body, pull out markdown tables as (header, rows)
    and return them plus whatever prose text is left over."""
    lines = body.splitlines()
    tables = []
    prose_lines = []
    i = 0
    while i < len(lines):
        row_m = _TABLE_ROW_RE.match(lines[i])
        sep_ok = i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1])
        if row_m and sep_ok:
            header = [c.strip() for c in row_m.group(1).split("|")]
            j = i + 2
            rows = []
            while j < len(lines) and _TABLE_ROW_RE.match(lines[j]):
                cells = [c.strip() for c in _TABLE_ROW_RE.match(lines[j]).group(1).split("|")]
                rows.append(cells)
                j += 1
            tables.append((header, rows))
            i = j
        else:
            prose_lines.append(lines[i])
            i += 1
    return tables, "\n".join(prose_lines)


# _tracker.md has 14 distinct header rows across its batches (audited by
# enumerating every real table -- see career-rag-guide.html step 5). Map
# them all onto one canonical field set so a query for "salary" or "notes"
# hits every batch, not just whichever one happened to use that exact
# column name. Second audit round found two real headers missing from an
# earlier, reactively-built version of this map ("Asked salary" -- reversed
# word order from "Salary asked" -- and "Hiring contact (for reference)"),
# both silently dropping real data for otherwise-matched folders. This
# version was built by enumerating every actual header tuple in the file,
# not by patching misses as they were noticed.
CANONICAL_HEADER_ALIASES = {
    "slug": "Slug",
    "company": "Company",
    "role": "Role",
    "platform": "Platform",
    "source": "Platform",
    "ats": "Platform",
    "salary": "Salary",
    "salary asked": "Salary",
    "asked salary": "Salary",
    "band usd": "Salary",
    "sector": "Sector",
    "location": "Location",
    "status": "Status",
    "confirmation seen": "Notes",
    "hiring contact": "Hiring contact",
    "hiring contact (for reference)": "Hiring contact",
    "materials": "Materials",
    "folder": "Materials",
    "notes": "Notes",
    "angle taken": "Notes",
    "gap named upfront": "Notes",
    "answers given": "Notes",
    "reason": "Notes",
    "reason skipped": "Notes",
    "why blocked": "Notes",
    "blocked on": "Notes",
}


def _canonical_header(raw_header: str) -> str:
    return CANONICAL_HEADER_ALIASES.get(raw_header.strip().lower(), raw_header.strip())


def _row_text(header, cells, section_title):
    canon = [_canonical_header(h) for h in header]
    pairs = [f"{h}: {v}" for h, v in zip(canon, cells)
             if h != "#" and v and v not in ("—", "-", "")]
    prefix = f"[{section_title}] " if section_title else ""
    return normalize_text(prefix + " | ".join(pairs))


def parse_table_file(path: Path):
    """Structured (not chunked) access to every table row in a markdown
    file that follows the '## section' + table convention -- canonical
    header -> value dicts, plus which section each row came from. General
    version of what used to be tracker_rows()-only logic; step 9's
    outreach_gaps.py points this at _outreach_log.md the same way step 5's
    meta.py points it at _tracker.md."""
    if not path.exists():
        return []
    rows = []
    for title, body in _split_sections(path.read_text()):
        tables, _ = _extract_tables(body)
        for header, raw_rows in tables:
            canon = [_canonical_header(h) for h in header]
            for cells in raw_rows:
                row = {h: v for h, v in zip(canon, cells)
                       if h != "#" and v and v not in ("—", "-", "")}
                if row:
                    row["_section"] = title
                    rows.append(row)
    return rows


def tracker_rows():
    """Structured (not chunked) access to every _tracker.md row. Used by
    step 5 (meta.py) to join tracker data onto other files in the same
    application folder. Deliberately separate from the row-chunking path
    in chunk_markdown_sectioned, which flattens to text for embedding;
    this keeps the structured values around for joining and filtering."""
    return parse_table_file(ROOT / "applications" / "_tracker.md")


def chunk_markdown_sectioned(path: Path, text: str, base_doc_type: str):
    """LOG.md / RESUME_HERE.md / _tracker.md all use this: split on '## '
    sections, then within each section split any table into row-level
    chunks and keep leftover prose as its own chunk."""
    chunks = []
    for title, body in _split_sections(text):
        tables, prose = _extract_tables(body)
        for header, rows in tables:
            for idx, row in enumerate(rows):
                row_text = _row_text(header, row, title)
                if row_text.strip():
                    chunks.append(Chunk(path, f"{base_doc_type}_row", row_text,
                                         {"section": title, "row": idx}))
        prose = prose.strip()
        if prose:
            for part_i, window in enumerate(_guarded_windows(prose)):
                chunks.append(Chunk(path, f"{base_doc_type}_note", window,
                                     {"section": title, "part": part_i}))
    return chunks


def _format_json_value(v) -> str:
    if isinstance(v, list):
        return "; ".join(_format_json_value(x) for x in v)
    if isinstance(v, dict):
        return "; ".join(f"{k}: {_format_json_value(x)}" for k, x in v.items())
    return str(v)


def chunk_json(path: Path, raw: str):
    data = json.loads(raw)
    lines = [f"{k}: {_format_json_value(v)}" for k, v in data.items() if v not in (None, "", [], {})]
    return [Chunk(path, "job_meta", "\n".join(lines), {})]


def chunk_whole_file(path: Path, text: str, doc_type: str):
    return [Chunk(path, doc_type, w, {"part": i})
            for i, w in enumerate(_guarded_windows(text.strip()))]


def _html_doc_type(path: Path) -> str:
    if path.parent == ROOT:
        return "resume_master"
    name = path.name
    if name == "Cover_Letter.html":
        return "cover"
    if name == "Resume.html":
        return "resume_tailored"
    return "supplementary"


def chunk_file(path: Path):
    """Dispatch by filename/extension -- the one place chunking rules live."""
    name = path.name
    ext = path.suffix.lower()

    if name == "_tracker.md":
        return chunk_markdown_sectioned(path, path.read_text(), "tracker")
    if name in ("LOG.md", "RESUME_HERE.md"):
        return chunk_markdown_sectioned(path, path.read_text(), "log")
    if ext == ".json":
        return chunk_json(path, path.read_text())
    if name == "NOTES.md":
        return chunk_whole_file(path, path.read_text(), "notes")
    if name == "outreach.md":
        return chunk_whole_file(path, path.read_text(), "outreach")
    if ext == ".md":
        return chunk_whole_file(path, path.read_text(), "note")
    if ext == ".html":
        return chunk_whole_file(path, strip_html(path.read_text()), _html_doc_type(path))

    raise ValueError(f"no chunking rule for {path}")


def chunk_report(buckets):
    files = buckets["indexable_now"] + buckets["indexable_html"]
    all_chunks = []
    for f in files:
        all_chunks.extend(chunk_file(f))

    by_type = {}
    split_files = 0
    for c in all_chunks:
        by_type.setdefault(c.doc_type, []).append(c)

    word_counts = [len(c.text.split()) for c in all_chunks]
    over_guard = sum(1 for c in all_chunks if c.extra.get("part", 0) > 0)

    print(f"{len(files)} files -> {len(all_chunks)} chunks\n")
    for doc_type, chunks in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
        avg_words = sum(len(c.text.split()) for c in chunks) / len(chunks)
        print(f"  [{doc_type}] {len(chunks)} chunks, avg {avg_words:.0f} words")
    print(f"\n  Longest chunk: {max(word_counts)} words")
    print(f"  Chunks produced by the {MAX_WORDS}-word split guard: {over_guard}")

    empty = [c for c in all_chunks if not c.text.strip()]
    assert not empty, f"{len(empty)} empty chunks produced -- fix the source rule"
    print("Safety check passed: no empty chunks.")

    # Second, independent credential check -- content-based, not filename-based.
    # This catches what filename exclusion structurally can't: a legitimate,
    # in-scope file (RESUME_HERE.md, a handover doc) that has an actual
    # plaintext password typed into its prose. Quarantine those specific
    # chunks rather than crash the whole run, and never print the secret
    # itself -- only which file/section it came from, so it can be redacted.
    _has_password = lambda c: bool(re.search(r"\bpassword\b", c.text, re.IGNORECASE))
    flagged = [c for c in all_chunks if _has_password(c)]
    clean_chunks = [c for c in all_chunks if not _has_password(c)]

    if flagged:
        print(f"\nSECURITY: {len(flagged)} chunk(s) mention 'password' -- quarantined, NOT included below:")
        for c in flagged:
            print(f"    {c.path.relative_to(ROOT)}  [{c.doc_type}]  section={c.extra.get('section')!r}")
        print("  -> go redact the actual password text in those files/sections.\n")
    print("Safety check passed: password-bearing chunks filtered before returning (content never printed).")
    return clean_chunks


if __name__ == "__main__":
    buckets = inventory()
    report(buckets)
    print("\n" + "=" * 60 + "\n")
    chunk_report(buckets)
