"""livewall.py -- live job wall.

Watches the corpus for anything the bots record and streams it to a browser as
it lands: scraped posting feeds, scored matches, tailored application folders,
and new tracker rows. Read-only: this process never writes into the corpus.

    .venv/bin/python livewall.py          # http://127.0.0.1:5058
    .venv/bin/python livewall.py --no-ai  # skip the narrator lane

The narrator lane shells out to `claude -p --model haiku` for a short read on
each new record. It is given only the fields already on the card and is told to
say "unclear" rather than guess; if the CLI is missing or slow the card falls
back to a deterministic summary built from those same fields. Nothing on the
wall is invented.
"""

import argparse
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

from flask import Flask, Response, jsonify, request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
APPS = ROOT / "applications"
TRACKER = APPS / "_tracker.md"

POLL_SECONDS = 2.0
MAX_EVENTS = 4000
NARRATOR_TIMEOUT = 45

# The narrator is a plain one-shot call, so strip everything the interactive
# CLI would normally load: MCP servers, settings, hooks and tools. Without this
# each call costs ~15s and the hook output lands in the narration text. Run it
# from a neutral directory so no project CLAUDE.md is picked up either.
CLAUDE_CMD = [
    "claude", "-p", "--model", "haiku",
    "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
    "--setting-sources", "",
    "--disallowed-tools", "*",
]
NEUTRAL_CWD = tempfile.mkdtemp(prefix="livewall-narrator-")

# Same guard as lib.py: credential files never reach the wall, and neither does
# any text mentioning a password. Two independent nets, matching the pipeline.
_CREDENTIALS_RE = re.compile(r"^_ats[_-]credentials.*\.md$", re.IGNORECASE)
_PASSWORD_RE = re.compile(r"\bpassword\b", re.IGNORECASE)


def _safe(name: str, *texts: str) -> bool:
    """True when this record is safe to surface."""
    if _CREDENTIALS_RE.match(name):
        return False
    return not any(_PASSWORD_RE.search(t or "") for t in texts)


# ---------------------------------------------------------------- collectors
# Each collector returns {key: record}. A record is a plain dict; the watcher
# diffs keys between passes and emits whatever is new or changed.

def _rec(kind, key, company, role, **extra):
    r = {"kind": kind, "key": key, "company": company or "Unknown",
         "role": role or "Unknown role"}
    r.update(extra)
    return r


_MATCH_HEAD = re.compile(r"^##\s+(.+?)\s+\(fit\s+([0-9.]+)\)\s*$")
_URL_LINE = re.compile(r"^(https?://\S+)\s*$")


def collect_matches():
    """Scored postings: `## Company - Role  (fit 0.773)` blocks in any
    *matches*.md / *_report*.md the bots drop at the project root."""
    out = {}
    # Every markdown at the two scan roots, not a filename pattern: a bot can
    # call its shortlist anything, and only files that actually carry a
    # `(fit 0.xxx)` header produce records. 31 files, ~400K, cheap to re-read.
    files = list(ROOT.glob("*.md")) + list(APPS.glob("*.md"))
    for f in files:
        if not _safe(f.name):
            continue
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        if _PASSWORD_RE.search(text):
            continue  # whole file quarantined, same policy as chunk_report()
        lines = text.splitlines()
        skipped = "already tracked" in text.lower()
        for i, line in enumerate(lines):
            m = _MATCH_HEAD.match(line)
            if not m:
                continue
            title, fit = m.group(1).strip(), m.group(2)
            company, _, role = title.partition("—")   # em dash
            if not role:
                company, _, role = title.partition(" - ")
            url = ""
            precedent = ""
            for nxt in lines[i + 1:i + 8]:
                u = _URL_LINE.match(nxt.strip())
                if u and not url:
                    url = u.group(1)
                if nxt.strip().startswith("- `applications/") and not precedent:
                    precedent = nxt.strip().split("`")[1]
            key = f"match::{f.name}::{title}"
            out[key] = _rec("match", key, company.strip(), role.strip(),
                            fit=float(fit), url=url, precedent=precedent,
                            source=f.name, skipped=skipped,
                            ts=f.stat().st_mtime)
    return out


