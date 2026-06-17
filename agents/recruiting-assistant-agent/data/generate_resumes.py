#!/usr/bin/env python3
"""Generate HTML resume pages from candidates.json.

Reads candidates.json and writes one HTML file per candidate into resumes/.
Re-run this script any time candidates.json changes to keep HTML in sync.

Usage:
    python generate_resumes.py
"""

import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent
CANDIDATES_FILE = DATA_DIR / "candidates.json"
RESUMES_DIR = DATA_DIR / "resumes"


def _name_to_filename(name: str) -> str:
    """Convert 'Dr. Anika Johansson' → 'Anika_Johansson'."""
    clean = re.sub(r"^(Dr\.|Mr\.|Ms\.|Mrs\.)\s*", "", name).strip()
    return clean.replace(" ", "_")


HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{name} — Resume</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
      color: #1a1a2e;
      background: #f8f9fa;
      line-height: 1.6;
    }}
    .container {{
      max-width: 800px;
      margin: 2rem auto;
      background: #fff;
      border-radius: 12px;
      box-shadow: 0 4px 24px rgba(0,0,0,0.08);
      overflow: hidden;
    }}
    .header {{
      background: linear-gradient(135deg, #0f3460 0%, #16213e 100%);
      color: #fff;
      padding: 2.5rem 2.5rem 2rem;
    }}
    .header h1 {{
      font-size: 1.8rem;
      font-weight: 700;
      margin-bottom: 0.3rem;
    }}
    .header .title {{
      font-size: 1.1rem;
      color: #a3cfff;
      margin-bottom: 0.2rem;
    }}
    .header .company {{
      font-size: 0.95rem;
      color: #7fb3e0;
    }}
    .contact {{
      display: flex;
      flex-wrap: wrap;
      gap: 1.2rem;
      margin-top: 1rem;
      font-size: 0.85rem;
      color: #c8ddf0;
    }}
    .contact span {{ display: flex; align-items: center; gap: 0.3rem; }}
    .body {{ padding: 2rem 2.5rem 2.5rem; }}
    .section {{ margin-bottom: 1.8rem; }}
    .section-title {{
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: #0f3460;
      border-bottom: 2px solid #e8edf3;
      padding-bottom: 0.4rem;
      margin-bottom: 1rem;
      font-weight: 700;
    }}
    .summary {{ color: #444; font-size: 0.95rem; }}
    .skills {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
    }}
    .skill {{
      background: #e8f0fe;
      color: #0f3460;
      padding: 0.25rem 0.75rem;
      border-radius: 100px;
      font-size: 0.82rem;
      font-weight: 500;
    }}
    .exp-entry {{ margin-bottom: 1.4rem; }}
    .exp-header {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      flex-wrap: wrap;
    }}
    .exp-role {{ font-weight: 600; font-size: 1rem; color: #1a1a2e; }}
    .exp-duration {{ font-size: 0.85rem; color: #666; }}
    .exp-company {{ font-size: 0.9rem; color: #0f3460; margin-bottom: 0.4rem; }}
    .exp-highlights {{ padding-left: 1.2rem; }}
    .exp-highlights li {{
      margin-bottom: 0.3rem;
      font-size: 0.9rem;
      color: #333;
    }}
    .edu-entry {{ margin-bottom: 0.6rem; }}
    .edu-degree {{ font-weight: 600; font-size: 0.95rem; }}
    .edu-meta {{ font-size: 0.85rem; color: #666; }}
    .signals {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      margin-top: 0.5rem;
    }}
    .signal {{
      padding: 0.2rem 0.65rem;
      border-radius: 100px;
      font-size: 0.78rem;
      font-weight: 600;
    }}
    .signal-yes {{ background: #d4edda; color: #155724; }}
    .signal-no {{ background: #f8d7da; color: #721c24; }}
    .signal-neutral {{ background: #fff3cd; color: #856404; }}
    .footer {{
      text-align: center;
      padding: 1rem;
      font-size: 0.75rem;
      color: #999;
      border-top: 1px solid #eee;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>{name}</h1>
      <div class="title">{current_title}</div>
      <div class="company">{current_company}</div>
      <div class="contact">
        <span>{location}</span>
        <span>{email}</span>
        <span>{linkedin}</span>
      </div>
    </div>
    <div class="body">
      <div class="section">
        <div class="section-title">Summary</div>
        <p class="summary">{summary}</p>
      </div>

      <div class="section">
        <div class="section-title">Key Signals</div>
        <div class="signals">{signals_html}</div>
      </div>

      <div class="section">
        <div class="section-title">Skills</div>
        <div class="skills">{skills_html}</div>
      </div>

      <div class="section">
        <div class="section-title">Experience</div>
        {experience_html}
      </div>

      <div class="section">
        <div class="section-title">Education</div>
        {education_html}
      </div>
    </div>
    <div class="footer">
      Candidate ID: {candidate_id} &middot; {years_experience} years of experience &middot; Mock resume for demo purposes
    </div>
  </div>
</body>
</html>
"""


def _signals_html(signals: dict) -> str:
    parts = []
    for key, label in [
        ("startup_experience", "Startup Experience"),
        ("shipped_product", "Shipped Product"),
        ("production_deployment", "Production Deployment"),
        ("research_focus", "Research Focus"),
    ]:
        val = signals.get(key, False)
        if key == "research_focus":
            cls = "signal-neutral" if val else "signal-no"
        else:
            cls = "signal-yes" if val else "signal-no"
        icon = "✓" if val else "✗"
        parts.append(f'<span class="signal {cls}">{icon} {label}</span>')

    pub_count = signals.get("publications_count", 0)
    cls = "signal-neutral" if pub_count > 0 else "signal-no"
    parts.append(f'<span class="signal {cls}">{pub_count} Publications</span>')
    return "\n        ".join(parts)


def _skills_html(skills: list[str]) -> str:
    return "\n        ".join(f'<span class="skill">{s}</span>' for s in skills)


def _experience_html(experience: list[dict]) -> str:
    entries = []
    for exp in experience:
        highlights = ""
        if exp.get("highlights"):
            items = "\n".join(f"          <li>{h}</li>" for h in exp["highlights"])
            highlights = f'\n        <ul class="exp-highlights">\n{items}\n        </ul>'
        entries.append(
            f"""      <div class="exp-entry">
        <div class="exp-header">
          <span class="exp-role">{exp["title"]}</span>
          <span class="exp-duration">{exp["duration"]}</span>
        </div>
        <div class="exp-company">{exp["company"]}</div>{highlights}
      </div>"""
        )
    return "\n".join(entries)


def _education_html(education: list[dict]) -> str:
    entries = []
    for edu in education:
        entries.append(
            f"""      <div class="edu-entry">
        <div class="edu-degree">{edu["degree"]}</div>
        <div class="edu-meta">{edu["institution"]} — {edu["year"]}</div>
      </div>"""
        )
    return "\n".join(entries)


def generate() -> None:
    RESUMES_DIR.mkdir(exist_ok=True)
    with open(CANDIDATES_FILE, encoding="utf-8") as f:
        candidates = json.load(f)

    # Remove old C0xx.html files from previous runs
    for old in RESUMES_DIR.glob("C0*.html"):
        old.unlink()

    json_modified = False
    for c in candidates:
        filename = _name_to_filename(c["name"])
        expected_ref = f"resumes/{filename}.html"

        if c.get("resume_ref") != expected_ref:
            c["resume_ref"] = expected_ref
            json_modified = True

        html = HTML_TEMPLATE.format(
            name=c["name"],
            candidate_id=c["candidate_id"],
            current_title=c["current_title"],
            current_company=c["current_company"],
            location=c["location"],
            email=c["email"],
            linkedin=c["linkedin"],
            years_experience=c["years_experience"],
            summary=c["summary"],
            signals_html=_signals_html(c.get("signals", {})),
            skills_html=_skills_html(c.get("skills", [])),
            experience_html=_experience_html(c.get("experience", [])),
            education_html=_education_html(c.get("education", [])),
        )
        out_path = RESUMES_DIR / f"{filename}.html"
        out_path.write_text(html, encoding="utf-8")

    if json_modified:
        with open(CANDIDATES_FILE, "w", encoding="utf-8") as f:
            json.dump(candidates, f, indent=2, ensure_ascii=False)
        print(f"Updated resume_ref fields in {CANDIDATES_FILE}")

    print(f"Generated {len(candidates)} resume HTML files in {RESUMES_DIR}/")


if __name__ == "__main__":
    generate()
