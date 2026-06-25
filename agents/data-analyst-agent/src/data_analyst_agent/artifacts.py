"""Playground artifact helpers for the data analyst demo."""

from __future__ import annotations

from typing import Any

import magenta_sdk_core

MessageArtifact: Any | None = getattr(magenta_sdk_core, "MessageArtifact", None)
Artifact = dict[str, Any]


def build_mongodb_query_artifact(
    *,
    artifact_id: str,
    title: str,
    collection: str,
    pipeline: list[dict[str, Any]],
    summary: str,
) -> Artifact:
    return {
        "id": artifact_id,
        "kind": "mongodb_query",
        "title": title,
        "collection": collection,
        "operation": "aggregate",
        "pipeline": pipeline,
        "pipeline_summary": summary,
    }


def build_chart_artifact(
    *,
    artifact_id: str,
    title: str,
    chart_type: str,
    label_key: str,
    metric: str,
    data: list[dict[str, Any]],
    summary: str,
) -> Artifact:
    return {
        "id": artifact_id,
        "kind": "chart",
        "title": title,
        "chart_type": chart_type,
        "label_key": label_key,
        "metric": metric,
        "data": data,
        "data_summary": summary,
    }


def build_image_artifact(
    *,
    artifact_id: str,
    title: str,
    mime_type: str,
    image_base64: str,
    alt: str | None = None,
) -> Artifact:
    return {
        "id": artifact_id,
        "kind": "image",
        "title": title,
        "mime_type": mime_type,
        "image_base64": image_base64,
        "alt": alt or title,
    }


def build_loss_frequency_chart_artifact(
    *,
    artifact_id: str,
    title: str,
    rows: list[dict[str, Any]],
) -> Artifact:
    return build_chart_artifact(
        artifact_id=artifact_id,
        title=title,
        chart_type="bar",
        label_key="label",
        metric="loss_frequency_per_1000",
        data=rows,
        summary="Claims per 1000 policies by pedal-count cohort.",
    )


def build_cohort_mix_chart_artifact(
    *,
    artifact_id: str,
    title: str,
    rows: list[dict[str, Any]],
) -> Artifact:
    return build_chart_artifact(
        artifact_id=artifact_id,
        title=title,
        chart_type="line",
        label_key="quarter",
        metric="share_pct",
        data=rows,
        summary="Quarterly policy share by pedal-count cohort.",
    )


def build_source_artifact(
    *,
    artifact_id: str,
    title: str,
    sources: list[dict[str, Any]],
    summary: str,
) -> Artifact:
    return {
        "id": artifact_id,
        "kind": "source",
        "title": title,
        "content": _source_content(sources),
        "metadata": {
            "sources": sources,
            "summary": summary,
        },
    }


def build_review_summary_artifact(
    *,
    artifact_id: str,
    title: str,
    summary: str,
    decision: dict[str, Any],
) -> Artifact:
    return {
        "id": artifact_id,
        "kind": "review_summary",
        "title": title,
        "status": "awaiting_human_review",
        "decision_type": str(decision.get("decision_type") or "rating_recommendation"),
        "summary": summary,
        "metadata": {"decision": decision},
    }


def _source_content(sources: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for source in sources:
        label = str(source.get("label") or source.get("id") or "Source")
        summary = str(source.get("summary") or "").strip()
        lines.append(f"{label}: {summary}" if summary else label)
    return "\n".join(lines)


def build_message_artifact_metadata(artifacts: list[Artifact]) -> dict[str, Any]:
    """Build SDK message metadata for Playground-rendered artifacts."""
    if MessageArtifact is None:
        return {"artifacts": artifacts}

    return {
        "artifacts": [
            MessageArtifact.model_validate(artifact).model_dump(
                exclude_none=True,
                mode="json",
            )
            for artifact in artifacts
        ]
    }
