"""
Tool implementations and mock data for the Recruiting Assistant Agent.

Provides candidate search, outreach generation, funnel analytics, and
email simulation. All tool functions are registered with @app.tool in main.py.
"""

import json
import os
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def _resume_server_url() -> str:
    return os.environ.get("RESUME_SERVER_URL", "http://localhost:8090").rstrip("/")


def _load_json(filename: str) -> Any:
    filepath = _DATA_DIR / filename
    if not filepath.exists():
        return []
    with open(filepath, encoding="utf-8") as f:
        return json.load(f)


def _candidates() -> list[dict[str, Any]]:
    return _load_json("candidates.json")


def _hiring_funnel() -> list[dict[str, Any]]:
    return _load_json("backend_hiring_funnel_nyc.json")


def _rejection_reasons() -> list[dict[str, Any]]:
    return _load_json("rejection_reasons_nyc.json")


# ---------------------------------------------------------------------------
# Tool: search_candidates
# ---------------------------------------------------------------------------


def search_candidates(
    role: str,
    skills: str = "",
    min_years_experience: int = 0,
    preferences: str = "",
) -> str:
    """Search the candidate pool and rank results against role requirements.

    Returns a ranked list of candidates with match scores and evidence.
    When learned recruiter preferences are available, the ranking
    automatically incorporates them (e.g. startup preference, shipped-product
    signal).

    Args:
        role: Target role title (e.g. "Senior Robotics Engineer")
        skills: Comma-separated required skills (e.g. "SLAM, perception, ROS")
        min_years_experience: Minimum years of experience (default 0)
        preferences: Optional comma-separated ranking preferences
            (e.g. "startup experience, shipped product")
    """
    all_candidates = _candidates()
    if not all_candidates:
        return json.dumps({"status": "no_data", "message": "Candidate pool not loaded."}, indent=2)

    required_skills = [s.strip().lower() for s in skills.split(",") if s.strip()]
    pref_list = [p.strip().lower() for p in preferences.split(",") if p.strip()]

    scored: list[dict[str, Any]] = []
    for c in all_candidates:
        cand_skills = [s.lower() for s in c.get("skills", [])]
        yoe = c.get("years_experience", 0)

        if min_years_experience and yoe < min_years_experience:
            continue

        skill_matches = [s for s in required_skills if any(s in cs for cs in cand_skills)]
        skill_score = len(skill_matches) / max(len(required_skills), 1)

        pref_score = 0.0
        applied_insights: list[str] = []
        signals = c.get("signals", {})

        for pref in pref_list:
            if "startup" in pref and signals.get("startup_experience"):
                pref_score += 0.3
                applied_insights.append("Startup experience")
            if "shipped" in pref and signals.get("shipped_product"):
                pref_score += 0.3
                applied_insights.append("Shipped product")
            if "production" in pref and signals.get("production_deployment"):
                pref_score += 0.2
                applied_insights.append("Production deployment")

        total_score = round(0.6 * skill_score + 0.4 * min(pref_score, 1.0), 3)

        name = c.get("name", "")
        profile_filename = name.replace(" ", "_") + ".html"
        scored.append(
            {
                "candidate_id": c.get("candidate_id"),
                "name": name,
                "current_title": c.get("current_title"),
                "current_company": c.get("current_company"),
                "years_experience": yoe,
                "skills_matched": skill_matches,
                "match_score": total_score,
                "applied_insights": applied_insights,
                "resume_ref": c.get("resume_ref", ""),
                "profile_url": f"{_resume_server_url()}/{profile_filename}",
                "summary": c.get("summary", ""),
            }
        )

    scored.sort(key=lambda x: x["match_score"], reverse=True)

    return json.dumps(
        {
            "status": "ok",
            "role": role,
            "total_candidates": len(all_candidates),
            "matches_returned": len(scored[:10]),
            "results": scored[:10],
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# Tool: generate_outreach_batch
# ---------------------------------------------------------------------------


def generate_outreach_batch(
    candidate_ids: str,
    role: str,
    company: str = "MongoDB",
    tone: str = "professional",
    key_selling_points: str = "",
) -> str:
    """Generate personalised outreach drafts for a batch of candidates.

    Creates one draft per candidate on behalf of MongoDB's recruiting team,
    tailored to their background. Drafts are returned for review before sending.

    Args:
        candidate_ids: Comma-separated candidate IDs (e.g. "C001,C002,C003")
        role: Role being recruited for (e.g. "Senior Robotics Engineer")
        company: Hiring company name (defaults to "MongoDB")
        tone: Outreach tone — "professional", "casual", or "technical" (default "professional")
        key_selling_points: Comma-separated selling points to emphasise
            (e.g. "remote-first, strong eng culture, competitive comp")
    """
    if not company or company.lower() in ("your company", "unknown"):
        company = "MongoDB"
    ids = [cid.strip() for cid in candidate_ids.split(",") if cid.strip()]
    all_candidates = {c["candidate_id"]: c for c in _candidates()}

    drafts: list[dict[str, Any]] = []
    for cid in ids:
        cand = all_candidates.get(cid)
        if not cand:
            drafts.append({"candidate_id": cid, "status": "not_found"})
            continue

        name = cand.get("name", "Candidate")
        email = cand.get("email", "")
        title = cand.get("current_title", "Engineer")
        skills = ", ".join(cand.get("skills", [])[:5])

        subject = f"{company} — {role} opportunity"
        body = (
            f"Hi {name},\n\n"
            f"I'm reaching out from the engineering recruiting team at {company}. "
            f"I came across your profile and was impressed by your experience as {title}. "
            f"Your background in {skills} aligns well with a {role} position we're "
            f"hiring for.\n\n"
        )
        if key_selling_points:
            body += f"A few highlights about this role: {key_selling_points}.\n\n"
        body += (
            "I'd love to chat for 15 minutes about whether this could be a fit. "
            "Would you be open to a quick call this week?\n\n"
            f"Best regards,\n{company} Recruiting Team"
        )

        drafts.append(
            {
                "candidate_id": cid,
                "candidate_name": name,
                "email": email,
                "status": "draft",
                "subject": subject,
                "body": body,
                "tone": tone,
            }
        )

    return json.dumps(
        {
            "status": "ok",
            "drafts_count": len(drafts),
            "drafts": drafts,
            "note": "Drafts ready for review. Use request_outreach_approval to submit for approval.",
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# Tool: analyze_funnel_insight
# ---------------------------------------------------------------------------


def analyze_funnel_insight(
    role_family: str,
    location: str,
) -> str:
    """Analyse hiring funnel data and extract actionable insights.

    Reads pipeline analytics and rejection reasons for a role family
    and location, then produces structured recommendations.

    Args:
        role_family: Role family to analyse (e.g. "Backend", "Data Science", "Robotics")
        location: Office location identifier (e.g. "NYC", "SF", "Austin")
    """
    funnel = _hiring_funnel()
    rejections = _rejection_reasons()

    if not funnel and not rejections:
        return json.dumps(
            {
                "status": "no_data",
                "message": (
                    f"No funnel or rejection data found for {role_family} in {location}. "
                    "Data files may not be loaded."
                ),
            },
            indent=2,
        )

    stage_dropoffs: list[dict[str, Any]] = []
    for entry in funnel:
        if entry.get("role_family", "").lower() == role_family.lower():
            stage_dropoffs.append(entry)

    top_rejections: list[dict[str, Any]] = []
    for entry in rejections:
        if entry.get("role_family", "").lower() == role_family.lower():
            top_rejections.append(entry)

    bottleneck_stage = ""
    bottleneck_dropoff = 0.0
    for sd in stage_dropoffs:
        if sd.get("dropoff_pct", 0) > bottleneck_dropoff:
            bottleneck_dropoff = sd["dropoff_pct"]
            bottleneck_stage = sd.get("stage", "")

    recommendations: list[str] = []
    for rej in top_rejections:
        reason = rej.get("reason", "")
        if "scheduling" in reason.lower():
            recommendations.append("Compress scheduling SLA to reduce onsite drop-off")
        if "on-call" in reason.lower() or "oncall" in reason.lower():
            recommendations.append(
                "Pre-emptively clarify on-call expectations in job description and outreach"
            )
        if "compensation" in reason.lower() or "comp" in reason.lower():
            recommendations.append("Review compensation bands against market data")

    insight = {
        "status": "ok",
        "role_family": role_family,
        "location": location,
        "bottleneck_stage": bottleneck_stage,
        "bottleneck_dropoff_pct": bottleneck_dropoff,
        "stage_dropoffs": stage_dropoffs,
        "top_rejection_reasons": top_rejections,
        "recommendations": recommendations,
        "insight_summary": (
            f"{location} {role_family.lower()} hiring: "
            f"highest drop-off at {bottleneck_stage} stage ({bottleneck_dropoff}%). "
            + "; ".join(recommendations[:3])
        ),
    }

    return json.dumps(insight, indent=2)


# ---------------------------------------------------------------------------
# Tool: send_email (mock)
# ---------------------------------------------------------------------------


def send_email(
    candidate_ids: str,
    subject: str,
    body: str = "",
) -> str:
    """Send outreach emails to one or more candidates (mock — logs instead of sending).

    In production this would integrate with an email service.
    For the demo it returns a success confirmation for each candidate.

    Args:
        candidate_ids: Comma-separated candidate IDs (e.g. "C001,C002,C003")
        subject: Email subject line
        body: Email body text
    """
    ids = [cid.strip() for cid in candidate_ids.split(",") if cid.strip()]
    all_candidates = {c["candidate_id"]: c for c in _candidates()}

    results = []
    for cid in ids:
        cand = all_candidates.get(cid, {})
        name = cand.get("name", cid)
        results.append(
            {
                "candidate_id": cid,
                "name": name,
                "status": "sent",
                "subject": subject,
            }
        )

    return json.dumps(
        {
            "status": "ok",
            "emails_sent": len(results),
            "results": results,
            "message": f"Outreach emails sent to {len(results)} candidate(s).",
        },
        indent=2,
    )
