"""
Step 5 -- metadata schema.

Chunking (steps 1-2) decides how a file's TEXT splits into retrieval units.
This is the other half: what gets ATTACHED to each chunk so a query can
filter/rank, not just semantically match. Every field here answers a
question the semantic search alone can't: "only Applied roles", "only
Sydney", "only energy sector", "roles above $200K".

Two of the fields were wanted early on -- industry/category and country
-- turned out not to exist anywhere in the corpus as structured data. Built
here as best-effort extractors instead of assumed. A third -- how long a
posting had been open -- turned out not to be buildable at all: nothing in
this corpus ever recorded the original posting date, only the date each
application was tailored. Documented as a real gap below, not faked.

Field-level provenance (this matters -- these are not equally reliable):
  company, role       -- from job.json when the folder has one (53/66
                          folders); folder-name fallback otherwise
  platform, salary,    -- joined from the matching _tracker.md row by
  status, sector,         normalized company-name match. Real coverage:
  hiring_contact          54/66 folders (82%) -- the rest were tracked in
                          the batch-specific _report_*.md files instead of
                          _tracker.md and aren't joined (known gap, not a
                          matching bug -- see report() below)
  country              -- regex city/keyword scan over NOTES.md prose.
                          Best-effort: only as good as whether the location
                          was ever mentioned in that folder's notes
  category              -- regex keyword scan over NOTES.md + role text
                          against a small sector wordlist. Coarse on
                          purpose -- flagged in the guide as a candidate
                          for hand-correction later, not treated as ground
                          truth
  contact_name          -- first segment of the tracker row's "Hiring
                          contact" field, split on " - "
  date_posted           -- NOT AVAILABLE. job.json's "date" field is when
                          the application was tailored, not when the job
                          was posted. That data was never captured. A
                          derived "days_open" field is not buildable from
                          this corpus, retroactively, at all.
"""

import json
import re

import lib

FOLDER_COMPANY_CACHE = {}   # folder name -> company string or None, filled by build_folder_index()

_COUNTRY_KEYWORDS = [
    (r"\b(sydney|melbourne|brisbane|perth|adelaide|canberra|newcastle|hobart|nsw|vic|qld|wa\b)", "Australia"),
    (r"\b(los angeles|\bla\b|san francisco|new york|nyc|seattle|austin|chicago|boston|usa|united states)", "United States"),
    (r"\blondon\b|\buk\b|united kingdom", "United Kingdom"),
    (r"\bauckland\b|new zealand", "New Zealand"),
    (r"\bsingapore\b", "Singapore"),
]

# "visa" and "aemo|aer|arena" were pulled after the second audit round: both
# are the original user's own PAST-employer/background references, reused near-verbatim
# in the "real fit / real gap" framing paragraph across dozens of unrelated
# NOTES.md files -- not signal about the TARGET company's sector. Scanning
# them mislabeled Deloitte and WWT as Energy, Insight/FutureSecureAI as
# Finance. Keeping only keywords specific enough that a false hit would mean
# the target role is *actually* in that sector, not that the applicant once was.
_CATEGORY_KEYWORDS = [
    (r"\bdeloitte\b|\bbcg\b|\bpwc\b|\bkpmg\b|\bey\b|consultancy|consulting firm", "Consulting"),
    (r"renewable|solar|battery storage|distributed energy|\bgrid\b", "Energy"),
    (r"\bbank\b|fintech|payments|\binsurer\b|\binsurance\b|asset management|hedge fund|macquarie", "Finance"),
    (r"clinical trial|biotech|\bpharma\b|healthcare provider|\bmrff\b", "Healthcare / Biotech"),
]

