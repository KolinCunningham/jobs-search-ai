"""
Local web UI for query.py -- so the query interface can actually be seen
and used, not just read as CLI output. Runs entirely on localhost, nothing
leaves the machine (same as every other piece of this pipeline).

    .venv/bin/python webui.py
    -> open http://127.0.0.1:5057

Filters are dropdowns populated from the store's REAL distinct values
(query.distinct_values), not a hardcoded guess -- they stay accurate as
more batches get ingested. Search history persists to search_history.json
(capped at 50) so it survives restarting this process.
"""

import html
import json
import re
import threading
import time
from pathlib import Path

from flask import (Flask, request, render_template_string, redirect, url_for,
                   jsonify, send_file, abort, Response)

import lib
import query as q
import graph as g
import store

app = Flask(__name__)

CLAUDE_MD_PATH = Path(__file__).resolve().parent.parent / "CLAUDE.md"

HISTORY_PATH = Path(__file__).resolve().parent / "search_history.json"
HISTORY_CAP = 50

# Third audit round reproduced real corruption here: 10 concurrent requests
# against the old read-modify-write (no lock, non-atomic write_text) turned
# the file into two concatenated JSON arrays, and most of the 10 entries
# were lost. Fixed two ways: _history_lock serializes the read-modify-write
# so concurrent writers don't clobber each other, and writing to a temp
# file + os.replace() means any concurrent READER only ever sees a fully
# old or fully new file, never a half-written one.
_history_lock = threading.Lock()


def load_history():
    if HISTORY_PATH.exists():
        try:
            return json.loads(HISTORY_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return []
    return []


def save_history(entry):
    with _history_lock:
        history = load_history()
        history.insert(0, entry)
        history = history[:HISTORY_CAP]
        tmp_path = HISTORY_PATH.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(history, indent=2))
        tmp_path.replace(HISTORY_PATH)   # atomic on POSIX
        return history


