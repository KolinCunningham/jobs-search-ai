"""
Step 9 -- outreach: LinkedIn, SEEK, and email, not just applications.

Every application folder can carry an outreach.md draft and a hiring
contact, but only ONE channel has ever actually been sent through, per the
project's own history: _outreach_log.md is a real, hand-kept LinkedIn send
log (21 rows). SEEK in-platform messaging and direct recruiter email have
never been built as channels at all -- there's no data anywhere recording
either one, for any company. That's stated here plainly rather than
faked with an invented "outreach_sent" field with no real values behind it.

So the real, honest scope of this script: reconcile which folders have a
DRAFT (outreach.md) against which companies have a recorded LinkedIn SEND
(_outreach_log.md), and surface the gap -- companies with a draft sitting
ready that was never actually sent. This reproduces, programmatically and
re-runnably, work a previous session did by hand in _outreach_log.md's own
"Not yet sent" section (26 drafted-not-sent + 4 never-drafted, 24 Aug 2026)
-- see report() for how closely the two agree.

Email is out of scope for automated action here for a second reason beyond
"never built": there is no contact_email field anywhere in this corpus (see
meta.py's docstring) to send one TO. Flagged as a real gap, not drafted.
"""

import lib
import meta

MIN_DRAFT_BYTES = 20   # an outreach.md under this is empty/placeholder, not a real draft


def _linkedin_sent_companies():
    """Company -> most recent LinkedIn send status, from the real hand-kept
    log. A company counts as 'sent' if any row's Status contains 'Sent'."""
    rows = lib.parse_table_file(lib.ROOT / "applications" / "_outreach_log.md")
    sent = {}
    for row in rows:
        company = row.get("Company")
        status = row.get("Status", "")
        if company and "sent" in status.lower():
            sent[meta._norm(company)] = status
    return sent


def _best_match(key, candidates):
    """Same scored-match discipline as meta.py's build_folder_index() --
    exact/substring wins outright, a word-overlap tie between two different
    real companies is refused rather than guessed. Deliberately not a
    looser ad hoc scheme: an earlier draft of this function accepted
    whichever candidate scored highest with no floor, which is exactly the
    generic-word false-join bug the second audit round found and fixed in
    meta.py -- caught while writing this, not by review."""
    scored = [(meta._match_score(key, cand_key), cand_key) for cand_key in candidates]
    scored = [(s, k) for s, k in scored if s is not None]
    if not scored:
        return None
    scored.sort(key=lambda sk: -sk[0])
    best_score = scored[0][0]
    best = [k for s, k in scored if s == best_score]
    if best_score >= 80 or len(best) == 1:
        return best[0]
    return None


def _raw_overlap_ratio(a, b):
    """Same word-overlap math as meta._match_score, WITHOUT its 0.6 accept
    floor -- used only for reporting a near-miss to a human, never for
    auto-matching. Distinguishes "genuinely zero relation" (0.0) from "some
    real overlap that didn't clear the bar" (found live: Talenza's folder
    key "talenza ai delivery lead" vs. the log's "talenza client financial
    services org" -- ratio 0.5, correctly refused by the strict matcher
    since "financial"/"delivery"/"lead" share nothing, but genuinely the
    same company, worth a human glance rather than silent mislabeling)."""
    sig_a, sig_b = meta._significant_words(a), meta._significant_words(b)
    if not sig_a or not sig_b:
        return 0.0
    overlap = sig_a & sig_b
    return len(overlap) / min(len(sig_a), len(sig_b)) if overlap else 0.0