# Direct company -> category, checked before the keyword scan below and
# after the tracker's own Sector column. Added because "Uncategorized" was
# swallowing genuine, real industries the keyword scan has no way to catch
# on its own (an example: Georgiou
# Group is a real Australian construction contractor, not an AI company;
# scanning its NOTES.md prose for sector words would never find that,
# since the JD is written entirely in AI-enablement language). Hand-
# verified against real public knowledge of each company, matched on the
# folder's already-resolved company name (not free text), so this can't
# reproduce the earlier self-referential keyword-scan bug -- there's no
# text to misfire against, just an exact lookup.
_COMPANY_CATEGORY_OVERRIDES = {
    "georgiou group": "Construction",
    "ares management": "Finance",
    "automic group": "Finance",
    "colonial first state": "Finance",
    "creditorwatch": "Finance",
    "choice ventures": "Finance",
    "private family office": "Finance",
    "deloitte": "Consulting",
    "bcg x": "Consulting",
    "blackbook ai": "Consulting",
    "practiv": "Consulting",
    "intertek": "Professional Services",
    "correlate resources": "Recruiting",
    "opus recruitment": "Recruiting",
    "fourquarters recruitment": "Recruiting",
    "halcyon knights": "Recruiting",
    "hays": "Recruiting",
    "reqiva": "Recruiting",
    "talent": "Recruiting",
    "talenza": "Recruiting",
    "coco republic": "Retail",
    "crunchyroll": "Media",
    "reapit": "Real Estate",
    "energy vault": "Energy",
    "aircall": "Technology",
    "braze": "Technology",
    "canva": "Technology",
    "databricks": "Technology",
    "nice": "Technology",
    "salesforce": "Technology",
    "insight enterprises": "Technology",
    "kinetic it": "Technology",
    "palo it": "Technology",
    "imei": "Technology",
    "cognition": "Technology",
    "elevenlabs": "Technology",
    "future secure ai": "Technology",
    "j4rvis": "Technology",
    "openai": "Technology",
    "relevance ai": "Technology",
    "sierra": "Technology",
    "ndeva": "Technology",
    "google": "Technology",
}


def _company_category(resolved_company):
    """Substring match, not exact -- the resolved company string carries
    real noise around the actual name ("Hays (client role)", "Halcyon
    Knights (client role)", a bare folder-name fallback like
    "Talenza_AI_Delivery_Lead" normalizing to "talenza ai delivery lead").
    Safe to do as substring here specifically because every key is a
    multi-word or otherwise distinctive proper noun, not a generic word --
    this is a fundamentally different, safer operation than the word-
    overlap company JOIN earlier audit rounds had to lock down; there's no
    risk of two different real companies colliding on a specific name like
    "georgiou group" or "energy vault" the way they could on "group" alone."""
    norm_company = _norm(resolved_company)
    for key, category in _COMPANY_CATEGORY_OVERRIDES.items():
        if key in norm_company or norm_company in key:
            return category
    return None

# Generic business/role words that appear in many unrelated company names --
# excluded from the word-overlap join so "Automic Group" doesn't match
# "Professional Search Group" just because both contain "group". This list
# is exactly the false-positive triggers the second audit round reproduced.
_GENERIC_COMPANY_WORDS = {
    "group", "technology", "technologies", "solutions", "engineer", "engineering",
    "senior", "principal", "forward", "deployed", "ai", "applied", "search",
    "recruitment", "partners", "consulting", "services", "limited", "pty", "ltd",
    "inc", "corp", "company", "the", "and", "client", "role",
}


def _norm(s: str) -> str:
    # Any run of non-alphanumeric characters becomes ONE space -- a word
    # boundary, not silently deleted. Found live (fourth audit round, via
    # outreach_gaps.py): the old version deleted punctuation outright, so
    # "Blackbook.AI" normalized to the single glued token "blackbookai",
    # while the SAME company's folder name ("BlackbookAI_Senior_...")
    # went through _norm_folder()'s camelCase splitter and correctly
    # became two words, "blackbook ai ...". Same company, two different
    # tokenizations depending on which function touched it -- guaranteed
    # to never match. Periods, ampersands, hyphens, parens all get this
    # treatment now, uniformly.
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _norm_folder(name: str) -> str:
    # split camelCase at a lowercase->uppercase boundary only -- NOT
    # digit->uppercase. Found live: "J4RVIS_Forward_..." was splitting to
    # "j4 rvis forward..." (the digit before "RVIS" triggered a false
    # split), which broke the substring match against the tracker's
    # "J4RVIS" company cell entirely. Acronym-style names like this need
    # the digit run to stay attached to the letters around it.
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    return _norm(s.replace("_", " "))


def _extract_first(text: str, keyword_pairs):
    low = text.lower()
    for pattern, label in keyword_pairs:
        if re.search(pattern, low):
            return label
    return None


