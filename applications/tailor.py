#!/usr/bin/env python3
"""Generate a tailored resume + cover letter PDF for one job application.
Usage: python3 tailor.py job.json
job.json fields: slug, tagline, cred_role, cred_stack, profile,
comp1_label, comp1_items (list), comp2_label, comp2_items, comp3_label, comp3_items,
current_role_bullets (list), date, addressee, p1, p2, p3, p4
Applicant name comes from ../profile/config.json ("name").
"""
import json, sys, subprocess, os

HERE = os.path.dirname(os.path.abspath(__file__))
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

def applicant_name():
    cfg = os.path.join(HERE, "..", "profile", "config.json")
    try:
        with open(cfg) as f:
            return json.load(f)["name"]
    except (OSError, KeyError, json.JSONDecodeError):
        return "Applicant"

def li_list(items):
    return "\n          ".join(f"<li>{i}</li>" for i in items)

def render_pdf(html_path, pdf_path):
    subprocess.run([
        CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
        f"--print-to-pdf={pdf_path}", "--print-to-pdf-no-header",
        html_path
    ], check=True, capture_output=True)

def main(job_path):
    with open(job_path) as f:
        job = json.load(f)

    out_dir = os.path.join(HERE, job["slug"])
    os.makedirs(out_dir, exist_ok=True)

    # RESUME
    with open(os.path.join(HERE, "_template_resume.html")) as f:
        resume = f.read()
    resume = resume.replace("{{TAGLINE}}", job["tagline"])
    resume = resume.replace("{{CRED_ROLE}}", job["cred_role"])
    resume = resume.replace("{{CRED_STACK}}", job["cred_stack"])
    resume = resume.replace("{{PROFILE}}", job["profile"])
    resume = resume.replace("{{COMP1_LABEL}}", job["comp1_label"])
    resume = resume.replace("{{COMP1_ITEMS}}", li_list(job["comp1_items"]))
    resume = resume.replace("{{COMP2_LABEL}}", job["comp2_label"])
    resume = resume.replace("{{COMP2_ITEMS}}", li_list(job["comp2_items"]))
    resume = resume.replace("{{COMP3_LABEL}}", job["comp3_label"])
    resume = resume.replace("{{COMP3_ITEMS}}", li_list(job["comp3_items"]))
    resume = resume.replace("{{CURRENT_ROLE_BULLETS}}", li_list(job["current_role_bullets"]))

    resume_html_path = os.path.join(out_dir, "Resume.html")
    resume_pdf_path = os.path.join(out_dir, f"{applicant_name()} - Resume - {job['role_short']}.pdf")
    with open(resume_html_path, "w") as f:
        f.write(resume)
    render_pdf(resume_html_path, resume_pdf_path)

    # COVER LETTER
    with open(os.path.join(HERE, "_template_cover.html")) as f:
        cover = f.read()
    cover = cover.replace("{{DATE}}", job["date"])
    cover = cover.replace("{{ADDRESSEE}}", job["addressee"])
    cover = cover.replace("{{P1}}", job["p1"])
    cover = cover.replace("{{P2}}", job["p2"])
    cover = cover.replace("{{P3}}", job["p3"])
    cover = cover.replace("{{P4}}", job["p4"])

    cover_html_path = os.path.join(out_dir, "Cover_Letter.html")
    cover_pdf_path = os.path.join(out_dir, f"{applicant_name()} - Cover Letter - {job['company']}.pdf")
    with open(cover_html_path, "w") as f:
        f.write(cover)
    render_pdf(cover_html_path, cover_pdf_path)

    print(json.dumps({"resume_pdf": resume_pdf_path, "cover_pdf": cover_pdf_path}))

if __name__ == "__main__":
    main(sys.argv[1])
