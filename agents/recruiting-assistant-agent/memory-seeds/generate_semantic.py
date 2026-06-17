#!/usr/bin/env python3
"""Generate semantic.json from candidates.json + static org entries.

Reads candidates.json and produces one semantic memory per candidate,
plus org-wide insights.
Re-run this script any time candidates.json changes to keep memories in sync.

Usage:
    python generate_semantic.py
"""

import json
from pathlib import Path

CANDIDATES_FILE = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "candidates.json"
)
OUTPUT_FILE = Path(__file__).resolve().parent / "semantic.json"

ORG_ID = "org_recruiting_demo"


def _build_candidate_text(c: dict) -> str:
    """Produce a single rich-text memory combining profile + evidence."""
    skills_str = ", ".join(c.get("skills", []))

    evidence_lines = []
    for exp in c.get("experience", []):
        for h in exp.get("highlights", []):
            evidence_lines.append(f"- {h} ({exp['company']}, {exp['duration']})")

    edu_lines = []
    for edu in c.get("education", []):
        edu_lines.append(f"{edu['degree']} from {edu['institution']} ({edu['year']})")

    text = (
        f"{c['name']}: {c['current_title']} at {c['current_company']}. "
        f"{c['years_experience']} years of experience. "
        f"Location: {c['location']}. "
        f"Skills: {skills_str}. "
        f"{c.get('summary', '')} "
        f"Education: {'; '.join(edu_lines)}. "
        f"Key evidence: {' '.join(evidence_lines)}"
    )
    return text


def _candidate_memories(candidates: list[dict]) -> list[dict]:
    memories = []
    for c in candidates:
        cid = c["candidate_id"]
        memories.append(
            {
                "label": f"candidate_profile_{cid.lower()}",
                "text": _build_candidate_text(c),
                "org_id": ORG_ID,
                "source": "candidate_profile",
                "visibility": "org",
                "contextual_metadata": {
                    "candidate_id": cid,
                    "candidate_name": c["name"],
                    "role_family": _infer_role_family(c),
                    "location": c["location"],
                    "resume_ref": c.get("resume_ref", ""),
                },
            }
        )
    return memories


def _infer_role_family(c: dict) -> str:
    title = c.get("current_title", "").lower()
    skills = [s.lower() for s in c.get("skills", [])]

    if any(k in title for k in ["robot", "perception", "slam"]):
        return "Robotics"
    if any(k in skills for k in ["slam", "ros", "ros2", "lidar", "robotics", "grasping"]):
        return "Robotics"
    if any(k in title for k in ["data scien", "ml lead", "head of data"]):
        return "Data Science"
    if any(k in title for k in ["backend", "platform"]):
        return "Backend"
    if any(k in title for k in ["ml ", "mlops", "ml infra", "machine learning", "computer vision"]):
        return "ML Engineering"
    if "sre" in title or "reliability" in title:
        return "SRE"
    if "full-stack" in title or "full stack" in title:
        return "Full-Stack"
    if "product" in title:
        return "Product Engineering"
    return "Engineering"


STATIC_MEMORIES = [
    {
        "label": "org_insight_backend_nyc",
        "text": (
            "Org-wide hiring insight for Backend roles in NYC (New York, NY): "
            "Highest pipeline drop-off occurs at the onsite interview stage (38% drop-off). "
            "Top rejection reasons: (1) long scheduling delays averaging 12 days from screen to onsite, "
            "(2) candidates surprised by on-call expectations not mentioned earlier in process, "
            "(3) comp expectations misaligned — candidates benchmarking against FAANG offers. "
            "Recommendations: compress scheduling SLA to under 7 days, pre-emptively clarify "
            "on-call rotation in job description and recruiter screen, include comp range in "
            "initial outreach to filter early. This insight is derived from Q3-Q4 2025 pipeline "
            "data across 47 backend candidates in NYC."
        ),
        "org_id": ORG_ID,
        "source": "funnel_analytics",
        "visibility": "org",
        "contextual_metadata": {
            "role_family": "Backend",
            "location": "New York, NY",
            "bottleneck_stage": "onsite",
            "bottleneck_dropoff_pct": 38,
            "data_period": "Q3-Q4 2025",
            "candidate_count": 47,
        },
    },
]


def generate() -> None:
    with open(CANDIDATES_FILE, encoding="utf-8") as f:
        candidates = json.load(f)

    memories = _candidate_memories(candidates) + STATIC_MEMORIES

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(memories, f, indent=2, ensure_ascii=False)

    print(
        f"Generated {len(memories)} semantic memories "
        f"({len(candidates)} candidates + {len(STATIC_MEMORIES)} static) → {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    generate()