# _tracker.md's own "Status" column only ever holds one literal value
# ("SUBMITTED") on 11 of 93 rows -- everywhere else, status has to come from
# which '## ' section the row lives under. Second audit round reproduced the
# actual bug: defaulting missing Status to "Applied" mislabeled folders whose
# row sits under "Skipped" or "blocked" sections as status="Applied",
# breaking the module's own headline use case ("only Applied roles").
_SECTION_STATUS_RULES = [
    (r"skip", "Skipped"),
    (r"blocked|ready for manual|not yet started", "Blocked"),
    (r"flagged", "Flagged"),
    (r"applied|submit", "Applied"),
]


def _status_from_section(section_title, explicit_status):
    if explicit_status:
        return "Applied" if explicit_status.strip().upper() == "SUBMITTED" else explicit_status
    if not section_title:
        return None
    low = section_title.lower()
    for pattern, label in _SECTION_STATUS_RULES:
        if re.search(pattern, low):
            return label
    return None


def _significant_words(s: str):
    return {w for w in s.split() if len(w) >= 4 and w not in _GENERIC_COMPANY_WORDS}


def _match_score(key: str, rc: str):
    """Score how confidently `key` (a folder's company) identifies `rc` (a
    tracker row's Company field). Returns None for no match. Higher is
    better. Deliberately conservative -- reproduced in the second audit
    round: a looser version of this (any shared word >3 chars, first match
    wins) joined Automic/Georgiou/Nuage/Macquarie Group all to "Professional
    Search Group" purely via the shared generic word "group". A wrong join
    that LOOKS successful is worse than an honest non-match."""
    if not key or not rc:
        return None
    if key == rc:
        return 100
    if len(key) >= 6 and (key in rc or rc in key):
        return 80
    sig_key, sig_rc = _significant_words(key), _significant_words(rc)
    if not sig_key or not sig_rc:
        return None
    overlap = sig_key & sig_rc
    if not overlap:
        return None
    ratio = len(overlap) / min(len(sig_key), len(sig_rc))
    if ratio >= 0.6:
        return 50 + int(ratio * 10)
    return None


def build_folder_index():
    """One metadata dict per application folder, keyed by folder name.
    Every chunk from that folder gets this dict merged onto its own
    chunk-level metadata (doc_type, section, etc.)."""
    apps_dir = lib.ROOT / "applications"
    tracker = lib.tracker_rows()

    index = {}
    for folder in sorted(p for p in apps_dir.iterdir() if p.is_dir()):
        job_json = folder / "job.json"
        if job_json.exists():
            data = json.loads(job_json.read_text())
            company = data.get("company", "")
            role = data.get("role_short", "")
        else:
            company, role = "", ""

        key = _norm(company) if company else _norm_folder(folder.name)
        scored = []
        for r in tracker:
            rc = _norm(r.get("Company", ""))
            score = _match_score(key, rc)
            if score is not None:
                scored.append((score, r))
        row = None
        if scored:
            scored.sort(key=lambda sr: -sr[0])
            best_score = scored[0][0]
            best_rows = [r for s, r in scored if s == best_score]
            # an exact/substring match (score >= 80) is unambiguous even if
            # the same company has multiple tracker rows (reapplications,
            # multiple batches) -- take the first of those. A tie among
            # WORD-OVERLAP-only matches (score < 80) is genuinely ambiguous
            # between two different companies -- refuse rather than guess.
            if best_score >= 80 or len(best_rows) == 1:
                # Same company can have multiple tied rows (e.g. an early
                # submission-status table row, then a later "BATCH COMPLETE"
                # summary row that adds Sector) -- prefer the row that
                # actually carries Sector over an earlier row that doesn't,
                # rather than blindly taking whichever came first in the file.
                best_rows.sort(key=lambda r: not r.get("Sector"))
                row = best_rows[0]

        notes_path = folder / "NOTES.md"
        notes_text = notes_path.read_text() if notes_path.exists() else ""

        contact_name = None
        if row and row.get("Hiring contact"):
            contact_name = row["Hiring contact"].split(" - ")[0].strip()

        resolved_company = company or (row.get("Company") if row else None) or folder.name
        company_override_category = _company_category(resolved_company)

        index[folder.name] = {
            "company": resolved_company,
            "role": role or (row.get("Role") if row else None),
            "platform": row.get("Platform") if row else None,
            "salary": row.get("Salary") if row else None,
            "status": _status_from_section(row.get("_section") if row else None,
                                           row.get("Status") if row else None),
            "sector": row.get("Sector") if row else None,
            "contact_name": contact_name,
            # tracker's own Location column (only some batches have it, e.g.
            # "Sydney", "Remote") beats the NOTES.md keyword scan when it
            # resolves to a real country; "Remote"/"Hybrid" alone don't, so
            # fall through to the notes scan in that case
            "country": ((_extract_first(row.get("Location", ""), _COUNTRY_KEYWORDS) if row else None)
                        or _extract_first(notes_text, _COUNTRY_KEYWORDS)),
            # Priority: tracker's own Sector column (authoritative, hand-
            # written) > direct company-name lookup (hand-verified against
            # real companies, see _COMPANY_CATEGORY_OVERRIDES) > keyword
            # scan over NOTES.md prose (the least reliable of the three,
            # last resort only).
            "category": ((row.get("Sector") if row and row.get("Sector") else None)
                         or company_override_category
                         or _extract_first(notes_text + " " + role, _CATEGORY_KEYWORDS)),
            "_tracker_matched": row is not None,
        }
    return index