PAGE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Career RAG — Query</title>
<style>
  /* Same tokens as Engram's own dashboard/viz UI
     (crates/engram-http/src/{viz,dashboard}.html in the engram repo) -- read
     directly from its source, not approximated. Engram itself has no light mode, so neither
     does this: one fixed dark palette, not a prefers-color-scheme toggle. */
  :root{
    --bg:#0a0a0f; --raised:#0e0e18; --surface2:#1a1a2e; --ink:#c8c8d0; --soft:#999;
    --faint:#666; --line:#2a2a4a; --teal:#4a9eff; --teal-tint:rgba(74,158,255,.13);
    --amber:#ffb86c; --amber-tint:rgba(255,184,108,.13);
  }
  *{box-sizing:border-box;}
  body{ background:var(--bg); color:var(--ink); font-family:-apple-system,"Segoe UI",sans-serif; margin:0; padding:32px 20px; }
  .shell{ max-width:1100px; margin:0 auto; display:grid; grid-template-columns:minmax(0,1fr) 280px; gap:28px; align-items:start; }
  @media (max-width:820px){ .shell{ grid-template-columns:1fr; } }
  .main{ display:flex; flex-direction:column; gap:24px; min-width:0; }
  h1{ font-size:22px; margin:0; }
  h2{ font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:var(--faint); margin:0 0 12px; }
  .sub{ color:var(--soft); font-size:14px; margin-top:4px; }
  form{ background:var(--raised); border:1px solid var(--line); border-radius:10px; padding:18px; display:flex; flex-direction:column; gap:10px; }
  .row{ display:flex; gap:10px; flex-wrap:wrap; }
  input[type=text], select{ padding:10px 12px; border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--ink); font-size:14px; }
  input[type=text]{ flex:1; min-width:220px; }
  select{ min-width:150px; }
  input[type=number]{ width:70px; padding:10px; border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--ink); }
  button{ padding:10px 18px; border:none; border-radius:6px; background:var(--teal); color:#fff; font-weight:600; cursor:pointer; font-size:14px; }
  button.secondary{ background:transparent; border:1px solid var(--line); color:var(--ink); }
  label{ font-size:11.5px; color:var(--faint); text-transform:uppercase; letter-spacing:.04em; display:block; margin-bottom:4px; }
  .field{ display:flex; flex-direction:column; }
  .results{ display:flex; flex-direction:column; gap:12px; }
  .card{ background:var(--raised); border:1px solid var(--line); border-radius:8px; padding:14px 16px; }
  .card .meta{ font-size:12px; color:var(--faint); font-family:ui-monospace,monospace; margin-bottom:6px; }
  .card .meta a.doclink{ color:var(--soft); text-decoration:none; border-bottom:1px dotted var(--line); }
  .card .meta a.doclink:hover{ color:var(--teal); border-bottom-color:var(--teal); }
  .card .score{ display:inline-block; background:var(--teal-tint); color:var(--teal); border-radius:4px; padding:1px 6px; font-size:11px; margin-right:8px; font-family:ui-monospace,monospace; }
  .card .tags{ display:inline; color:var(--soft); font-size:12.5px; }
  .card p{ margin:6px 0 0; font-size:13.5px; color:var(--ink); line-height:1.5; }
  .empty{ color:var(--faint); font-size:14px; }
  .section-label{ font-size:13px; font-weight:600; color:var(--soft); }
  .sidebar{ background:var(--raised); border:1px solid var(--line); border-radius:10px; padding:16px; max-height:80vh; overflow-y:auto; }
  .hist-item{ display:block; padding:9px 0; border-bottom:1px solid var(--line); text-decoration:none; color:inherit; }
  .hist-item:last-child{ border-bottom:none; }
  .hist-q{ font-size:13px; color:var(--ink); font-weight:500; }
  .hist-meta{ font-size:11px; color:var(--faint); margin-top:2px; font-family:ui-monospace,monospace; }
  .hist-empty{ font-size:13px; color:var(--faint); }
  .topnav{ max-width:1100px; margin:0 auto 20px; display:flex; gap:4px; border-bottom:1px solid var(--line); }
  .navlink{ padding:10px 16px; font-size:13.5px; font-weight:500; color:var(--soft); text-decoration:none; border-bottom:2px solid transparent; margin-bottom:-1px; }
  .navlink.active{ color:var(--ink); border-bottom-color:var(--teal); }
  .navlink:hover{ color:var(--ink); }
  .doc{ max-width:820px; margin:0 auto; display:flex; flex-direction:column; gap:30px; }
  .doc h2{ font-size:19px; color:var(--ink); font-weight:600; margin:0; }
  .doc h3{ font-size:12px; color:var(--faint); text-transform:uppercase; letter-spacing:.05em; margin:0 0 6px; }
  .doc p{ font-size:14.5px; line-height:1.65; color:var(--ink); margin:0; }
  .doc p.soft{ color:var(--soft); }
  .doc section{ display:flex; flex-direction:column; gap:12px; }
  .doc ol, .doc ul{ margin:0; padding-left:20px; display:flex; flex-direction:column; gap:9px; font-size:14.5px; color:var(--ink); line-height:1.6; }
  .doc code{ font-family:ui-monospace,monospace; font-size:0.9em; background:var(--bg); border:1px solid var(--line); border-radius:4px; padding:0.1em 0.4em; }
  .doc pre{ background:var(--raised); border:1px solid var(--line); border-radius:8px; padding:14px 16px; overflow-x:auto; font-family:ui-monospace,monospace; font-size:12.5px; line-height:1.65; margin:0; color:var(--ink); }
  .doc-table-wrap{ border:1px solid var(--line); border-radius:8px; overflow:hidden; overflow-x:auto; }
  .doc table{ width:100%; border-collapse:collapse; font-size:13px; background:var(--raised); }
  .doc th{ text-align:left; font-size:10.5px; text-transform:uppercase; letter-spacing:.04em; color:var(--faint); padding:9px 12px; border-bottom:1px solid var(--line); white-space:nowrap; }
  .doc td{ padding:9px 12px; border-bottom:1px solid var(--line); color:var(--ink); vertical-align:top; }
  .doc td:first-child{ font-family:ui-monospace,monospace; font-size:12px; white-space:nowrap; }
  .doc tr:last-child td{ border-bottom:none; }
  .callout{ background:var(--amber-tint); border:1px solid var(--amber); border-radius:8px; padding:14px 16px; font-size:13.5px; color:var(--ink); line-height:1.55; }
  .callout b{ color:var(--amber); }
</style>
</head>
<body>
<div class="topnav">
  <a href="/" class="navlink active">Search</a>
  <a href="/memory" class="navlink">Memory</a>
  <a href="/how-to" class="navlink">How to use this</a>
  <a href="http://127.0.0.1:5058/" class="navlink" target="_blank" rel="noopener">Live wall &#8599;</a>
  <a href="/claude" class="navlink">Claude</a>
</div>
<div class="shell">
  <div class="main">
    <div>
      <h1>Career RAG — Query</h1>
      <div class="sub">{{ chunk_count }} chunks, {{ folder_count }} application folders, running local + free on bge-small + Chroma. Nothing leaves this machine.</div>
    </div>

    <form method="get" action="/search">
      <span class="section-label">Semantic search</span>
      <div class="row" style="align-items:flex-end;">
        <div class="field" style="flex:1; min-width:220px;">
          <label>Query</label>
          <input type="text" name="q" placeholder='e.g. "AI role in energy sector" or "what did I tell Automic about RAG"' value="{{ q or '' }}" autofocus>
        </div>
        <div class="field">
          <label>Results</label>
          <input type="number" name="n" value="{{ n or 5 }}" min="1" max="20">
        </div>
        <button type="submit">Search</button>
      </div>
      <div class="row">
        <div class="field">
          <label>Country</label>
          <select name="country">
            <option value="">Any</option>
            {% for v in countries %}<option value="{{ v }}" {{ 'selected' if v==country else '' }}>{{ v }}</option>{% endfor %}
          </select>
        </div>
        <div class="field">
          <label>Category</label>
          <select name="category">
            <option value="">Any</option>
            {% for v in categories %}<option value="{{ v }}" {{ 'selected' if v==category else '' }}>{{ v }}</option>{% endfor %}
          </select>
        </div>
        <div class="field">
          <label>Status</label>
          <select name="status">
            <option value="">Any</option>
            {% for v in statuses %}<option value="{{ v }}" {{ 'selected' if v==status else '' }}>{{ v }}</option>{% endfor %}
          </select>
        </div>
        <div class="field">
          <label>Doc type</label>
          <select name="doc_type">
            <option value="">Any</option>
            {% for v in doc_types %}<option value="{{ v }}" {{ 'selected' if v==doc_type else '' }}>{{ v }}</option>{% endfor %}
          </select>
        </div>
      </div>
    </form>

    <form method="get" action="/dedup">
      <span class="section-label">Dedup check — has this company already come up?</span>
      <div class="row">
        <input type="text" name="company" list="companies" placeholder="e.g. Deloitte" value="{{ company or '' }}">
        <datalist id="companies">
          {% for c in all_companies %}<option value="{{ c }}">{% endfor %}
        </datalist>
        <button type="submit" class="secondary">Check</button>
      </div>
    </form>

    <div class="results">
      {% if mode == 'search' %}
        <h2>{{ results|length }} result{{ '' if results|length==1 else 's' }}</h2>
        {% if results %}
          {% for r in results %}
          <div class="card">
            <div class="meta">
              {% if r.meta.get('path') %}
                <a class="doclink" href="/open?path={{ r.meta.get('path')|urlencode }}" target="_blank" rel="noopener">{{ r.meta.get('path') }}</a>
              {% endif %}
              &middot; {{ r.meta.get('doc_type') }}
            </div>
            <span class="score">{{ '%.3f'|format(r.score) }}</span>
            <span class="tags">
              {% for k in ['company','country','category','status'] %}
                {% if r.meta.get(k) %}{{ k }}={{ r.meta.get(k) }} &nbsp;{% endif %}
              {% endfor %}
            </span>
            <p>{{ r.doc[:280] }}{% if r.doc|length > 280 %}…{% endif %}</p>
          </div>
          {% endfor %}
        {% else %}
          <div class="empty">no results</div>
        {% endif %}
      {% elif mode == 'dedup' %}
        <h2>{{ hits|length }} mention{{ '' if hits|length==1 else 's' }} of "{{ company }}"</h2>
        {% if hits %}
          {% for doc, meta, cid in hits %}
          <div class="card">
            <div class="meta">{{ meta.get('doc_type') }} &middot; status={{ meta.get('status') }}</div>
            <p>{{ doc[:280] }}{% if doc|length > 280 %}…{% endif %}</p>
          </div>
          {% endfor %}
        {% else %}
          <div class="empty">No mention found — looks like a new company.</div>
        {% endif %}
      {% endif %}
    </div>
  </div>

  <div class="sidebar">
    <h2>Recent searches</h2>
    {% if history %}
      {% for h in history %}
        {% if h.mode == 'search' %}
        <a class="hist-item" href="/search?q={{ h.q|urlencode }}&n={{ h.n }}&country={{ h.country|urlencode }}&category={{ h.category|urlencode }}&status={{ h.status|urlencode }}&doc_type={{ h.doc_type|urlencode }}">
          <div class="hist-q">{{ h.q }}</div>
          <div class="hist-meta">{{ h.result_count }} results
            {%- if h.country %} &middot; {{ h.country }}{% endif %}
            {%- if h.category %} &middot; {{ h.category }}{% endif %} &middot; {{ h.when }}</div>
        </a>
        {% else %}
        <a class="hist-item" href="/dedup?company={{ h.company|urlencode }}">
          <div class="hist-q">dedup: {{ h.company }}</div>
          <div class="hist-meta">{{ h.result_count }} mentions &middot; {{ h.when }}</div>
        </a>
        {% endif %}
      {% endfor %}
    {% else %}
      <div class="hist-empty">Nothing searched yet this session.</div>
    {% endif %}
    {% if mode in ('search', 'dedup') and highlight_ids %}
      <a class="hist-item" href="/memory?highlight={{ highlight_ids|join(',') }}" style="border-top:1px solid var(--line); margin-top:6px; padding-top:12px; color:var(--teal); font-weight:500;">See these {{ highlight_ids|length }} in the Memory graph &rarr;</a>
    {% endif %}
  </div>
</div>
</body>
</html>
"""


HOWTO_STYLE = PAGE[PAGE.index("<style>"):PAGE.index("</style>") + len("</style>")]

HOWTO_PAGE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Career RAG — How to use this</title>
""" + HOWTO_STYLE + """
</head>
<body>
<div class="topnav">
  <a href="/" class="navlink">Search</a>
  <a href="/memory" class="navlink">Memory</a>
  <a href="/how-to" class="navlink active">How to use this</a>
  <a href="/claude" class="navlink">Claude</a>
</div>
<div class="doc">

  <div>
    <h1 style="margin-bottom:6px;">How to use this</h1>
    <p class="soft">A local, free, offline index of every job application, cover letter, and outreach message in this folder — plus a way to check a brand-new posting against it before you apply. Two locations, worth keeping straight: the <code>.py</code> scripts below all run from inside <code>career_index/</code>; <b>Career RAG.command</b> and <b>career-rag-guide.html</b> live one level up, in the main project folder (<code>jobs ai search program/</code>) — that's where you double-click things from Finder.</p>
  </div>

  <section>
    <h2>Quick command reference</h2>
    <div class="doc-table-wrap">
      <table>
        <tr><th>Command</th><th>What it does</th></tr>
        <tr><td>Career RAG.command</td><td>Double-click — it's one level up from here, in the main project folder, not inside <code>career_index/</code>. Opens this page as its own window; close the window to stop the server and auto-run maintenance.</td></tr>
        <tr><td>webui.py</td><td>Starts this web UI by hand: <code>.venv/bin/python webui.py</code>, then open http://127.0.0.1:5057</td></tr>
        <tr><td>query.py "text"</td><td>Same search as the Search tab, from the terminal. Add <code>--filter country=Australia</code> (repeatable) or <code>--n 10</code>.</td></tr>
        <tr><td>query.py --company "X"</td><td>Same as the Dedup box — has this company come up before, and what happened.</td></tr>
        <tr><td>rank_new.py postings.json</td><td>Score new job postings against your resume, skip anything already tracked, and permanently record each new one. See "Finding new jobs" below.</td></tr>
        <tr><td>outreach_gaps.py</td><td>Who's been sent a LinkedIn message and who hasn't — writes <code>outreach_gaps.md</code>.</td></tr>
        <tr><td>ingest.py</td><td>Re-index after adding or editing application files. Safe to re-run any time — updates in place, never duplicates.</td></tr>
        <tr><td>maintenance.py</td><td>Runs ingest.py + outreach_gaps.py together. This is what Career RAG.command runs automatically on close.</td></tr>
      </table>
    </div>
  </section>

  <section>
    <h2>Finding new jobs — and recording them here</h2>
    <p class="soft">This is the actual "search for a job, then record the information here" loop: checks a posting you just found against everything you've already applied to or been told about, then <b>permanently adds it as a real memory</b> — not a throwaway report you read once and lose. From then on it's searchable, dedup-checkable, and shows up as its own node in the Memory graph, right alongside your past applications.</p>
    <ol>
      <li>Scrape the posting's text. If you're working with Claude Code, ask it to pull the JD with vibatchium: <em>"use vb explore on this job URL and save the text."</em></li>
      <li>Put it in a JSON file, one entry per posting:
        <pre>[
  {"company": "Example Co", "role": "Forward Deployed Engineer",
   "url": "https://...", "text": "the full job description..."}
]</pre>
      </li>
      <li>Run it: <code>.venv/bin/python rank_new.py new_postings.json</code></li>
      <li>Read <code>new_matches.md</code> (written one level up from <code>career_index/</code>, in the main project folder) — ranked highest-fit first, with the 3 most similar past NOTES.md entries as precedent for each. Anything already in your tracker gets skipped automatically, with a note on why.</li>
    </ol>
    <div class="callout"><b>One real caveat:</b> if <b>webui.py</b> is already running when you do this, restart it (or just close and reopen <b>Career RAG.command</b>) to see the new posting in Search or the Memory graph — a live server's connection doesn't reliably pick up someone else's write while it's running, confirmed while building this. A fresh terminal command (<code>query.py</code>, another <code>rank_new.py</code> run) always sees it immediately, no restart needed — it's only the already-open browser session that needs a nudge.</div>
    <div class="callout" style="margin-top:10px;"><b>Ask Claude to do this for you:</b> "Scrape this job posting and run it through rank_new.py, then tell me if it's worth applying to and what angle I've used for similar roles before."</div>
  </section>

  <section>
    <h2>Finding old / past applications</h2>
    <p class="soft">Three tools, three different questions:</p>
    <ul>
      <li><b>"What did I already say?"</b> — use <b>Search</b>. Try things like <em>"what did I tell Automic about the RAG/MCP question"</em> or <em>"AI role in finance sector"</em> with the Country/Category filters. Each result shows the source file and a similarity score.</li>
      <li><b>"Have I already dealt with this company?"</b> — use <b>Dedup check</b>. Type the company name, it searches the tracker directly (not semantically) and shows every real mention — including ones buried in prose notes, not just the formal tracker rows.</li>
      <li><b>"What does the whole picture look like?"</b> — use <b>Memory</b>. Every indexed chunk as a node, grouped by sector of work (Finance, Energy, Consulting…), connected by real semantic similarity. A search result page links straight to its matches lit up in the graph.</li>
    </ul>
    <p class="soft">Search and Dedup are also available from the terminal — see the command table above — and both remember what you searched, in the sidebar on the Search tab.</p>
  </section>

  <section>
    <h2>Using this with Claude</h2>
    <p class="soft">If you're in a Claude Code session (this one or a future one) and want it to use this index instead of re-reading 90 folders by hand, point it at this file structure. A few ways to phrase it:</p>
    <ul>
      <li><em>"Check career_index/query.py — have I already applied to [Company]?"</em></li>
      <li><em>"Search the career index for how past cover letters handled a missing AWS Bedrock requirement."</em></li>
      <li><em>"Run outreach_gaps.py and tell me who's ready to message on LinkedIn today."</em></li>
      <li><em>"I found this job posting — scrape it and run it through rank_new.py."</em></li>
    </ul>
    <p class="soft">Claude can run any of these scripts directly with <code>.venv/bin/python</code> from <code>career_index/</code> — every file has a docstring at the top explaining what it does and why it's built the way it is. The full build guide (every design decision, every bug found and fixed across five audit rounds) is <code>career-rag-guide.html</code>, one level up from <code>career_index/</code>, in the main project folder.</p>
  </section>

  <section>
    <h2>Keeping it current</h2>
    <p class="soft">Closing the <b>Career RAG.command</b> window already does this automatically. To do it by hand: <code>.venv/bin/python maintenance.py</code> — safe to run any time, as often as you like.</p>
  </section>

</div>
</body>
</html>
"""


MEMORY_PAGE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Career RAG — Memory</title>
""" + HOWTO_STYLE + """
<style>
  body{ padding:0; }
  .mem-wrap{ display:flex; flex-direction:column; height:100vh; }
  .mem-topnav{ max-width:none; margin:0; padding:0 20px; }
  .mem-head{ display:flex; justify-content:space-between; align-items:flex-start; padding:16px 20px; gap:16px; flex-wrap:wrap; border-bottom:1px solid var(--line); }
  #graph-legend{ display:flex; flex-wrap:wrap; gap:8px 14px; font-size:11.5px; color:var(--soft); }
  #graph-legend .dot{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:5px; vertical-align:middle; }
  /* min-height:0 is load-bearing, not decoration: an SVG inside a flex
     column defaults to min-height:auto, which lets its own (unpredictable,
     pre-layout) intrinsic size override flex:1 -- confirmed live, the SVG's
     rendered box (294px tall) didn't match its own viewBox (469px), which
     squeezed every node from the force simulation into a small top-left
     region despite the simulation's own coordinates correctly spanning the
     full canvas. Classic flexbox+replaced-element bug, not a d3 bug. */
  #graph-svg{ flex:1; min-height:0; width:100%; display:block; background:var(--bg); cursor:grab; }
  #graph-svg:active{ cursor:grabbing; }
  .graph-tooltip{ position:fixed; pointer-events:none; background:var(--raised); border:1px solid var(--line); border-radius:6px; padding:9px 11px; font-size:12px; max-width:280px; box-shadow:0 6px 20px rgba(0,0,0,.4); z-index:50; display:none; }
  .graph-tooltip .t-title{ font-weight:600; color:var(--ink); margin-bottom:2px; }
  .graph-tooltip .t-meta{ color:var(--faint); font-family:ui-monospace,monospace; font-size:10.5px; }
  .graph-tooltip .t-preview{ color:var(--soft); margin-top:5px; line-height:1.45; }
  #graph-status{ font-size:11.5px; color:var(--faint); }
  #graph-panel{ position:fixed; top:0; right:0; width:380px; height:100vh; background:var(--raised); border-left:1px solid var(--line); padding:22px; overflow-y:auto; display:none; z-index:40; }
  #graph-panel h3{ font-size:14px; color:var(--ink); margin:0 0 8px; padding-right:22px; }
  #graph-panel .meta{ color:var(--faint); font-size:12px; margin-bottom:14px; line-height:1.8; font-family:ui-monospace,monospace; }
  #graph-panel .full{ color:var(--ink); font-size:13.5px; line-height:1.6; white-space:pre-wrap; word-break:break-word; }
  #graph-panel-close{ position:absolute; top:14px; right:16px; background:none; border:none; color:var(--faint); font-size:18px; cursor:pointer; }
  #graph-panel-close:hover{ color:var(--ink); }
</style>
</head>
<body>
<div class="mem-wrap">
<div class="topnav mem-topnav">
  <a href="/" class="navlink">Search</a>
  <a href="/memory" class="navlink active">Memory</a>
  <a href="/how-to" class="navlink">How to use this</a>
  <a href="http://127.0.0.1:5058/" class="navlink" target="_blank" rel="noopener">Live wall &#8599;</a>
  <a href="/claude" class="navlink">Claude</a>
</div>
<div class="mem-head">
  <div>
    <h1 style="font-size:18px;">Memory graph</h1>
    <div class="sub" style="margin-top:2px;">Every indexed chunk is a node. Sectors of work (Finance, Energy, Consulting…) are the brain's regions. Edges are real cosine-similarity nearest neighbors -- the same vectors search uses, not a decorative layout.</div>
  </div>
  <div style="display:flex; flex-direction:column; align-items:flex-end; gap:6px;">
    <div id="graph-legend"></div>
    <div id="graph-status">Loading graph…</div>
  </div>
</div>
<svg id="graph-svg"></svg>
</div>
<div class="graph-tooltip" id="graph-tooltip"></div>
<div id="graph-panel"><button id="graph-panel-close">&times;</button><div id="graph-panel-body"></div></div>

<script src="/static/d3.min.js"></script>
<script>
const HIGHLIGHT_IDS = new Set({{ highlight_ids|default([])|tojson }});

// Region palette -- one hue per sector of work, the graph's "brain regions."
// Same accent family as the rest of the app (Engram's own tokens), assigned
// per real category found in this corpus -- not a fixed enum, since which
// categories exist depends on what meta.py has actually detected.
const PALETTE = ["#c24976", "#028a9b", "#b36300", "#5671d8", "#718506", "#ad51a7", "#078e7d", "#c74c3d", "#0482c0", "#957602", "#8a5fc9", "#079343"];
const REGION_COLORS = {};
function regionColor(cat){
  if (cat === "Uncategorized" || !cat) return "#555566";
  if (!(cat in REGION_COLORS)) REGION_COLORS[cat] = PALETTE[Object.keys(REGION_COLORS).length % PALETTE.length];
  return REGION_COLORS[cat];
}

function escapeHtml(s){
  return (s || "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

const svgEl = document.getElementById("graph-svg");
const tooltip = document.getElementById("graph-tooltip");
const statusEl = document.getElementById("graph-status");
const panel = document.getElementById("graph-panel");
const panelBody = document.getElementById("graph-panel-body");
document.getElementById("graph-panel-close").addEventListener("click", () => panel.style.display = "none");

function resizeSvg(){
  // Measure the SVG's OWN box, not its parent's. Confirmed live: this used
  // to read .mem-wrap's full height (469px, the whole flex column including
  // the nav bar and header above the graph), while the SVG element itself
  // only actually occupies the remaining flex space (294px, correctly --
  // 469 minus the 37px nav and 138px header really used above it). Setting
  // viewBox to the taller, wrong number meant every node's simulated
  // position was computed against a coordinate space bigger than the box
  // that actually renders it, so the real graph only ever occupied the top
  // ~63% of the visible canvas -- looked like everything was clustered in
  // one corner, but the simulation itself was correct the whole time.
  const rect = svgEl.getBoundingClientRect();
  svgEl.setAttribute("viewBox", [0, 0, rect.width, rect.height].join(" "));
  return { w: rect.width, h: rect.height };
}

fetch("/api/graph").then(r => r.json()).then(data => {
  const { nodes, edges, categories } = data;
  if (!nodes.length) { statusEl.textContent = "No chunks indexed yet -- run ingest.py."; return; }
  categories.filter(c => c !== "Uncategorized").forEach(regionColor); // stable color assignment order

  // Legend built BEFORE measuring the canvas, not after. Confirmed live
  // (independent review): building it after meant the header could still
  // grow (legend chips wrapping to a second line) AFTER resizeSvg() had
  // already measured the SVG's box, leaving the viewBox off by the exact
  // height the legend added -- a smaller-magnitude recurrence of the same
  // wrong-size-at-measurement-time bug class, just from ordering instead of
  // a wrong element.
  const legend = document.getElementById("graph-legend");
  categories.forEach(c => {
    const span = document.createElement("span");
    span.innerHTML = `<span class="dot" style="background:${regionColor(c)}"></span>${escapeHtml(c)}`;
    legend.appendChild(span);
  });

  let { w, h } = resizeSvg();
  const svg = d3.select(svgEl);
  const root = svg.append("g");
  svg.call(d3.zoom().scaleExtent([0.1, 8]).on("zoom", ev => root.attr("transform", ev.transform)));

  // One anchor point per sector, arranged on a circle -- the pull toward
  // "home" that keeps each region a visible cluster instead of one blob.
  // Same conceptual role as Engram's region grouping, done via a light
  // forceX/forceY pull rather than a fixed 3D octree -- d3-force's charge
  // force already uses a Barnes-Hut (quadtree) approximation internally
  // for repulsion, the same family of algorithm, in 2D rather than 3D.
  //
  // `let`, not `const`: a real window resize needs these anchors (and the
  // w/h used by the containment clamp below) to move too. Independent
  // review found the resize listener only updated the SVG's viewBox
  // attribute and left every one of these frozen at page-load values --
  // the coordinate space and the simulation's actual geometry would go out
  // of sync on any resize, reproducing the original clustering bug through
  // a different door. forceX/forceY read `catCenters` by closure on every
  // tick, so mutating its contents in place (not replacing the object) is
  // enough to move the simulation's targets without re-registering forces.
  let catCenters = { _default: { x: w / 2, y: h / 2 } };
  function layoutCenters(width, height) {
    categories.forEach((c, i) => {
      const angle = (i / categories.length) * 2 * Math.PI - Math.PI / 2;
      const r = Math.min(width, height) * 0.36;
      catCenters[c] = { x: width / 2 + r * Math.cos(angle), y: height / 2 + r * Math.sin(angle) };
    });
    catCenters._default = { x: width / 2, y: height / 2 };
  }
  layoutCenters(w, h);

  const highlighting = HIGHLIGHT_IDS.size > 0;
  const nodeById = new Map(nodes.map(n => [n.id, n]));

  // Seed starting positions instead of letting d3 default every node to its
  // built-in spiral-near-the-origin -- confirmed live (screenshot vs. a
  // delayed eval of the same page): the simulation genuinely does converge
  // to fill the canvas, but for the first second or so -- exactly the
  // window a page-load screenshot lands in -- every node starts clustered
  // in one corner and animates outward. Placing each node at its sector's
  // anchor point plus jitter means the very first rendered frame already
  // looks like the finished layout, not just the eventually-settled one.
  nodes.forEach(d => {
    const c = catCenters[d.category] || catCenters._default;
    d.x = c.x + (Math.random() - 0.5) * 40;
    d.y = c.y + (Math.random() - 0.5) * 40;
  });

  const simulation = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(edges).id(d => d.id).distance(30).strength(0.25))
    .force("charge", d3.forceManyBody().strength(-16))
    .force("collide", d3.forceCollide(5))
    .force("x", d3.forceX(d => (catCenters[d.category] || catCenters._default).x).strength(0.05))
    .force("y", d3.forceY(d => (catCenters[d.category] || catCenters._default).y).strength(0.05));

  const link = root.append("g").attr("stroke", "#2a2a4a").attr("stroke-opacity", 0.5)
    .selectAll("line").data(edges).join("line")
    .attr("stroke-width", d => Math.max(0.5, d.weight * 1.6));

  function dragBehavior(sim) {
    function started(ev, d) { if (!ev.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; }
    function dragged(ev, d) { d.fx = ev.x; d.fy = ev.y; }
    function ended(ev, d) { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }
    return d3.drag().on("start", started).on("drag", dragged).on("end", ended);
  }

  function nodeDetailHTML(d){
    const nLinks = edges.filter(e => (e.source.id || e.source) === d.id || (e.target.id || e.target) === d.id);
    const rows = nLinks.slice().sort((a,b) => b.weight - a.weight).slice(0, 20).map(e => {
      const otherId = (e.source.id || e.source) === d.id ? (e.target.id || e.target) : (e.source.id || e.source);
      const other = nodeById.get(otherId);
      return `&nbsp;&nbsp;${e.weight.toFixed(3)} &rarr; ${escapeHtml(other ? (other.label || other.path || "?") : "?")}`;
    }).join("<br>");
    return `<h3>${escapeHtml(d.label || "(untitled)")}</h3>` +
      `<div class="meta"><b>doc_type:</b> ${escapeHtml(d.doc_type)}<br>` +
      `<b>sector:</b> ${escapeHtml(d.category)}<br>` +
      (d.company ? `<b>company:</b> ${escapeHtml(d.company)}<br>` : "") +
      `<b>path:</b> ${escapeHtml(d.path || "")}<br>` +
      `<b>connections:</b> ${nLinks.length}<br>` +
      (rows ? `<br><b>strongest links:</b><br>${rows}` : "") +
      `</div><div class="full">${escapeHtml(d.preview)}</div>`;
  }

  const node = root.append("g").selectAll("circle").data(nodes).join("circle")
    .attr("r", d => (highlighting && HIGHLIGHT_IDS.has(d.id)) ? 7 : 3.5)
    .attr("fill", d => regionColor(d.category))
    .attr("opacity", d => !highlighting ? 0.85 : (HIGHLIGHT_IDS.has(d.id) ? 1 : 0.1))
    .attr("stroke", d => (highlighting && HIGHLIGHT_IDS.has(d.id)) ? "#fff" : "none")
    .attr("stroke-width", 1.5)
    .style("cursor", "pointer")
    .call(dragBehavior(simulation))
    .on("mouseenter", (ev, d) => {
      tooltip.innerHTML = `<div class="t-title">${escapeHtml(d.label || "(untitled)")}</div>
        <div class="t-meta">${escapeHtml(d.doc_type)} &middot; ${escapeHtml(d.category)}</div>
        <div class="t-preview">${escapeHtml(d.preview)}</div>`;
      tooltip.style.display = "block";
    })
    .on("mousemove", ev => { tooltip.style.left = (ev.clientX + 14) + "px"; tooltip.style.top = (ev.clientY + 14) + "px"; })
    .on("mouseleave", () => { tooltip.style.display = "none"; })
    .on("click", (ev, d) => { panelBody.innerHTML = nodeDetailHTML(d); panel.style.display = "block"; });

  if (highlighting) link.attr("stroke-opacity", d =>
    (HIGHLIGHT_IDS.has(d.source.id || d.source) || HIGHLIGHT_IDS.has(d.target.id || d.target)) ? 0.75 : 0.04);

  // Containment, not just anchoring: forceX/forceY (strength 0.05) pull
  // nodes toward their sector's home point, but nothing STOPS the charge
  // force (549 nodes repelling each other) from pushing outliers well past
  // the canvas edge -- confirmed live, node y-coordinates ranged from -198
  // to +481 against a ~300px-tall box before this. A weak directional pull
  // doesn't cap displacement; an explicit clamp on every tick does.
  const MARGIN = 8;
  simulation.on("tick", () => {
    nodes.forEach(d => {
      d.x = Math.max(MARGIN, Math.min(w - MARGIN, d.x));
      d.y = Math.max(MARGIN, Math.min(h - MARGIN, d.y));
    });
    link.attr("x1", d => d.source.x).attr("y1", d => d.source.y).attr("x2", d => d.target.x).attr("y2", d => d.target.y);
    node.attr("cx", d => d.x).attr("cy", d => d.y);
  });

  // Count of nodes actually present in this graph that match a highlight
  // id, not the raw size of HIGHLIGHT_IDS -- an id from a stale/edited URL
  // that doesn't correspond to any real node (found in review: e.g.
  // ?highlight=nonexistent_id) would otherwise report "1 lit up" while
  // visibly nothing is highlighted.
  const matchedCount = highlighting ? nodes.filter(d => HIGHLIGHT_IDS.has(d.id)).length : 0;
  statusEl.innerHTML = `${nodes.length} chunks &middot; ${edges.length} connections &middot; ${categories.length} sectors`
    + (highlighting ? ` &middot; ${matchedCount} lit up` : "") + " &middot; drag / scroll / click a node";

  window.addEventListener("resize", () => {
    const resized = resizeSvg();
    w = resized.w; h = resized.h;
    layoutCenters(w, h);
    simulation.alpha(0.3).restart();
  });
});
</script>
</body>
</html>
"""


CLAUDE_PAGE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Career RAG — Claude</title>
""" + HOWTO_STYLE + """
<style>
  .term-wrap{ max-width:820px; margin:0 auto; display:flex; flex-direction:column; gap:16px; }
  .term-window{ border:1px solid var(--line); border-radius:10px; background:#050508; overflow:hidden; }
  .term-titlebar{ display:flex; align-items:center; gap:8px; padding:10px 14px; background:var(--raised); border-bottom:1px solid var(--line); }
  .term-dot{ width:10px; height:10px; border-radius:50%; }
  .term-path{ margin-left:8px; font-family:ui-monospace,monospace; font-size:12px; color:var(--faint); }
  .term-body{ padding:18px 20px; font-family:ui-monospace,monospace; font-size:12.5px; line-height:1.6; color:#c8d6c8; white-space:pre-wrap; word-break:break-word; max-height:65vh; overflow-y:auto; }
</style>
</head>
<body>
<div class="topnav">
  <a href="/" class="navlink">Search</a>
  <a href="/memory" class="navlink">Memory</a>
  <a href="/how-to" class="navlink">How to use this</a>
  <a href="http://127.0.0.1:5058/" class="navlink" target="_blank" rel="noopener">Live wall &#8599;</a>
  <a href="/claude" class="navlink active">Claude</a>
</div>
<div class="term-wrap">
  <div>
    <h1 style="margin-bottom:6px; font-size:22px;">Claude</h1>
    <p class="soft" style="font-size:14.5px; line-height:1.65;">This is <code>CLAUDE.md</code>, sitting in the main project folder. Claude Code reads it automatically the moment you launch <code>claude</code> from there — it already knows this is the Career RAG pipeline, what every script does, and the standing rules, before you type a word.</p>
  </div>
  <div class="term-window">
    <div class="term-titlebar">
      <span class="term-dot" style="background:#ff5f56;"></span>
      <span class="term-dot" style="background:#ffbd2e;"></span>
      <span class="term-dot" style="background:#27c93f;"></span>
      <span class="term-path">~/Desktop/Personal/jobs ai search program — CLAUDE.md</span>
    </div>
    <div class="term-body">{{ content }}</div>
  </div>
  <div class="callout"><b>To use it:</b> open Terminal, <code>cd ~/Desktop/Personal/"jobs ai search program"</code>, then run <code>claude</code>. It loads this file on its own — no need to paste anything or explain what the project is.</div>
</div>
</body>
</html>
"""


def _filter_options():
    return {
        "countries": q.distinct_values("country"),
        "categories": q.distinct_values("category"),
        "statuses": q.distinct_values("status"),
        "doc_types": q.distinct_values("doc_type"),
        "all_companies": q.distinct_values("company"),
    }


def _stats():
    # Computed live, not hardcoded -- a stale count is exactly the kind of
    # fabricated-looking data this project's own standing rules forbid.
    chunk_count = store.get_collection().count()
    apps_dir = lib.ROOT / "applications"
    folder_count = sum(1 for p in apps_dir.iterdir() if p.is_dir())
    return {"chunk_count": chunk_count, "folder_count": folder_count}


@app.route("/")
def home():
    return render_template_string(PAGE, mode=None, history=load_history(), highlight_ids=[], **_filter_options(), **_stats())


@app.route("/how-to")
def how_to():
    return render_template_string(HOWTO_PAGE)


@app.route("/memory")
def memory():
    raw = request.args.get("highlight", "")
    highlight_ids = [x for x in raw.split(",") if x] if raw else []
    return render_template_string(MEMORY_PAGE, highlight_ids=highlight_ids)


@app.route("/claude")
def claude_page():
    if CLAUDE_MD_PATH.exists():
        content = CLAUDE_MD_PATH.read_text()
    else:
        content = f"CLAUDE.md not found at {CLAUDE_MD_PATH} -- nothing to show yet."
    return render_template_string(CLAUDE_PAGE, content=content)


def _refused(rel, why):
    """Say why a document will not be opened, rather than a bare 403.

    The guard is the point, but a silent 403 looks like a broken link.
    """
    body = (
        "<!doctype html><meta charset='utf-8'>"
        "<title>Not shown</title>"
        "<body style=\"background:#0d1117;color:#c9d1d9;"
        "font:14px/1.6 ui-monospace,monospace;padding:40px;max-width:640px\">"
        f"<h2 style='color:#e6a23c;margin:0 0 12px'>Not shown</h2>"
        f"<p style='margin:0 0 10px'><code>{html.escape(rel)}</code></p>"
        f"<p style='margin:0 0 10px'>This file is not served because {html.escape(why)}.</p>"
        "<p style='color:#8b949e;margin:0'>Open it directly on disk if you need it. "
        "The pipeline refuses to render credentials or anything mentioning a "
        "password, on purpose.</p></body>"
    )
    return Response(body, status=403, mimetype="text/html; charset=utf-8")


@app.route("/open")
def open_document():
    """Serve one indexed document so a search result can be clicked through.

    A `file://` link cannot work here: the browser refuses to follow one from
    an http:// page, silently, so the path has to come back through this
    server instead.

    This reads arbitrary paths off disk, so it is deliberately narrow:
    the resolved path must sit inside the project root (which stops
    ../../ traversal and symlink escapes, since resolve() follows links),
    the credential files are refused by name using the same pattern lib.py
    excludes them with, and any text document mentioning a password is
    refused rather than rendered.
    """
    rel = (request.args.get("path") or "").strip()
    if not rel:
        abort(404)
    root = lib.ROOT.resolve()
    try:
        target = (root / rel).resolve()
    except (OSError, ValueError):
        abort(404)
    if not target.is_relative_to(root) or not target.is_file():
        abort(404)
    if lib._CREDENTIALS_RE.match(target.name):
        return _refused(rel, "it is a credentials file")

    suffix = target.suffix.lower()
    if suffix in (".md", ".json", ".txt", ".csv"):
        try:
            text = target.read_text(errors="replace")
        except OSError:
            abort(404)
        if re.search(r"\bpassword\b", text, re.IGNORECASE):
            return _refused(rel, "it contains the word \u201cpassword\u201d")
        return Response(text, mimetype="text/plain; charset=utf-8")
    if suffix in (".html", ".htm", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg"):
        return send_file(str(target))
    return _refused(rel, f"{suffix or 'this file type'} is not a document type "
                         "this viewer renders")


@app.route("/api/graph")
def api_graph():
    # Real cosine-similarity nearest-neighbor edges (graph.py), computed
    # fresh -- 547 nodes is cheap enough (~10ms with numpy) that caching
    # isn't worth the staleness risk the fourth audit round already found
    # once in store.py's client cache.
    return jsonify(g.build())


@app.route("/search", methods=["GET"])
def do_search():
    text = request.args.get("q", "").strip()
    # Unvalidated before -- ?n=abc raised ValueError, ?n=0/-1 raised a
    # Chroma TypeError ("cannot be negative, or zero"), both 500ing.
    # HTML's <input min=1 max=20> only guards the normal form; a hand-edited
    # URL or an old saved history link bypasses it entirely. Clamp instead.
    try:
        n = int(request.args.get("n") or 5)
    except ValueError:
        n = 5
    n = max(1, min(20, n))
    country = request.args.get("country", "").strip()
    category = request.args.get("category", "").strip()
    status = request.args.get("status", "").strip()
    doc_type = request.args.get("doc_type", "").strip()

    if not text:
        return redirect(url_for("home"))

    where = {k: v for k, v in
             {"country": country, "category": category, "status": status, "doc_type": doc_type}.items() if v}
    results = q.search(text, n=n, where=where or None)

    save_history({
        "mode": "search", "q": text, "n": n, "country": country, "category": category,
        "status": status, "doc_type": doc_type, "result_count": len(results),
        "when": time.strftime("%H:%M:%S"),
    })

    return render_template_string(PAGE, mode="search", results=results, history=load_history(),
                                   q=text, n=n, country=country, category=category,
                                   status=status, doc_type=doc_type,
                                   highlight_ids=[r["id"] for r in results], **_filter_options(), **_stats())


@app.route("/dedup", methods=["GET"])
def do_dedup():
    company = request.args.get("company", "").strip()
    if not company:
        return redirect(url_for("home"))

    hits = q.dedup_check(company)

    save_history({
        "mode": "dedup", "company": company, "result_count": len(hits),
        "when": time.strftime("%H:%M:%S"),
    })

    return render_template_string(PAGE, mode="dedup", hits=hits, history=load_history(),
                                   company=company, highlight_ids=[h[2] for h in hits], **_filter_options(), **_stats())


if __name__ == "__main__":
    print("Career RAG query UI -> http://127.0.0.1:5057")
    # threaded=False, explicit: single local user, no benefit to concurrency,
    # and it closes off the whole class of races the third audit round found
    # (concurrent requests racing the Chroma client, corrupting history.json)
    # at the server level, on top of the fixes in store.py and save_history().
    app.run(port=5057, debug=False, threaded=False)
