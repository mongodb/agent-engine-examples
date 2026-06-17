from __future__ import annotations

import json

from recruiting_assistant_agent.tools import (
    analyze_funnel_insight,
    generate_outreach_batch,
    search_candidates,
)


def test_search_candidates_applies_preferences() -> None:
    payload = json.loads(
        search_candidates(
            role="Senior Robotics Engineer",
            skills="SLAM, perception",
            preferences="startup experience, shipped product",
        )
    )

    assert payload["status"] == "ok"
    results = payload["results"]
    assert results
    assert results[0]["profile_url"].startswith("http://localhost:8090/")
    assert any(result["applied_insights"] for result in results[:3])
    assert any("Startup experience" in result["applied_insights"] for result in results[:3])


def test_search_candidates_uses_runtime_resume_server_url(monkeypatch) -> None:
    monkeypatch.setenv("RESUME_SERVER_URL", "http://localhost:9777")

    payload = json.loads(search_candidates(role="Senior Robotics Engineer"))

    assert payload["results"]
    assert payload["results"][0]["profile_url"].startswith("http://localhost:9777/")


def test_generate_outreach_batch_returns_drafts() -> None:
    payload = json.loads(
        generate_outreach_batch(
            candidate_ids="C002,C010",
            role="Senior Robotics Engineer",
            company="MongoDB",
        )
    )

    assert payload["status"] == "ok"
    assert payload["drafts_count"] == 2
    assert all(draft["status"] == "draft" for draft in payload["drafts"])
    assert all(
        "MongoDB" in draft["subject"] or "MongoDB" in draft["body"] for draft in payload["drafts"]
    )


def test_analyze_funnel_insight_returns_recommendations() -> None:
    payload = json.loads(analyze_funnel_insight(role_family="Backend", location="NYC"))

    assert payload["status"] == "ok"
    assert payload["role_family"] == "Backend"
    assert payload["location"] == "NYC"
    assert payload["recommendations"]