def attach(chunk, folder_index):
    """Merge folder-level metadata onto one chunk's own extra dict.
    Chunk-level fields (doc_type, section, row, part) always win over
    folder-level ones -- they're specific to this exact chunk."""
    rel = chunk.path.relative_to(lib.ROOT)
    folder_name = rel.parts[1] if len(rel.parts) > 1 and rel.parts[0] == "applications" else None
    folder_meta = folder_index.get(folder_name, {}) if folder_name else {}

    # Coerce both sources to Chroma-safe types (str/int/float/bool) --
    # folder_meta happens to always be strings today (everything comes from
    # tracker_rows()'s string cells), but that's not enforced anywhere else,
    # so don't rely on it silently staying true.
    merged = {k: (v if isinstance(v, (str, int, float, bool)) else str(v))
              for k, v in folder_meta.items() if not k.startswith("_") and v is not None}
    merged.update({"doc_type": chunk.doc_type, "path": str(rel)})
    for k, v in chunk.extra.items():
        merged[k] = v if isinstance(v, (str, int, float, bool)) else str(v)
    return merged


def report():
    index = build_folder_index()
    n = len(index)
    matched = sum(1 for v in index.values() if v["_tracker_matched"])
    has_country = sum(1 for v in index.values() if v["country"])
    has_category = sum(1 for v in index.values() if v["category"])
    has_contact = sum(1 for v in index.values() if v["contact_name"])

    print(f"{n} application folders indexed\n")
    print(f"  tracker-joined (platform/salary/status/sector): {matched}/{n} "
          f"({matched/n:.0%}) -- unmatched folders were tracked in _report_*.md "
          f"batch files instead of _tracker.md, not in this join yet")
    print(f"  country detected from NOTES.md:                  {has_country}/{n} ({has_country/n:.0%})")
    print(f"  category detected from NOTES.md/role:             {has_category}/{n} ({has_category/n:.0%})")
    print(f"  hiring contact name parsed:                       {has_contact}/{n} ({has_contact/n:.0%})")

    print("\nCategory breakdown (of the ones detected):")
    from collections import Counter
    cats = Counter(v["category"] for v in index.values() if v["category"])
    for cat, c in cats.most_common():
        print(f"    {cat}: {c}")

    print("\nCountry breakdown (of the ones detected):")
    countries = Counter(v["country"] for v in index.values() if v["country"])
    for c, n_ in countries.most_common():
        print(f"    {c}: {n_}")

    print("\nGAP, not built: 'how long has the job been open'. job.json's date"
          "\nfield is when the application was tailored, not when the role was"
          "\nposted -- the original posting date was never captured for any of"
          "\nthese 90 folders, so days-open can't be backfilled retroactively.")

    print("\nSample folder metadata (Automic_Senior_AI_Engineer):")
    sample = index.get("Automic_Senior_AI_Engineer")
    if sample:
        for k, v in sample.items():
            if not k.startswith("_"):
                print(f"    {k}: {v}")

    return index


if __name__ == "__main__":
    report()
