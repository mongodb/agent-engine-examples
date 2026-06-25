"""Tool registration for the data analyst demo."""

from __future__ import annotations

import json
import logging
from typing import Any

from data_analyst_agent.data_store import DemoDataStore
from data_analyst_agent.review import DEFAULT_RATING_ACTION, DEFAULT_RELATIVITY
from data_analyst_agent.routing import memory_user_id

logger = logging.getLogger(__name__)


def register_tools(app: Any, data_store: DemoDataStore) -> None:
    @app.tool(is_local=False)
    def compare_loss_frequency() -> str:
        """Compare Customer loss frequency across one-, two-, and three-pedal vehicles."""
        return json.dumps(data_store.compare_loss_frequency(), indent=2)

    @app.tool(is_local=False)
    def cohort_mix_over_time() -> str:
        """Return quarterly Customer cohort mix by pedal count."""
        return json.dumps(data_store.cohort_mix_over_time(), indent=2)

    @app.tool(is_local=False)
    def get_claim_narratives(pedal_count: int = 1, limit: int = 5) -> str:
        """Return representative claim narratives for a pedal-count cohort."""
        return json.dumps(
            data_store.claims_narratives(pedal_count=pedal_count, limit=limit), indent=2
        )

    @app.tool(is_local=False)
    def plot_loss_frequency_chart(analytics_result_json: str) -> str:
        """Prepare the loss-frequency chart artifact for Playground rendering."""
        rows = _analytics_result_rows(analytics_result_json, "loss_frequency")
        if not rows:
            return json.dumps(
                {
                    "status": "error",
                    "message": "Unable to build a loss frequency chart from the analytics result.",
                },
                indent=2,
            )
        return json.dumps(
            {
                "status": "ok",
                "artifact_id": "artifact-chart-loss-frequency",
                "message": "Rendered loss frequency chart for one-, two-, and three-pedal cohorts.",
                "title": "Loss frequency by pedal cohort",
            },
            indent=2,
        )

    @app.tool(is_local=False)
    def plot_cohort_mix_trend_chart(analytics_result_json: str) -> str:
        """Prepare the cohort-mix trend chart artifact for Playground rendering."""
        rows = _analytics_result_rows(analytics_result_json, "cohort_mix")
        if not rows:
            return json.dumps(
                {
                    "status": "error",
                    "message": "Unable to build a cohort mix trend chart from the analytics result.",
                },
                indent=2,
            )
        return json.dumps(
            {
                "status": "ok",
                "artifact_id": "artifact-chart-cohort-mix",
                "message": "Rendered cohort mix trend chart by quarter.",
                "title": "Cohort mix by quarter",
            },
            indent=2,
        )

    @app.tool(is_local=False)
    def write_audit_log(decision: str, summary: str, reviewer_notes: str = "") -> str:
        """Write an approved rating recommendation to audit_log."""
        document = data_store.write_audit_log(
            {
                "decision": decision,
                "summary": summary,
                "reviewer_notes": reviewer_notes,
                "rating_action": DEFAULT_RATING_ACTION,
                "proposed_relativity": DEFAULT_RELATIVITY,
            }
        )
        return json.dumps(document, default=str, indent=2)

    @app.tool(is_local=False)
    def recall_demo_memories(query: str) -> str:
        """Search semantic, taxonomic, episodic, and procedural demo memories."""
        return json.dumps(_recall_demo_memories(app, query), default=str, indent=2)


def _recall_demo_memories(app: Any, query: str) -> dict[str, Any]:
    user_id = memory_user_id(app)
    try:
        return {
            "semantic": app.memory.search_semantic(query=query, user_id=user_id, top_k=3),
            "taxonomic": app.memory.search_taxonomic(query=query, top_k=3),
            "episodic": app.memory.search_episodes(query=query, user_id=user_id, top_k=3),
            "procedural": app.memory.discover_procedures(
                query=query,
                user_id=user_id,
                top_k=3,
                similarity_threshold=0.5,
            ),
        }
    except Exception as exc:  # noqa: BLE001 - memory is optional for local smoke tests.
        logger.warning("Memory recall failed: %s", exc)
        return {"error": str(exc)}


def _analytics_result_rows(analytics_result_json: str, key: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(analytics_result_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []
    rows = parsed.get(key)
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]