def build_report():
    apps_dir = lib.ROOT / "applications"
    folder_index = meta.build_folder_index()   # company resolution, reused from step 5
    linkedin_sent = _linkedin_sent_companies()

    categories = {"sent": [], "drafted_not_sent": [], "not_drafted": []}
    matched_log_keys = set()
    folder_keys = {}   # folder.name -> (key, company), collected for the near-miss pass below

    for folder in sorted(p for p in apps_dir.iterdir() if p.is_dir()):
        fmeta = folder_index.get(folder.name, {})
        company = fmeta.get("company") or folder.name
        # meta.build_folder_index() itself falls back to the raw folder
        # name (no job.json, no tracker match) and ALWAYS runs that through
        # _norm_folder()'s camelCase/underscore splitting, never plain
        # _norm(). This file was calling plain _norm() unconditionally --
        # real bug, found in the fourth audit round: Talenza_AI_Delivery_Lead
        # (a real, correctly-sent folder) normalized to the glued string
        # "talenzaaideliverylead" instead of "talenza ai delivery lead",
        # which then failed to match the outreach log's "Talenza" entry --
        # wrongly landing in BOTH sent_no_folder (as if orphaned) and
        # not_drafted (hiding that it was actually sent). Detecting the
        # fallback case the same way meta.py does -- company resolved to
        # nothing else, so it IS the raw folder name -- fixes it.
        key = meta._norm_folder(folder.name) if company == folder.name else meta._norm(company)
        folder_keys[folder.name] = (key, company)

        draft_path = folder / "outreach.md"
        has_draft = draft_path.exists() and draft_path.stat().st_size >= MIN_DRAFT_BYTES

        matched_key = _best_match(key, linkedin_sent.keys())
        sent_status = linkedin_sent.get(matched_key) if matched_key else None

        if sent_status:
            categories["sent"].append((company, folder.name, sent_status))
            matched_log_keys.add(matched_key)
        elif has_draft:
            categories["drafted_not_sent"].append((company, folder.name))
        else:
            categories["not_drafted"].append((company, folder.name))

    # Companies the log says were sent to, that never matched any folder --
    # split into two REALLY different cases, not lumped together (fourth
    # audit round found the earlier lumped version was wrong: Talenza has a
    # real folder, it just doesn't clear the strict match threshold, and
    # was being reported identically to weave./Galileo/MapAI/Lookahead,
    # which genuinely have none at all).
    unmatched = [k for k in linkedin_sent if k not in matched_log_keys]
    orphaned, needs_review = [], []
    for log_key in unmatched:
        # Track ALL folders tied at the best ratio, not just the first one
        # found -- found live: Talenza's own folder and Insignia's folder
        # both scored exactly 0.5 against "talenza client financial
        # services org" ("talenza" vs. "financial" respectively). Keeping
        # only the first-seen winner (Insignia, purely because it's
        # alphabetically earlier) would have pointed a human at the WRONG
        # company with false confidence -- worse than showing nothing.
        best_ratio = 0.0
        best_folders = []
        for folder_name, (fkey, _company) in folder_keys.items():
            ratio = _raw_overlap_ratio(log_key, fkey)
            if ratio > best_ratio:
                best_ratio, best_folders = ratio, [folder_name]
            elif ratio == best_ratio and ratio > 0:
                best_folders.append(folder_name)
        if best_ratio > 0:
            needs_review.append((log_key, best_folders, best_ratio))
        else:
            orphaned.append(log_key)

    categories["sent_no_folder"] = sorted(orphaned)
    categories["sent_needs_review"] = sorted(needs_review, key=lambda t: -t[2])

    return categories


def write_report(categories, out_path):
    lines = ["# Outreach gaps\n"]
    lines.append(f"LinkedIn sent: {len(categories['sent'])} · "
                 f"Drafted, not sent: {len(categories['drafted_not_sent'])} · "
                 f"No draft yet: {len(categories['not_drafted'])}\n")

    lines.append("## Ready to send now — drafted, never sent via LinkedIn\n")
    for company, folder, in sorted(categories["drafted_not_sent"]):
        lines.append(f"- **{company}** — `applications/{folder}/outreach.md`")
    lines.append("")

    lines.append("## No draft at all yet\n")
    for company, folder in sorted(categories["not_drafted"]):
        lines.append(f"- **{company}** — `applications/{folder}/`")
    lines.append("")

    if categories.get("sent_needs_review"):
        lines.append("## Sent via LinkedIn — folder match unconfirmed, check by hand\n")
        lines.append("The strict matcher found a real folder with SOME word overlap, but not "
                     "enough to auto-accept (the bar that stops it from ever repeating the "
                     "Automic/Georgiou/Nuage cross-join bug found in an earlier audit round). "
                     "These are the folders most likely to actually be that company:\n")
        for log_key, folder_names, ratio in categories["sent_needs_review"]:
            candidates = ", ".join(f"`applications/{f}/`" for f in folder_names)
            lines.append(f"- \"{log_key}\" → possibly {candidates} (overlap {ratio:.2f} each)")
        lines.append("")

    if categories.get("sent_no_folder"):
        lines.append("## Sent via LinkedIn, no folder exists at all\n")
        lines.append("Batch-1 companies applied to with a shared generic resume, before the "
                     "per-application-folder pipeline existed -- zero word overlap with any "
                     "real folder, not just an unconfirmed one:\n")
        for k in categories["sent_no_folder"]:
            lines.append(f"- {k}")
        lines.append("")

    lines.append("## Channels not built, real gaps, not faked\n")
    lines.append("- **SEEK messaging**: never attempted for any company. No data exists to check.")
    lines.append("- **Direct email**: never attempted, and no `contact_email` field exists anywhere "
                 "in this corpus to send one to (see meta.py's docstring) — would need a lookup step "
                 "before this could even be drafted, let alone sent. Email stays draft-only regardless, "
                 "per the standing rule: no script in this pipeline sends an email, that's the applicant's call.")

    out_path.write_text("\n".join(lines))


def report():
    categories = build_report()
    out_path = lib.ROOT / "outreach_gaps.md"
    write_report(categories, out_path)

    print(f"LinkedIn sent:        {len(categories['sent'])}")
    print(f"Drafted, not sent:    {len(categories['drafted_not_sent'])}  <- actionable today")
    print(f"No draft yet:         {len(categories['not_drafted'])}")
    print(f"Sent, needs review:   {len(categories['sent_needs_review'])}  (folder exists, match unconfirmed)")
    print(f"Sent, no folder:      {len(categories['sent_no_folder'])}  (batch-1, pre-pipeline, confirmed none)")
    print(f"\nWritten to {out_path.relative_to(lib.ROOT)}")
    return categories


if __name__ == "__main__":
    report()