def collect_postings():
    """Raw scraped cards: the new_postings_*.json / postings_*.json feeds the
    search bots write before anything is scored."""
    out = {}
    for f in list(HERE.glob("*postings*.json")) + list(ROOT.glob("*postings*.json")):
        try:
            data = json.loads(f.read_text(errors="replace"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            data = data.get("postings") or data.get("jobs") or []
        if not isinstance(data, list):
            continue
        mtime = f.stat().st_mtime
        for n, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            company = item.get("company") or item.get("employer") or ""
            role = item.get("title") or item.get("role") or item.get("role_short") or ""
            blob = " ".join(str(v) for v in item.values())
            if not _safe(f.name, blob):
                continue
            key = f"posting::{f.name}::{company}::{role}::{n}"
            out[key] = _rec("posting", key, company, role,
                            url=item.get("url") or item.get("link") or "",
                            location=item.get("location") or "",
                            source=f.name, ts=mtime,
                            desc=(item.get("description") or item.get("jd") or "")[:900])
    return out


def collect_applications():
    """Tailored application folders -- a job.json means a real package exists."""
    out = {}
    if not APPS.is_dir():
        return out
    for d in APPS.iterdir():
        if not d.is_dir() or d.name.startswith(("_", ".")) or d.name == "__pycache__":
            continue
        jf = d / "job.json"
        if not jf.is_file():
            continue
        try:
            job = json.loads(jf.read_text(errors="replace"))
        except (OSError, ValueError):
            continue
        blob = " ".join(str(v) for v in job.values())
        if not _safe(d.name, blob):
            continue
        assets = sorted(p.name for p in d.iterdir() if p.suffix.lower() in
                        (".pdf", ".html") and not p.name.startswith("_"))
        key = f"app::{d.name}"
        out[key] = _rec("application", key, job.get("company"),
                        job.get("role_short"),
                        tagline=job.get("tagline", ""),
                        slug=job.get("slug", d.name),
                        assets=assets,
                        has_notes=(d / "NOTES.md").is_file(),
                        has_outreach=(d / "outreach.md").is_file(),
                        desc=(job.get("p1") or "")[:900],
                        ts=jf.stat().st_mtime)
    return out


_MD = re.compile(r"\*\*|`|_{2,}")
_SEP = re.compile(r"^\|[\s\-:|]+\|?\s*$")


def _clean(cell: str) -> str:
    """Strip the markdown the tracker writes inline (bold, code ticks)."""
    return _MD.sub("", cell).strip()


def _cells(line: str):
    return [_clean(c) for c in line.strip().strip("|").split("|")]


# The tracker's 30-odd tables do not share a layout. Rather than guess a status
# per row, the table's own header tells us what kind of list it is. Anything
# that isn't clearly a submission log is labelled by what it actually is.
_SUBMITTED_COLS = ("materials", "confirmation seen", "status", "salary asked")
_SHORTLIST_COLS = ("why", "gate", "reason", "risk", "angle taken", "where it stopped",
                   "blocked on", "blocker", "why blocked", "reason skipped")


def _row_status(cols, section):
    joined = " ".join(cols)
    if any(c in joined for c in _SUBMITTED_COLS):
        return "logged"
    if any(c in joined for c in _SHORTLIST_COLS):
        return "shortlist"
    return "listed"


def collect_tracker():
    """Every job row in _tracker.md, from any of its tables.

    Columns are located by name from each table's own header, because the
    batches disagree on layout (some lead with an ATS reference before
    Company). The status shown on the card comes from the table's header --
    a shortlist table is never labelled as a submission.
    """
    out = {}
    if not TRACKER.is_file():
        return out
    try:
        text = TRACKER.read_text(errors="replace")
    except OSError:
        return out
    mtime = TRACKER.stat().st_mtime
    section, cols, status = "Tracker", [], "listed"
    for line in text.splitlines():
        if line.startswith("## "):
            section, cols = line[3:].strip(), []
            continue
        if not line.lstrip().startswith("|"):
            continue
        if _SEP.match(line):
            continue
        cells = _cells(line)
        low = [c.lower() for c in cells]
        if "company" in low and "role" in low:          # this table's header
            cols = low
            status = _row_status(cols, section)
            continue
        if not cols or _PASSWORD_RE.search(line):
            continue

        def col(*names, default=""):
            for n in names:
                for i, c in enumerate(cols):
                    if c.startswith(n) and i < len(cells):
                        return cells[i]
            return default

        company, role = col("company"), col("role")
        if not company or not role:
            continue
        key = f"tracker::{section}::{company}::{role}"
        out[key] = _rec("tracker", key, company, role,
                        section=section, status=status,
                        salary=col("salary", "band"),
                        platform=col("platform", "ats", "source"),
                        ts=mtime)
    return out


COLLECTORS = (collect_postings, collect_matches, collect_applications, collect_tracker)


# ------------------------------------------------------------------ narrator

class Narrator:
    """Short grounded read on each record via `claude -p --model haiku`.

    Only the fields already visible on the card are sent. On any failure the
    card keeps its deterministic fallback line rather than showing nothing.
    """

    BASE = (
        "You are labelling one job record on a live job-search wall.{who}\n\n"
        "Write ONE sentence (max 28 words) reading this record: what the role "
        "is{fit}. A job title and company are enough to write a useful sentence "
        "-- do so. Never state a fact that is not in the record; say a field is "
        "not stated rather than guessing it. Only if BOTH company and role are "
        "missing, reply exactly: too thin to judge. No preamble, no quotes, "
        "just the sentence.\n\n"
        "RECORD:\n"
    )

    @staticmethod
    def _prompt():
        """Build the narration prompt from profile/config.json when it exists.

        The applicant's background is not hardcoded: without a config the
        narrator simply describes the role and says nothing about fit.
        """
        who, fit = "", ""
        try:
            cfg = json.loads((ROOT / "profile" / "config.json").read_text())
        except (OSError, ValueError):
            cfg = {}
        parts = []
        for key in ("target_roles", "target_industries", "locations"):
            vals = [v for v in (cfg.get(key) or []) if isinstance(v, str)][:4]
            if vals:
                parts.append(f"{key.replace('_', ' ')}: {', '.join(vals)}")
        if cfg.get("notes_for_claude"):
            parts.append(str(cfg["notes_for_claude"])[:300])
        if parts:
            who = (" The applicant is searching for -- " + "; ".join(parts) + ".")
            fit = " and where it sits against that search"
        return Narrator.BASE.format(who=who, fit=fit)

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.prompt = self._prompt()
        self.q = deque()
        self.lock = threading.Lock()
        self.out = {}          # key -> {"text":..., "by": "ai"|"fallback"}
        self.available = None  # None = untested

    @staticmethod
    def fallback(rec):
        bits = []
        if rec["kind"] == "match":
            bits.append(f"scored {rec.get('fit', 0):.3f} against the master resume")
            if rec.get("precedent"):
                bits.append("closest precedent " + Path(rec["precedent"]).parent.name)
        elif rec["kind"] == "application":
            n = len(rec.get("assets") or [])
            bits.append(f"{n} tailored file(s) rendered" if n else "package started, no PDFs yet")
        elif rec["kind"] == "tracker":
            bits.append(f"{rec.get('status')} in tracker section {rec.get('section')}")
            if rec.get("platform"):
                bits.append("via " + rec["platform"])
        else:
            bits.append("scraped posting, not yet scored")
            if rec.get("location"):
                bits.append(rec["location"])
        return "; ".join(bits) + "."

    def submit(self, rec):
        """Queue a record for narration. Anything landing live jumps ahead of
        the startup backfill -- the wall is about what is arriving now."""
        with self.lock:
            self.out[rec["key"]] = {"text": self.fallback(rec), "by": "fallback"}
            if not self.enabled:
                return
            if rec.get("backfill"):
                self.q.append(rec)
            else:
                self.q.appendleft(rec)

    def get(self, key):
        with self.lock:
            return self.out.get(key)

    def _ask(self, rec):
        fields = {k: v for k, v in rec.items()
                  if k in ("company", "role", "location", "fit", "tagline",
                           "salary", "platform", "section", "status", "desc",
                           "precedent")
                  and v not in ("", None)}
        payload = json.dumps(fields, indent=1)[:2500]
        if _PASSWORD_RE.search(payload):
            return None
        try:
            p = subprocess.run(
                CLAUDE_CMD,
                input=self.prompt + payload, capture_output=True, text=True,
                timeout=NARRATOR_TIMEOUT,
                cwd=NEUTRAL_CWD,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if p.returncode != 0:
            return None
        line = " ".join(p.stdout.strip().split())
        return line[:260] or None

    def run(self):
        while True:
            rec = None
            with self.lock:
                if self.q:
                    rec = self.q.popleft()
            if rec is None:
                time.sleep(0.5)
                continue
            text = self._ask(rec)
            with self.lock:
                self.available = text is not None if self.available is None else \
                    (self.available or text is not None)
                if text:
                    self.out[rec["key"]] = {"text": text, "by": "ai"}


# ------------------------------------------------------------------- watcher

class Wall:
    def __init__(self, narrator):
        self.narrator = narrator
        self.lock = threading.Lock()
        self.seen = {}
        self.events = []      # [{seq, rec, new}]
        self.seq = 0
        self.scans = 0
        self.last_scan = 0.0
        self.error = ""

    def scan(self):
        found = {}
        err = ""
        for c in COLLECTORS:
            try:
                found.update(c())
            except Exception as e:            # a half-written file must not kill the wall
                err = f"{c.__name__}: {e}"
        fresh = []
        with self.lock:
            first_pass = not self.seen
            for k in [k for k in self.seen if k not in found]:
                self.seq += 1
                gone = dict(self.seen.pop(k), seq=self.seq, seen_at=time.time(),
                            removed=True, backfill=False)
                self.events.append(gone)
            # Oldest first, so the newest record ends up with the highest seq
            # and lands at the top of the wall rather than at the bottom.
            for k, rec in sorted(found.items(), key=lambda kv: kv[1].get("ts", 0)):
                prev = self.seen.get(k)
                if prev == rec:
                    continue
                self.seq += 1
                rec = dict(rec, seq=self.seq, seen_at=time.time(),
                           backfill=first_pass, updated=prev is not None)
                self.seen[k] = found[k]
                self.events.append(rec)
                fresh.append(rec)
            if len(self.events) > MAX_EVENTS:
                self.events = self.events[-MAX_EVENTS:]
            self.scans += 1
            self.last_scan = time.time()
            self.error = err
        for rec in fresh:
            self.narrator.submit(rec)

    def run(self):
        while True:
            self.scan()
            time.sleep(POLL_SECONDS)

    def since(self, seq):
        with self.lock:
            evs = [e for e in self.events if e["seq"] > seq]
            stats = {"total": len(self.seen), "scans": self.scans,
                     "last_scan": self.last_scan, "seq": self.seq,
                     "error": self.error}
        return evs, stats

    def notes(self, keys):
        return {k: self.narrator.get(k) for k in keys if self.narrator.get(k)}


app = Flask(__name__)
WALL = None


@app.route("/")
def index():
    return Response(PAGE, mimetype="text/html")


@app.route("/events")
def events():
    try:
        seq = int(request.args.get("since", 0))
    except ValueError:
        seq = 0
    evs, stats = WALL.since(seq)
    return jsonify({"events": evs, "stats": stats,
                    "ai": bool(WALL.narrator.enabled)})


@app.route("/notes")
def notes():
    keys = request.args.get("keys", "").split("\x1f")
    return jsonify(WALL.notes([k for k in keys if k]))


PAGE = r"""
<!doctype html><html><head><meta charset="utf-8">
<title>Live job wall</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{
  --bg:#07090c; --panel:#0d1117; --line:#1e2733; --ink:#dfe7f0; --dim:#8494a8;
  --accent:#4ade80; --accent2:#38bdf8; --warn:#fbbf24; --violet:#a78bfa;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font:14px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}
header{position:sticky;top:0;z-index:5;background:rgba(7,9,12,.94);
 border-bottom:1px solid var(--line);padding:12px 18px;
 display:flex;gap:18px;align-items:center;flex-wrap:wrap;backdrop-filter:blur(8px)}
h1{font-size:15px;margin:0;letter-spacing:.06em;text-transform:uppercase;color:var(--accent)}
.pulse{width:8px;height:8px;border-radius:50%;background:var(--accent);
 box-shadow:0 0 0 0 rgba(74,222,128,.7);animation:p 2s infinite}
@keyframes p{70%{box-shadow:0 0 0 9px rgba(74,222,128,0)}100%{box-shadow:0 0 0 0 rgba(74,222,128,0)}}
.stats{display:flex;gap:14px;flex-wrap:wrap;margin-left:auto;color:var(--dim);font-size:12px}
.stats b{color:var(--ink);font-weight:600}
.filters{display:flex;gap:6px;flex-wrap:wrap}
.filters button{background:transparent;border:1px solid var(--line);color:var(--dim);
 padding:3px 10px;border-radius:99px;cursor:pointer;font:inherit;font-size:12px}
.filters button.on{border-color:var(--accent);color:var(--accent)}
main{display:grid;grid-template-columns:1fr 340px;gap:16px;padding:16px 18px;
 align-items:start}
@media(max-width:900px){main{grid-template-columns:1fr}}
#grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--dim);
 border-radius:8px;padding:12px 13px;animation:in .45s ease both;overflow:hidden}
@keyframes in{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.card.flash{border-color:var(--accent);box-shadow:0 0 0 1px rgba(74,222,128,.35)}
.card.posting{border-left-color:var(--dim)}
.card.match{border-left-color:var(--accent2)}
.card.application{border-left-color:var(--violet)}
.card.tracker{border-left-color:var(--warn)}
.co{font-weight:700;font-size:14px}
.ro{color:var(--dim);font-size:12.5px;margin-bottom:8px;word-wrap:break-word}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px}
.tag{font-size:10px;letter-spacing:.09em;text-transform:uppercase;border:1px solid var(--line);
 padding:1px 7px;border-radius:99px;color:var(--dim)}
.match .tag.k{color:var(--accent2);border-color:var(--accent2)}
.application .tag.k{color:var(--violet);border-color:var(--violet)}
.tracker .tag.k{color:var(--warn);border-color:var(--warn)}
.fit{font-size:12px;color:var(--accent)}
.bar{height:3px;background:var(--line);border-radius:2px;overflow:hidden;margin:7px 0}
.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--accent2),var(--accent))}
.ai{margin-top:9px;padding-top:8px;border-top:1px dashed var(--line);
 font-size:12px;color:#b9c6d6}
.ai .who{font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--dim);
 display:block;margin-bottom:3px}
.ai.fb .who{color:#5c6b7d}
a{color:var(--accent2);text-decoration:none;font-size:11.5px}
a:hover{text-decoration:underline}
aside{background:var(--panel);border:1px solid var(--line);border-radius:8px;
 padding:12px 13px;position:sticky;top:66px;max-height:calc(100vh - 84px);
 display:flex;flex-direction:column}
aside h2{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--dim);
 margin:0 0 9px}
#feed{overflow-y:auto;font-size:12px}
#feed div{padding:4px 0;border-bottom:1px solid #131a22;color:var(--dim);
 animation:in .4s ease both}
#feed b{color:var(--ink);font-weight:600}
#feed .t{color:#48566a;font-size:10.5px}
.empty{color:var(--dim);padding:26px;text-align:center;grid-column:1/-1}
</style></head><body>
<header>
  <span class="pulse"></span><h1>Live job wall</h1>
  <a href="http://127.0.0.1:5057/" target="_blank" rel="noopener"
     style="font-size:12px">search past applications &rarr;</a>
  <div class="filters" id="filters"></div>
  <div class="stats">
    <span><b id="s-total">0</b> records</span>
    <span><b id="s-new">0</b> this session</span>
    <span>AI <b id="s-ai">-</b></span>
    <span id="s-scan">-</span>
  </div>
</header>
<main>
  <div id="grid"><div class="empty">waiting for the bots to write&hellip;</div></div>
  <aside><h2>Activity</h2><div id="feed"></div></aside>
</main>
<script>
const KINDS=['posting','match','application','tracker'];
const LABEL={posting:'scraped',match:'scored',application:'tailored',tracker:'tracker'};
let since=0, live=0, cards=new Map(), active=new Set(KINDS), pending=new Set();

const fbox=document.getElementById('filters');
KINDS.forEach(k=>{const b=document.createElement('button');b.textContent=LABEL[k];
  b.className='on';b.onclick=()=>{active.has(k)?(active.delete(k),b.classList.remove('on'))
  :(active.add(k),b.classList.add('on'));applyFilter()};fbox.appendChild(b)});

function applyFilter(){let shown=0;
  cards.forEach(c=>{const on=active.has(c.dataset.kind);c.style.display=on?'':'none';if(on)shown++});
  const e=document.querySelector('.empty'); if(e) e.style.display=shown?'none':'';}

function esc(s){return (s==null?'':String(s)).replace(/[<>&]/g,m=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[m]))}

function build(ev){
  const d=document.createElement('div');
  d.className='card '+ev.kind; d.dataset.kind=ev.kind; d.dataset.key=ev.key;
  let mid='';
  if(ev.kind==='match'){
    mid=`<div class="row"><span class="fit">fit ${ev.fit.toFixed(3)}</span>
      ${ev.skipped?'<span class="tag">dedup checked</span>':''}</div>
      <div class="bar"><i style="width:${Math.round(ev.fit*100)}%"></i></div>`;
  } else if(ev.kind==='application'){
    mid=`<div class="row">${(ev.assets||[]).map(a=>`<span class="tag">${esc(a.split('.').pop())}</span>`).join('')}
      ${ev.has_notes?'<span class="tag">notes</span>':''}
      ${ev.has_outreach?'<span class="tag">outreach</span>':''}</div>`;
    if(ev.tagline) mid+=`<div class="ro">${esc(ev.tagline)}</div>`;
  } else if(ev.kind==='tracker'){
    mid=`<div class="row">${ev.salary?`<span class="tag">${esc(ev.salary)}</span>`:''}
      ${ev.platform?`<span class="tag">${esc(ev.platform)}</span>`:''}</div>
      <div class="ro" style="font-size:11px">${esc(ev.section)}</div>`;
  } else if(ev.location){
    mid=`<div class="row"><span class="tag">${esc(ev.location)}</span></div>`;
  }
  d.innerHTML=`<div class="row"><span class="tag k">${ev.kind==='tracker'?ev.status:LABEL[ev.kind]}</span>
      ${ev.backfill?'':'<span class="tag">new</span>'}</div>
    <div class="co">${esc(ev.company)}</div>
    <div class="ro">${esc(ev.role)}</div>
    ${mid}
    ${ev.url?`<a href="${esc(ev.url)}" target="_blank" rel="noopener">open posting &rarr;</a>`:''}
    <div class="ai fb"><span class="who">reading&hellip;</span><span class="txt"></span></div>`;
  return d;
}

function feedLine(ev){
  const f=document.getElementById('feed');
  const d=document.createElement('div');
  const t=new Date(ev.seen_at*1000).toLocaleTimeString();
  d.innerHTML=`<span class="t">${t}</span> ${LABEL[ev.kind]} &middot; <b>${esc(ev.company)}</b>`;
  f.prepend(d); while(f.children.length>60) f.lastChild.remove();
}

async function tick(){
  try{
    const r=await fetch('/events?since='+since); const j=await r.json();
    const grid=document.getElementById('grid');
    if(j.events.length){
      const e=grid.querySelector('.empty'); if(e) e.remove();
      for(const ev of j.events){
        since=Math.max(since,ev.seq);
        if(ev.removed){
          const c=cards.get(ev.key);
          if(c){c.remove(); cards.delete(ev.key); pending.delete(ev.key);}
          continue;
        }
        if(!ev.backfill){live++; feedLine(ev);}
        const old=cards.get(ev.key);
        const node=build(ev);
        if(old){old.replaceWith(node)} else {grid.prepend(node)}
        cards.set(ev.key,node);
        pending.add(ev.key);
        if(!ev.backfill){node.classList.add('flash');
          setTimeout(()=>node.classList.remove('flash'),2600);}
      }
      applyFilter();
    }
    document.getElementById('s-total').textContent=j.stats.total;
    document.getElementById('s-new').textContent=live;
    document.getElementById('s-ai').textContent=j.ai?'on':'off';
    document.getElementById('s-scan').textContent='scan #'+j.stats.scans+
      (j.stats.error?' ⚠ '+j.stats.error.slice(0,40):'');
  }catch(e){}
  setTimeout(tick,1500);
}

async function pullNotes(){
  if(pending.size){
    const keys=[...pending].slice(0,60);
    try{
      const r=await fetch('/notes?keys='+encodeURIComponent(keys.join('\x1f')));
      const j=await r.json();
      for(const k in j){
        const c=cards.get(k); if(!c) continue;
        const box=c.querySelector('.ai');
        box.className='ai'+(j[k].by==='ai'?'':' fb');
        box.querySelector('.who').textContent=j[k].by==='ai'?'ai read':'derived';
        box.querySelector('.txt').textContent=j[k].text;
        if(j[k].by==='ai') pending.delete(k);
      }
    }catch(e){}
  }
  setTimeout(pullNotes,3000);
}
tick(); pullNotes();
</script></body></html>
"""


def main():
    global WALL
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5058)
    ap.add_argument("--no-ai", action="store_true", help="skip the narrator lane")
    args = ap.parse_args()

    narrator = Narrator(enabled=not args.no_ai)
    WALL = Wall(narrator)
    WALL.scan()                                   # backfill before first paint
    threading.Thread(target=WALL.run, daemon=True).start()
    if narrator.enabled:
        for _ in range(6):                        # six narrators in parallel
            threading.Thread(target=narrator.run, daemon=True).start()

    print(f"live job wall  ->  http://127.0.0.1:{args.port}")
    print(f"watching       ->  {ROOT}")
    print(f"backfilled     ->  {len(WALL.seen)} records")
    app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
