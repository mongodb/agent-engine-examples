"""
Pricing Analyst Agent - Magenta SDK example for pricing workflow demos.

This is a minimal agent that demonstrates:
- @app.tool decorator for secure, logged tools
- LangGraph for agent orchestration
- MongoDB checkpointing for conversation state

The platform launches this code in supported runtime roles:
    RUNNER_MODE=aer            -> Full LangGraph execution
    RUNNER_MODE=tool           -> Tool function execution
    RUNNER_MODE=memory-server  -> Long-term memory service

See env.example for configuration options.
"""

import atexit
import json
import logging
import os
import re
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional, Sequence, TypedDict, cast

import httpx
from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import InjectedState, ToolNode
from magenta_sdklanggraph import App
from runner_shared.context import (
    get_current_execution_id,
    get_current_oe_url,
    get_current_session_id,
    get_current_user_id,
)
from runner_shared.models import SuspendPayload

from .llm import build_llm
from . import tools as pricing_tools

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)
load_dotenv()

ENABLE_TRACING = os.environ.get("ENABLE_TRACING", "false").lower() == "true"
ENABLE_MEMORY = os.environ.get("ENABLE_MEMORY", "false").lower() == "true"
ENABLE_GUARDRAILS = os.environ.get("ENABLE_GUARDRAILS", "false").lower() == "true"

APP_NAME = "Pricing Analyst Agent"
ORG_ID = "org_pricing_demo"

app = App(
    app_name=APP_NAME,
    enable_tracing=ENABLE_TRACING,
    enable_memory=ENABLE_MEMORY,
    enable_guardrails=ENABLE_GUARDRAILS,
)
logger.info("App created")

SESSION_SUMMARY_SNAPSHOT_REF_ID = "session_summary"
SUMMARY_MIN_HUMAN_TURNS = 6
_SUMMARY_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pricing_summary")
_PROCEDURE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pricing_procedure")
atexit.register(lambda: _SUMMARY_EXECUTOR.shutdown(wait=False, cancel_futures=True))
atexit.register(lambda: _PROCEDURE_EXECUTOR.shutdown(wait=False, cancel_futures=True))


def _memory_engine() -> Any | None:
    """Return the underlying MemoryEngine when memory is enabled."""
    return getattr(app._runtime, "memory_engine", None)


def _background_llm(temperature: float = 0) -> BaseChatModel:
    """Create a raw provider client for background extraction tasks."""
    return build_llm(temperature=temperature)


def _empty_policy_result() -> dict[str, Any]:
    return {
        "require_review": False,
        "triggered": False,
        "reasons": [],
        "triggered_conditions": [],
    }


def _validate_policy(context: dict[str, Any]) -> dict[str, Any]:
    """Validate structured data against guardrail policy rules when configured."""
    guardrails_client = getattr(app._runtime, "guardrails_client", None)
    if guardrails_client is None:
        return _empty_policy_result()

    try:
        from runner_shared.context import get_validation_context

        return guardrails_client.validate_policy(context, context=get_validation_context())
    except Exception as exc:  # noqa: BLE001 - fail-open by design for demos
        logger.warning("Policy validation failed: %s", exc)
        return _empty_policy_result()


def _save_summary_episode(
    *,
    title: str,
    content: str,
    summary: str,
    user_id: str,
    session_id: str,
    participants: list[str],
    tags: list[str],
    visibility: str,
) -> str | None:
    """Upsert the per-session summary episode using a stable snapshot key."""
    memory_engine = _memory_engine()
    if memory_engine is None:
        logger.warning("Memory not enabled, cannot save episodic summary")
        return None

    try:
        existing = memory_engine.get_episodic(
            org_id=ORG_ID,
            session_id=session_id,
            snapshot_ref_id=SESSION_SUMMARY_SNAPSHOT_REF_ID,
        )
        if existing is not None:
            updated = memory_engine.update_episodic(
                org_id=ORG_ID,
                session_id=session_id,
                snapshot_ref_id=SESSION_SUMMARY_SNAPSHOT_REF_ID,
                title=title,
                content=content,
                summary_text=summary,
                visibility=visibility,
                tags=tags,
            )
            return str(getattr(updated, "id", "")) or None

        created = memory_engine.create_episodic(
            title=title,
            content=content,
            summary_text=summary,
            org_id=ORG_ID,
            user_id=user_id,
            session_id=session_id,
            snapshot_ref_id=SESSION_SUMMARY_SNAPSHOT_REF_ID,
            summary_type="llm",
            source_agent="pricing-analyst-agent",
            participants=participants,
            tags=tags,
            visibility=visibility,
        )
        return str(getattr(created, "id", "")) or None
    except Exception as exc:  # noqa: BLE001 - best effort memory write
        logger.warning("Failed to save episodic summary: %s", exc)
        return None


def _save_procedure_record(
    *,
    procedure: str,
    description: str,
    content: str,
    user_id: str,
    steps: list[dict[str, Any]] | None = None,
    allowed_tools: list[str] | None = None,
    tags: list[str] | None = None,
    visibility: str = "org",
    update_existing: bool = True,
) -> str | None:
    """Persist a procedural memory via the underlying MemoryEngine."""
    memory_engine = _memory_engine()
    if memory_engine is None:
        logger.warning("Memory not enabled, cannot save procedure")
        return None

    try:
        result = memory_engine.create_procedural(
            procedure=procedure,
            description=description,
            content=content,
            org_id=ORG_ID,
            user_id=user_id,
            steps=steps,
            allowed_tools=allowed_tools,
            tags=tags,
            visibility=visibility,
        )
        return str(getattr(result, "id", "")) or None
    except Exception as exc:  # noqa: BLE001 - duplicate/update behavior is best effort
        if "duplicate" not in str(exc).lower():
            logger.warning("Failed to save procedural memory: %s", exc)
            return None
        if not update_existing:
            logger.info("Procedure '%s' exists already; skipping automatic overwrite", procedure)
            return None

    try:
        updated = memory_engine.update_procedural(
            org_id=ORG_ID,
            procedure=procedure,
            description=description,
            content=content,
            steps=steps,
            allowed_tools=allowed_tools,
            tags=tags,
            visibility=visibility,
        )
        return str(getattr(updated, "id", "")) or procedure
    except Exception as exc:  # noqa: BLE001 - best effort update
        logger.warning("Failed to update procedural memory '%s': %s", procedure, exc)
        return None


def _discover_procedures(
    *,
    query: str,
    user_id: str,
    top_k: int = 3,
    similarity_threshold: float = 0.75,
) -> list[dict[str, Any]]:
    """Return lightweight procedural matches for the given query."""
    memory_engine = _memory_engine()
    if memory_engine is None:
        return []

    try:
        matches = memory_engine.discover_procedures(
            query=query,
            org_id=ORG_ID,
            user_id=user_id,
            top_k=top_k,
            similarity_threshold=similarity_threshold,
        )
    except Exception as exc:  # noqa: BLE001 - memory lookup should fail open
        logger.warning("Failed to discover procedures: %s", exc)
        return []

    return [match for match in matches if isinstance(match, dict)]


def _get_procedure(procedure_name: str) -> dict[str, Any] | None:
    """Load a stored procedure document from procedural memory."""
    memory_engine = _memory_engine()
    if memory_engine is None:
        return None

    try:
        procedure = memory_engine.get_procedural(org_id=ORG_ID, procedure=procedure_name)
    except Exception as exc:  # noqa: BLE001 - memory lookup should fail open
        logger.warning("Failed to load procedure '%s': %s", procedure_name, exc)
        return None

    if procedure is None:
        return None

    steps_raw = getattr(procedure, "steps", None) or []
    steps: list[dict[str, Any]] = []
    for step in steps_raw:
        if isinstance(step, dict):
            steps.append(cast(dict[str, Any], step))
            continue
        model_dump = getattr(step, "model_dump", None)
        if callable(model_dump):
            steps.append(cast(dict[str, Any], model_dump()))
    return {
        "procedure": getattr(procedure, "procedure", procedure_name),
        "description": getattr(procedure, "description", ""),
        "content": getattr(procedure, "content", ""),
        "steps": steps,
        "tags": getattr(procedure, "tags", []) or [],
        "allowed_tools": getattr(procedure, "allowed_tools", []) or [],
        "version": getattr(procedure, "version", 1),
    }


def _build_memory_context_response(
    *,
    query: str,
    user_id: str,
    session_id: str,
) -> Any | None:
    """Build rich memory context, including selected memories for citations."""
    memory_engine = _memory_engine()
    if memory_engine is None or not session_id:
        return None

    try:
        return memory_engine.build_context(
            query=query,
            session_id=session_id,
            org_id=ORG_ID,
            user_id=user_id,
            include_memories=True,
        )
    except Exception as exc:  # noqa: BLE001 - memory recall should fail open
        logger.warning("Failed to build memory context: %s", exc)
        return None


# =============================================================================
# Tools - Using @app.tool for secure, logged execution
# =============================================================================

lookup_coupa_costs = app.tool(is_local=False)(pricing_tools.lookup_coupa_costs)
search_deal_history = app.tool(is_local=False)(pricing_tools.search_deal_history)
calculate_pricing = app.tool(is_local=False)(pricing_tools.calculate_pricing)
plot_data = app.tool(is_local=False)(pricing_tools.plot_data)


def _parse_contract_categories(categories_json: str) -> list[dict[str, Any]]:
    """Parse and validate the contract categories payload."""
    try:
        categories = json.loads(categories_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid categories_json: {e!s}") from e

    if not isinstance(categories, list):
        raise ValueError("categories_json must be a JSON array")

    return categories


def _analyze_contract_terms(
    categories: list[dict[str, Any]], contract_duration_years: int
) -> dict[str, Any]:
    """Build normalized contract terms and policy review metadata."""
    all_categories = []
    triggered_categories = []
    all_guardrail_reasons: list[str] = []
    max_triggered_margin = 0.0

    for cat in categories:
        cat_name = cat.get("category", "Unknown")
        target_margin = cat.get("target_margin_pct", 0)
        current_margin = cat.get("current_margin_pct", 0)
        list_price = cat.get("list_price", 0)
        discount = cat.get("proposed_discount_pct", 0)
        deal_value = round(list_price * (1 - discount / 100), 3)

        guardrail_result = _validate_policy(
            {
                "margin_pct": target_margin,
                "discount_pct": discount,
                "deal_value": deal_value,
                "contract_duration_years": contract_duration_years,
            }
        )

        triggered = bool(guardrail_result.get("require_review"))
        cat_entry = {
            "category": cat_name,
            "target_margin_pct": target_margin,
            "current_margin_pct": current_margin,
            "list_price": list_price,
            "proposed_discount_pct": discount,
            "deal_value": deal_value,
            "triggered": triggered,
        }
        all_categories.append(cat_entry)

        if triggered:
            triggered_categories.append(cat_name)
            reasons = guardrail_result.get("reasons", [])
            for reason in reasons:
                all_guardrail_reasons.append(f"{cat_name}: {reason}")
            if target_margin > max_triggered_margin:
                max_triggered_margin = target_margin

    return {
        "all_categories": all_categories,
        "triggered_categories": triggered_categories,
        "guardrail_reasons": all_guardrail_reasons,
        "risk_level": "high" if max_triggered_margin >= 6 else "medium",
    }


@app.tool(is_local=True)
def finalize_contract(
    customer_name: str,
    categories_json: str,
    contract_duration_years: int,
    deal_summary: str,
) -> str:
    """Finalize a multi-category contract for a customer.

    Validates every category against policy guardrails before finalizing.
    If any category exceeds policy thresholds (e.g., margin >= 6%), the
    entire contract is SUSPENDED for manager review via human-in-the-loop.

    After manager approval, the execution resumes and the contract is finalized.
    After rejection, the tool returns the rejection reason.

    Args:
        customer_name: Customer for this contract (e.g., "MedPoint Pharmacies")
        categories_json: JSON array of category objects, each with keys:
            category (str), target_margin_pct (float), current_margin_pct (float),
            list_price (float), proposed_discount_pct (float).
            Example: [{"category": "Generic Analgesic", "target_margin_pct": 6.0,
            "current_margin_pct": 5.1, "list_price": 14.20, "proposed_discount_pct": 10.3}]
        contract_duration_years: Contract term in years (e.g., 2)
        deal_summary: Summary of the full proposed deal covering all categories
    """
    try:
        categories = _parse_contract_categories(categories_json)
    except ValueError as e:
        return json.dumps({"status": "error", "message": str(e)})

    task_id = f"CONTRACT-{uuid.uuid4().hex[:8].upper()}"
    analysis = _analyze_contract_terms(categories, contract_duration_years)
    triggered_categories = analysis["triggered_categories"]

    if triggered_categories:
        risk_level = analysis["risk_level"]

        logger.info(
            "Guardrail triggered for contract finalization: triggered_categories=%s, reasons=%s",
            triggered_categories,
            analysis["guardrail_reasons"],
        )

        return SuspendPayload(
            suspend_reason="guardrail_triggered",
            suspend_context={
                "task_id": task_id,
                "decision_type": "contract_finalization",
                "customer_name": customer_name,
                "contract_duration_years": contract_duration_years,
                "deal_summary": deal_summary,
                "all_categories": analysis["all_categories"],
                "triggered_categories": triggered_categories,
                "risk_level": risk_level,
                "guardrail_triggered": True,
                "guardrail_reasons": analysis["guardrail_reasons"],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "instructions": f"Guardrail triggered for {', '.join(triggered_categories)}. Review and approve or reject.",
            },
        ).to_json()

    logger.info(
        "Contract finalized without guardrail trigger: customer=%s, categories=%s",
        customer_name,
        [c["category"] for c in analysis["all_categories"]],
    )

    return json.dumps(
        {
            "status": "finalized",
            "task_id": task_id,
            "customer_name": customer_name,
            "contract_duration_years": contract_duration_years,
            "all_categories": analysis["all_categories"],
            "deal_summary": deal_summary,
            "message": f"Contract finalized for {customer_name} covering {len(analysis['all_categories'])} categories.",
        },
        indent=2,
    )


# NOTE: Temp, should use mongomem's extraction service but for now we'll use
# this as a placeholder for demo.
def save_conversation_summary(
    title: str,
    summary: str,
    user_id: str,
    session_id: str,
    tags: str = "",
) -> str:
    """Save a summary of the current conversation for future reference.

    Use this to capture key insights from deal discussions, pricing analyses,
    and customer interactions for recall in future sessions.

    Uses a deterministic ``snapshot_ref_id`` so mongomem's unique index
    ``(org_id, session_id, snapshot_ref_id)`` enforces exactly one episodic
    document per session.  Subsequent calls for the same session upsert the
    existing document with the latest summary.

    Args:
        title: Brief title for this conversation (e.g., "MedPoint 2-year renewal draft")
        summary: Summary of what was discussed and any outcomes (customer, deal terms, margins, insights).
        tags: Comma-separated tags for categorization (e.g., "renewal,medpoint,generic_analgesic").
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    episode_id = _save_summary_episode(
        title=title,
        content=summary,
        summary=summary,
        participants=["Pricing Analyst", "Pricing Analyst Agent (AI)"],
        tags=tag_list,
        user_id=user_id,
        session_id=session_id,
        visibility="private",
    )

    if episode_id:
        return json.dumps(
            {
                "status": "saved",
                "episode_id": episode_id,
                "title": title,
                "tags": tag_list,
                "message": "Conversation summary saved for future reference.",
            },
            indent=2,
        )

    return json.dumps(
        {
            "status": "error",
            "message": "Failed to save conversation summary. Memory may not be enabled.",
        },
        indent=2,
    )


def _comma_separated_tags(tags: str) -> list[str]:
    return [tag.strip() for tag in tags.split(",") if tag.strip()]


_TOOLS_EXCLUDED_FROM_PROCEDURES = {"finalize_contract"}
_PROCEDURE_TOOL_NAMES = (
    "lookup_coupa_costs",
    "search_deal_history",
    "calculate_pricing",
    "plot_data",
)


def _normalize_tool_list(tool_names: Any) -> Optional[list[str]]:
    """Normalize tool name collections while preserving order."""
    if tool_names is None:
        return None

    if isinstance(tool_names, str):
        raw_names = [tool_names]
    elif isinstance(tool_names, (list, tuple, set)):
        raw_names = list(tool_names)
    else:
        return None

    seen: dict[str, None] = {}
    for name in raw_names:
        normalized = str(name).strip()
        if (
            normalized
            and normalized not in _TOOLS_EXCLUDED_FROM_PROCEDURES
            and normalized not in seen
        ):
            seen[normalized] = None
    return list(seen) or None


def _infer_step_allowed_tools(step_content: str, fallback: Any = None) -> Optional[list[str]]:
    """Infer per-step tool access from the extracted step instructions."""
    inferred = [
        tool_name
        for tool_name in _PROCEDURE_TOOL_NAMES
        if re.search(
            rf"(?<![A-Za-z0-9_])`?{re.escape(tool_name)}`?(?![A-Za-z0-9_])",
            step_content or "",
        )
    ]
    return _normalize_tool_list(inferred or fallback)


def _collect_step_allowed_tools(steps: Optional[list[dict[str, Any]]]) -> Optional[list[str]]:
    """Union together the tool requirements captured on each step."""
    if not steps:
        return None

    tool_names: list[str] = []
    for step in steps:
        tool_names.extend(step.get("allowed_tools") or [])
    return _normalize_tool_list(tool_names)


def _procedure_steps_from_raw(raw_steps: Any) -> Optional[list[dict[str, Any]]]:
    """Normalize JSON step lists into procedural step dicts for mongomem."""
    if not raw_steps:
        return None

    normalized: list[dict[str, Any]] = []
    for step in raw_steps:
        if not isinstance(step, dict):
            continue

        content = str(step.get("content", "")).strip()
        if not content:
            continue

        step_allowed_tools = _infer_step_allowed_tools(
            content,
            step.get("allowed_tools"),
        )
        normalized_step: dict[str, Any] = {
            "step_type": step.get("step_type", "instruction"),
            "description": step.get("description", ""),
            "content": content,
        }
        if step_allowed_tools:
            normalized_step["allowed_tools"] = step_allowed_tools
        normalized.append(normalized_step)

    return normalized or None


def _strip_finalize_from_procedure(
    steps: Optional[list[dict[str, Any]]],
    allowed_tools: Optional[list[str]],
) -> tuple[Optional[list[dict[str, Any]]], Optional[list[str]]]:
    """Remove finalize_contract from procedure steps and allowed_tools.

    Procedures should only contain analysis steps so the user sees the
    recommendation before deciding to finalize via the normal free-form path.
    """
    allowed_tools = _normalize_tool_list(allowed_tools)

    if steps:
        filtered_steps: list[dict[str, Any]] = []
        for step in steps:
            if any(
                excluded in step.get("content", "") for excluded in _TOOLS_EXCLUDED_FROM_PROCEDURES
            ):
                continue

            normalized_step = dict(step)
            normalized_step_tools = _normalize_tool_list(normalized_step.get("allowed_tools"))
            if normalized_step_tools:
                normalized_step["allowed_tools"] = normalized_step_tools
            else:
                normalized_step.pop("allowed_tools", None)
            filtered_steps.append(normalized_step)

        steps = filtered_steps or None

    return steps, allowed_tools


def _normalize_loaded_procedure(procedure: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Backfill step-scoped tool metadata for stored procedures."""
    if not procedure:
        return procedure

    normalized = dict(procedure)
    normalized_steps = _procedure_steps_from_raw(normalized.get("steps")) or []
    normalized["steps"] = normalized_steps

    procedure_allowed_tools = _normalize_tool_list(normalized.get("allowed_tools"))
    if normalized_steps:
        normalized["allowed_tools"] = (
            _collect_step_allowed_tools(normalized_steps) or procedure_allowed_tools or []
        )
    else:
        normalized["allowed_tools"] = (
            _infer_step_allowed_tools(str(normalized.get("content", "")), procedure_allowed_tools)
            or []
        )
    return normalized


@app.tool(is_local=True)
def save_procedure(
    procedure_name: str,
    description: str,
    steps_markdown: str,
    steps_json: str = "[]",
    tags: str = "",
    state: Annotated[dict, InjectedState] = None,  # type: ignore[assignment]
) -> str:
    """Save the workflow from the current conversation as a reusable procedure.

    Use this when the user asks to save, encode, or remember the steps/workflow
    followed in the session. The procedure is stored org-wide for future reuse.
    """
    user_id = get_current_user_id()
    tag_list = _comma_separated_tags(tags) if tags else []

    steps: Optional[list] = None
    try:
        raw_steps = (
            json.loads(steps_json) if steps_json and steps_json.strip() not in ("[]", "") else []
        )
        steps = _procedure_steps_from_raw(raw_steps)
    except (json.JSONDecodeError, AttributeError):
        logger.warning("save_procedure: failed to parse steps_json, saving without steps")

    messages: list = (state or {}).get("messages", [])
    allowed_tools = _collect_step_allowed_tools(steps)
    if not allowed_tools:
        allowed_tools = _extract_used_tools(messages) or None
    steps, allowed_tools = _strip_finalize_from_procedure(steps, allowed_tools)
    if steps:
        allowed_tools = _collect_step_allowed_tools(steps) or allowed_tools

    procedure_id = _save_procedure_record(
        procedure=procedure_name,
        description=description,
        content=steps_markdown,
        user_id=user_id or "unknown",
        steps=steps,
        allowed_tools=allowed_tools,
        tags=tag_list,
        visibility="org",
    )

    if procedure_id:
        return json.dumps(
            {
                "status": "saved",
                "procedure_id": procedure_id,
                "procedure": procedure_name,
                "description": description,
                "tags": tag_list,
                "steps_saved": len(steps) if steps else 0,
                "message": (
                    f"Procedure '{procedure_name}' saved org-wide with "
                    f"{len(steps) if steps else 0} structured step(s)."
                ),
            },
            indent=2,
        )

    return json.dumps(
        {
            "status": "error",
            "message": "Failed to save procedure. Memory may not be enabled.",
        },
        indent=2,
    )


def _build_summary_conversation_text(messages: list[BaseMessage]) -> str:
    """Create a compact transcript for summary extraction."""
    lines: list[str] = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            lines.append(f"User: {content[:300]}")
            continue

        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            if content:
                lines.append(f"Agent: {content[:300]}")

    return "\n".join(lines)


def _parse_summary_response(raw_content: Any) -> dict[str, str]:
    """Parse the summary model response into title/summary/tags fields."""
    raw = raw_content
    if isinstance(raw, list):
        raw = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in raw
        )

    if not isinstance(raw, str):
        raw = str(raw)

    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", raw, re.DOTALL)
    if fence_match:
        raw = fence_match.group(1)

    parsed = json.loads(raw)
    title = str(parsed["title"]).strip()
    summary = str(parsed["summary"]).strip()
    tags = str(parsed.get("tags", "")).strip()

    if not title or not summary:
        raise ValueError("Summary response must contain non-empty title and summary")

    return {"title": title, "summary": summary, "tags": tags}


def _should_extract_conversation_summary(
    *,
    has_tool_calls: bool,
    human_count: int,
    user_id: Optional[str],
    session_id: Optional[str],
    memory_enabled: bool,
) -> bool:
    """Determine whether this turn should enqueue summary extraction."""
    return (
        not has_tool_calls
        and human_count >= SUMMARY_MIN_HUMAN_TURNS
        and bool(user_id)
        and bool(session_id)
        and memory_enabled
    )


def _should_extract_conversation_procedure(
    *,
    has_tool_calls: bool,
    human_count: int,
    user_id: Optional[str],
    session_id: Optional[str],
    memory_enabled: bool,
) -> bool:
    """Determine whether this turn should enqueue procedural extraction."""
    return (
        not has_tool_calls
        and human_count >= SUMMARY_MIN_HUMAN_TURNS
        and bool(user_id)
        and bool(session_id)
        and memory_enabled
    )


def _generate_and_save_conversation_summary(
    messages: list[BaseMessage],
    user_id: str,
    session_id: str,
) -> None:
    """Best-effort summary extraction that runs off the user-facing path."""
    conv_text = _build_summary_conversation_text(messages)
    if not conv_text:
        logger.info("Skipping conversation summary extraction: transcript was empty")
        return

    try:
        llm = _background_llm(temperature=0)
        summary_resp = llm.invoke(
            [
                SystemMessage(
                    content=(
                        "Summarize this pricing analyst conversation focusing on actionable insights for future deals. "
                        "Cover: (1) customer name and categories, (2) final agreed terms (margins, discounts), "
                        "(3) negotiation dynamics — what the customer pushed hardest on and where we held firm, "
                        "(4) strategy takeaways — what levers worked (e.g., offering room on generics to protect specialty margins), "
                        "(5) risk patterns — any margin thresholds that triggered guardrail reviews or showed higher rejection rates. "
                        "Write as if briefing a colleague about to negotiate with a DIFFERENT customer in the same Tier. "
                        "Also produce a short title (<=10 words) and comma-separated tags. "
                        'Respond as JSON: {"title": "...", "summary": "...", "tags": "..."}'
                    )
                ),
                HumanMessage(content=conv_text),
            ]
        )
        logger.info("SAVE-DEBUG: summary_resp.content=%.500s", summary_resp.content)
        parsed = _parse_summary_response(summary_resp.content)
        save_conversation_summary(
            title=parsed["title"],
            summary=parsed["summary"],
            tags=parsed["tags"],
            user_id=user_id,
            session_id=session_id,
        )
        logger.info("Conversation summary saved for session %s", session_id)
    except Exception:
        logger.warning("Failed to save conversation summary", exc_info=True)


def _queue_conversation_summary(messages: list[BaseMessage], user_id: str, session_id: str) -> None:
    """Queue extracted episodic memory work without delaying the response."""
    logger.info("Queueing background conversation summary for session %s", session_id)
    _SUMMARY_EXECUTOR.submit(
        _generate_and_save_conversation_summary, list(messages), user_id, session_id
    )


def _generate_and_save_procedural_memory(
    messages: list[BaseMessage],
    user_id: str,
    session_id: str,
) -> None:
    """Best-effort procedural extraction that runs off the user-facing path."""
    conversation_text = _build_conversation_text(messages, include_tool_calls=True)
    if not conversation_text:
        logger.info("Skipping procedural extraction: transcript was empty")
        return

    used_tools = _extract_used_tools(messages) or None

    try:
        llm = _background_llm(temperature=0)
        response = llm.invoke(
            [
                SystemMessage(content=PROCEDURAL_EXTRACTION_PROMPT),
                HumanMessage(content=conversation_text),
            ]
        )
        parsed = json.loads(_extract_json_str(response.content))
        if not parsed.get("procedure"):
            logger.info("No reusable procedure extracted for session %s", session_id)
            return

        raw_steps = parsed.get("steps") or []
        steps = _procedure_steps_from_raw(raw_steps)
        used_tools = _collect_step_allowed_tools(steps) or used_tools
        steps, used_tools = _strip_finalize_from_procedure(steps, used_tools)
        if steps:
            used_tools = _collect_step_allowed_tools(steps) or used_tools
        _save_procedure_record(
            procedure=parsed["procedure"],
            description=parsed.get("description", ""),
            content=parsed.get("content", ""),
            user_id=user_id or "unknown",
            steps=steps,
            allowed_tools=used_tools,
            tags=_comma_separated_tags(parsed.get("tags", "")),
            visibility="org",
            update_existing=False,
        )
        logger.info(
            "Auto-extracted procedural memory for session %s: %s (%d steps)",
            session_id,
            parsed["procedure"],
            len(steps) if steps else 0,
        )
    except Exception:
        logger.warning("Failed to auto-extract procedural memory", exc_info=True)


def _queue_procedural_memory(messages: list[BaseMessage], user_id: str, session_id: str) -> None:
    """Queue procedural extraction work without delaying the response."""
    logger.info("Queueing background procedural extraction for session %s", session_id)
    _PROCEDURE_EXECUTOR.submit(
        _generate_and_save_procedural_memory, list(messages), user_id, session_id
    )


# =============================================================================
# Agent Definition
# =============================================================================


class _AgentStateOptional(TypedDict, total=False):
    user_id: Optional[str]
    session_id: Optional[str]
    memory_context: str
    citation_index: str
    current_procedure: Optional[dict]
    procedure_request: str
    procedure_step_index: int
    step_tool_calls: int
    step_results: list[dict]
    procedure_artifacts: dict[str, dict[str, Any]]


class AgentState(_AgentStateOptional):
    messages: Annotated[list[BaseMessage], add_messages]


SYSTEM_PROMPT = """You are a Pricing Analyst assistant for pharmaceutical and healthcare deal drafting. \
You help with contract renewals, margin targets, cost breakdowns, and pricing recommendations.

## Your Capabilities
- **lookup_coupa_costs**: Look up current COGS and cost breakdown (materials, shipping, labor) for a product category (e.g. Generic Analgesic, Specialty Oncology, OTC Respiratory). Use this to get raw cost components and explain cost levers (e.g. alternate suppliers for generics).
- **calculate_pricing**: Compute net price, total COGS, margin dollars, and margin percentage for one or more categories. Pass a JSON array of objects with category, list_price, discount_pct, materials_cost, shipping_cost, labor_cost. Returns exact computed values. **Always use this tool for margin and pricing arithmetic — never calculate margins or percentages in-text.**
- **search_deal_history**: Search past deals for a customer to get prior list/discount/net/margin by category. Use for "vs. Last" comparisons and renewal drafting.
- **plot_data**: Create line or bar charts from JSON data. **Always call this tool to generate charts — never write chart specifications, data arrays, or plot parameters as text in your response.** The tool renders the chart image automatically in the UI.
- **finalize_contract**: Finalize a contract with specified terms. This tool checks policy guardrails before finalizing. If guardrails trigger, execution is suspended for manager review and resumes automatically after approval.
- **save_procedure**: Save the workflow from the current conversation as a reusable, org-wide procedure for future use.

## Margin Calculation Rule
**NEVER compute margin percentages, net prices, or COGS totals in-text.** Always call `calculate_pricing` with the raw cost components. Use the exact values returned by the tool in your tables and analysis. This applies to every scenario: single-category breakdowns, multi-category renewal tables, what-if comparisons (e.g. shipping consolidation), and any other pricing arithmetic.

## Deal drafting (renewals, margin targets)
- When the user asks for pricing and margin targets for a renewal, use memory context for prior deals and negotiation patterns, then call lookup_coupa_costs for each category to get current list price and COGS. Then call calculate_pricing with all categories to get exact margins. Build a table with columns: Category, List, Disc., Net, Margin, vs. Last using the values returned by calculate_pricing. Cite memories for negotiation tips and risks. When lookup_coupa_costs returns YoY shipping changes, include a separate "Shipping Cost Trends" table after the pricing table with columns: Category, Shipping YoY, Prior Shipping, Current Shipping. When presenting cost reduction levers for multi-category proposals, mention shipping consolidation options per category (from the shipping_logistics field in lookup_coupa_costs) so the full picture is established upfront.
- When the user asks why a margin is at a certain level or what levers exist, call lookup_coupa_costs for that category to get the cost breakdown, then call calculate_pricing to compute the exact margin. Use those values to explain COGS breakdown and levers (reduce discount, alternate supplier, or shipping frequency consolidation if shipping_logistics data is available). If memory context mentions rejection rate patterns for margins above certain thresholds, surface that warning.
- When drafting renewal proposals, default margin targets to the most recent historical margin for each category unless the user explicitly requests a different target. For every category with a YoY shipping cost increase, add +0.2% to +0.4% above the prior margin to offset cost pressure, and reflect that as a positive vs. Last value in the table.

## Response Format
Always format your responses in **markdown**:
- **Tables**: Use markdown tables for pricing tables, cost breakdowns, and deal comparisons.
- **Lists**: Use bullet or numbered lists for options, levers, or steps.
- **Emphasis**: Use **bold** for key figures (prices, margins, percentages).
- **Headings**: Use ## or ### for distinct sections.

## How to Help
- When memory context is provided, use it for relevant account and deal context. Call tools when you need current COGS (lookup_coupa_costs) or structured deal history (search_deal_history).
- For renewal or "price this deal" questions: combine memory + lookup_coupa_costs (and optionally search_deal_history) to produce a pricing table and narrative.
- For "why is margin X" or "can we get to Y%": use lookup_coupa_costs and memory to explain breakdown and levers.
- When the user asks to plot, chart, graph, or visualize data, ALWAYS call the `plot_data` tool. Never output chart data or parameters inline.

Be professional and data-driven.

## Citation Format
When your response uses data from the background knowledge or tool results, add numbered footnote \
markers [1], [2], etc. at the end of the relevant sentence or data point.

At the END of your response, add a divider and a **Sources** section listing each cited source as a bullet list. Format:

---
**Sources**
- [1] Source System — Description
- [2] Source System — Description

Rules:
- Only cite sources from the Available Sources index provided below or from tool call results.
- For tool results, cite as: [N] Coupa Procurement — {category} Cost Lookup  or  [N] Deal Management — {customer} Deal History
- NEVER use retrieval section headers or memory-type labels as source names. Forbidden source names include: "Semantic Memory", "Taxonomic Memory", "Episodic Memory", "Retrieved Data", "Memory Context", "Background Context". Always use the exact source line from Available Sources (e.g. [1] Account Master — Label).
- When citing data inside a table, place the footnote marker inline in the cell (e.g. $2.84M [1]), not on a separate line.
- Do NOT place citation markers in table column headers. Place them in the data cells instead.
- Do NOT cite a source you did not actually use.
- Number sources sequentially starting from [1].
- Place the Sources section at the very end, after all analysis and tables.

WRONG (never do this):
---
**Sources**
- [1] Semantic Memory — Some Data Label
- [2] Episodic Memory — Some Conversation Title

CORRECT (always do this — use the Available Sources line exactly):
---
**Sources**
- [1] Account Master — Customer Account Profile
- [2] Deal History — Customer Past Pricing Context

## Contract Finalization
When the user asks to "finalize", "commit", "approve", or "lock in" a contract or deal:
1. Summarize the proposed terms for ALL categories in the contract (customer, each category's discount, margin, duration)
2. Call `finalize_contract` with the full contract:
   - customer_name: the customer
   - categories_json: a JSON array string containing ALL categories in the contract. Each object must have:
     category, target_margin_pct, current_margin_pct, list_price, proposed_discount_pct
   - contract_duration_years: the contract term
   - deal_summary: a brief summary covering ALL categories and the overall deal
3. Include EVERY category in the contract (not just the one the user mentioned). The tool validates all categories against policy guardrails.
4. After manager approval, confirm the contract is finalized with a full summary of ALL approved terms
5. After rejection, explain the rejection reason and suggest adjustments based on the manager's notes

## Saving Workflows as Procedures
When the user asks to "save this workflow", "encode these steps", "remember this process", \
or "save as a procedure", call `save_procedure` with:
- A kebab-case procedure name derived from the workflow (e.g., "tier-1-pharmacy-renewal")
- A one-line description of what the procedure does and when to use it
- `steps_markdown`: a high-level narrative overview of the full workflow (markdown prose)
- `steps_json`: a JSON array of structured step objects mirroring the distinct actions \
taken in this conversation. Each step must have:
  - "step_type": "instruction"
  - "description": short label
  - "content": agent-executable directions that explicitly name the tool calls used
- Relevant tags for discoverability

IMPORTANT: `steps_json` drives future automated execution, so it must accurately reflect \
the tool calls and workflow steps that actually happened in the conversation.

## Cross-Customer Learnings
When memory context includes episodic memories from prior deals with OTHER customers:
- Explicitly cite the prior deal as a reference point
- ADAPT the insights to the current customer's profile — consider differences in account type, \
volume, specialty mix, and negotiation patterns rather than copying prior deal terms verbatim
- Transfer relevant strategy patterns and negotiation levers that worked in prior deals
- Flag risk patterns from prior deals that may apply (e.g., margin thresholds that triggered reviews)
- When both prior-deal insights and current-customer data are available, highlight how the \
current customer's profile leads to different recommendations

## Important
Do NOT end your responses with follow-up questions, suggestions, or prompts like "Would you like me to…?" or "Shall I…?". \
Deliver your analysis and stop. The user will ask if they need more.
"""


_MEMORY_TYPE_HEADER_RE = re.compile(
    r"^#{1,3}\s*(?:Semantic Memory|Taxonomic Memory|Episodic Memory|Procedural Memory)\s*$",
    re.MULTILINE,
)


def _sanitize_memory_context(text: str) -> str:
    """Strip memory-type section headers so the LLM can't latch onto them as source names."""
    return _MEMORY_TYPE_HEADER_RE.sub("", text)


def _renumber_citations(text: str) -> str:
    """Renumber citation markers to be sequential starting from [1].

    Handles both single [N] and grouped [N, M, ...] brackets with a
    single shared mapping so the same original number always maps to
    the same new number regardless of context.
    """
    seen: dict[str, str] = {}
    next_num = 1

    def _assign(old: str) -> str:
        nonlocal next_num
        if old not in seen:
            seen[old] = str(next_num)
            next_num += 1
        return seen[old]

    def _replace_group(match: re.Match) -> str:
        inner = match.group(1)
        if "," in inner:
            nums = [n.strip() for n in inner.split(",")]
            return "[" + ", ".join(_assign(n) for n in nums) + "]"
        return f"[{_assign(inner)}]"

    return re.sub(r"\[(\d+(?:\s*,\s*\d+)*)\]", _replace_group, text)


_SOURCE_DISPLAY_NAMES = {
    "account_master": "Account Master",
    "deal_history": "Deal History",
    "deal_outcomes": "Deal Outcomes",
    "coupa_costs": "Coupa Costs",
    "data_ontology": "Data Ontology",
    "logistics_analysis": "Logistics Analysis",
}


def _build_citation_index(raw_memories: list) -> str:
    """Build a numbered citation index from raw mongomem MemoryChunk objects.

    MemoryChunk has top-level .source (memory type: "semantic", "taxonomic", etc.)
    and .metadata dict with nested fields that vary by source type.
    For semantic: metadata has "label" and "source" (data source).
    For taxonomic: metadata has "domain" and "term".
    """
    if not raw_memories:
        return ""
    seen = set()
    lines = []
    idx = 0
    for mem in raw_memories:
        meta = getattr(mem, "metadata", None) or {}
        mem_type = getattr(mem, "source", "")

        if mem_type == "semantic":
            src_key = meta.get("source", "")
            lbl_key = meta.get("label", "")
        elif mem_type == "taxonomic":
            src_key = "data_ontology"
            lbl_key = f"{meta.get('domain', '')}:{meta.get('term', '')}"
        else:
            continue

        dedup_key = (src_key, lbl_key)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        idx += 1
        src = _SOURCE_DISPLAY_NAMES.get(src_key, src_key)
        lbl = lbl_key.replace("_", " ").title()
        lines.append(f"[{idx}] {src} — {lbl}")
    return "\n".join(lines)


def _extract_latest_user_query(messages: list[BaseMessage]) -> str:
    """Extract the most recent user message content from the message list."""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return msg.content if isinstance(msg.content, str) else str(msg.content)
    return ""


_INTENT_SYSTEM_PROMPT = """\
You are deciding whether a pricing analyst's request should trigger a search for a \
saved procedure, or be answered directly.

Reply with only DISCOVER or SKIP.

DISCOVER — the user is kicking off a new, open-ended analysis where a saved workflow \
could apply. The request is broad enough that a multi-step procedure would be useful.

SKIP — the user is asking a specific question, issuing a command, or continuing work \
already in progress. Answer directly without looking for a procedure.
"""


def _should_discover_procedure(query: str, llm: BaseChatModel) -> bool:
    """Return True when a new analysis request should trigger procedure discovery."""
    try:
        response = llm.invoke(
            [
                SystemMessage(content=_INTENT_SYSTEM_PROMPT),
                HumanMessage(content=query),
            ]
        )
        return "DISCOVER" in str(response.content).upper()
    except Exception:
        logger.debug("Procedure intent classification failed, defaulting to DISCOVER")
        return True


def _procedure_matches_query(
    query: str,
    procedure_name: str,
    description: str,
    llm: BaseChatModel,
) -> bool:
    """Return True when the retrieved procedure should run end-to-end for the query."""
    try:
        response = llm.invoke(
            [
                SystemMessage(
                    content=(
                        "You are deciding whether to run a saved procedure for a user's request. "
                        "Reply with only YES or NO."
                    )
                ),
                HumanMessage(
                    content=(
                        f"Procedure: {procedure_name}\n"
                        f"When to use it: {description}\n\n"
                        f"User request: {query}\n\n"
                        "Should this procedure run? Reply YES or NO."
                    )
                ),
            ]
        )
        return "YES" in str(response.content).upper()
    except Exception:
        logger.debug("Procedure match classification failed, defaulting to NO")
        return False


def _extract_used_tools(messages: list[BaseMessage]) -> list[str]:
    """Collect the unique set of tool names called in the conversation."""
    seen: dict[str, None] = {}
    for msg in messages:
        for tool_call in getattr(msg, "tool_calls", None) or []:
            name = (
                tool_call.get("name", "")
                if isinstance(tool_call, dict)
                else getattr(tool_call, "name", "")
            )
            if name and name not in seen:
                seen[name] = None
    return list(seen)


def _build_conversation_text(messages: list[BaseMessage], include_tool_calls: bool = False) -> str:
    """Build a text representation of the conversation for LLM extraction."""
    parts: list[str] = []
    for message in messages:
        if isinstance(message, HumanMessage):
            content = (
                message.content[:300]
                if isinstance(message.content, str)
                else str(message.content)[:300]
            )
            parts.append(f"User: {content}")
        elif isinstance(message, AIMessage):
            tool_calls = getattr(message, "tool_calls", None)
            if tool_calls and include_tool_calls:
                for tool_call in tool_calls:
                    parts.append(
                        "Agent called tool: "
                        f"{tool_call.get('name', '?')}({json.dumps(tool_call.get('args', {}))[:200]})"
                    )
            elif message.content and not tool_calls:
                content = (
                    message.content[:300]
                    if isinstance(message.content, str)
                    else str(message.content)[:300]
                )
                parts.append(f"Agent: {content}")
        elif isinstance(message, ToolMessage) and include_tool_calls:
            name = getattr(message, "name", "tool")
            content = (
                message.content[:200]
                if isinstance(message.content, str)
                else str(message.content)[:200]
            )
            parts.append(f"Tool result ({name}): {content}")
    return "\n".join(parts)


def _extract_json_str(content: object) -> str:
    """Extract a JSON string from an LLM response, handling fenced blocks."""
    raw = content
    if isinstance(raw, list):
        raw = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in raw
        )
    if not isinstance(raw, str):
        raw = str(raw)

    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", raw, re.DOTALL)
    if fence_match:
        raw = fence_match.group(1)
    return raw


def _normalize_tool_args(args: Any) -> str:
    """Serialize tool arguments into a stable cache key."""
    try:
        return json.dumps(args if args is not None else {}, sort_keys=True, separators=(",", ":"))
    except TypeError:
        return json.dumps(str(args), sort_keys=True)


def _tool_call_signature(tool_name: str, args: Any) -> str:
    """Create a stable signature for duplicate-tool detection."""
    return f"{tool_name}:{_normalize_tool_args(args)}"


def _parse_tool_json(raw_content: str) -> Any:
    """Best-effort parse of JSON tool content for structured carry-forward state."""
    try:
        return json.loads(raw_content)
    except (TypeError, json.JSONDecodeError):
        return None


def _render_tool_args(args: Any) -> str:
    """Render tool args compactly for prompt context and logs."""
    rendered = _normalize_tool_args(args)
    return rendered if len(rendered) <= 120 else rendered[:117] + "..."


def _summarize_tool_artifact(tool_name: str, args: Any, raw_content: str, parsed: Any) -> str:
    """Condense cached tool outputs so later steps can reuse them."""
    if tool_name == "lookup_coupa_costs" and isinstance(parsed, dict):
        breakdown = parsed.get("breakdown") or {}
        category = ""
        if isinstance(args, dict):
            category = str(args.get("category", "")).strip()
        category = category or parsed.get("data_source", "category")
        return (
            f"{category}: list ${parsed.get('current_list_price')}, COGS ${parsed.get('cogs')}, "
            f"materials ${breakdown.get('materials')}, shipping ${breakdown.get('shipping')}, "
            f"labor ${breakdown.get('labor')}."
        )

    if tool_name == "search_deal_history" and isinstance(parsed, dict):
        customer = parsed.get("customer", "customer")
        deals = parsed.get("deals") or []
        return f"{customer}: {len(deals)} prior deal(s) available for comparison."

    if tool_name == "calculate_pricing" and isinstance(parsed, dict):
        results = parsed.get("results") or []
        snippets = [
            f"{result.get('category', 'Unknown')} margin {result.get('margin_pct')}%"
            for result in results[:3]
            if isinstance(result, dict)
        ]
        if snippets:
            return "; ".join(snippets) + "."

    if tool_name == "plot_data":
        return "Chart already generated for this data set."

    return raw_content[:240]


def _refresh_procedure_artifacts(
    messages: list[BaseMessage],
    existing_artifacts: Optional[dict[str, dict[str, Any]]] = None,
) -> dict[str, dict[str, Any]]:
    """Capture structured tool outputs so later procedure steps can reuse them."""
    artifacts = dict(existing_artifacts or {})
    tool_call_lookup: dict[str, dict[str, Any]] = {}

    for message in messages:
        if isinstance(message, AIMessage):
            for tool_call in getattr(message, "tool_calls", None) or []:
                if isinstance(tool_call, dict) and tool_call.get("id"):
                    tool_call_lookup[str(tool_call["id"])] = tool_call
            continue

        if not isinstance(message, ToolMessage):
            continue

        raw_content = message.content if isinstance(message.content, str) else str(message.content)
        tool_call = tool_call_lookup.get(str(getattr(message, "tool_call_id", "")))
        tool_name = getattr(message, "name", "") or (tool_call.get("name", "") if tool_call else "")
        args = tool_call.get("args", {}) if tool_call else {}
        signature = _tool_call_signature(tool_name, args)
        if not tool_name or signature in artifacts:
            continue

        parsed = _parse_tool_json(raw_content)
        artifacts[signature] = {
            "tool_name": tool_name,
            "args": args,
            "raw_content": raw_content[:1200],
            "parsed_content": parsed,
            "summary": _summarize_tool_artifact(tool_name, args, raw_content, parsed),
        }

    return artifacts


def _build_cached_artifact_section(
    artifacts: dict[str, dict[str, Any]],
    step_allowed_tools: Optional[list[str]],
) -> str:
    """Expose reusable structured artifacts to later steps."""
    if not artifacts:
        return ""

    relevant_lines: list[str] = []
    for artifact in artifacts.values():
        if step_allowed_tools is not None and artifact["tool_name"] not in step_allowed_tools:
            continue
        relevant_lines.append(
            f"- `{artifact['tool_name']}` {_render_tool_args(artifact['args'])}: {artifact['summary']}"
        )

    if not relevant_lines:
        return ""

    return "## Cached Tool Results From This Procedure\n" + "\n".join(relevant_lines[-8:]) + "\n\n"


def _resolve_step_allowed_tools(
    procedure: Optional[dict[str, Any]],
    step: Optional[dict[str, Any]],
) -> Optional[list[str]]:
    """Resolve the tool subset a single replayed step is allowed to use."""
    if step is None:
        return _normalize_tool_list((procedure or {}).get("allowed_tools"))

    explicit_step_tools = _normalize_tool_list(step.get("allowed_tools"))
    if explicit_step_tools:
        return explicit_step_tools

    inferred_step_tools = _infer_step_allowed_tools(step.get("content", ""))
    if inferred_step_tools:
        return inferred_step_tools

    return []


def _filter_duplicate_tool_calls(
    tool_calls: Sequence[Any],
    artifacts: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Drop exact duplicate tool calls when cached results already exist."""
    filtered_calls: list[dict[str, Any]] = []
    duplicate_summaries: list[str] = []

    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            filtered_calls.append(tool_call)
            continue

        signature = _tool_call_signature(tool_call.get("name", ""), tool_call.get("args", {}))
        artifact = artifacts.get(signature)
        if artifact is None:
            filtered_calls.append(tool_call)
            continue

        duplicate_summaries.append(
            f"- `{artifact['tool_name']}` {_render_tool_args(artifact['args'])}: {artifact['summary']}"
        )

    return filtered_calls, duplicate_summaries


def _copy_ai_message_with_tool_calls(
    message: AIMessage,
    tool_calls: list[dict[str, Any]],
) -> AIMessage:
    """Clone an AIMessage with a filtered tool-call set."""
    additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
    additional_kwargs.pop("tool_calls", None)
    return AIMessage(
        content=message.content,
        additional_kwargs=additional_kwargs,
        response_metadata=getattr(message, "response_metadata", {}),
        tool_calls=tool_calls,
        id=getattr(message, "id", None),
    )


PROCEDURAL_EXTRACTION_PROMPT = """\
Analyze this pricing analyst conversation and extract ONE reusable procedure \
(the primary workflow the analyst followed). Focus on the EXACT sequence of tool calls \
that were made and the analysis steps that could be repeated for similar deals.

Respond as JSON with these fields:
- "procedure": kebab-case name
- "description": one-line description of what this procedure does and when to use it
- "content": full step-by-step instructions as markdown
- "tags": comma-separated tags
- "steps": array of step objects, where each object has:
  - "step_type": always "instruction"
  - "description": short label for the step
  - "content": detailed instructions for the step including the exact tool calls to make

If no clear reusable procedure was demonstrated, respond with {"procedure": null}.
"""


STEP_INSTRUCTION_TEMPLATE = """\
[Executing step {step_num}/{total_steps}: {step_description}]

{step_content}

{request_section}{memory_section}{cached_artifacts_section}{previous_results_section}\
Use the available tools to complete this step. Once the tool results for this step \
are available, respond with a clear summary in markdown (tables, bold figures, headings). \
Do NOT make additional tool calls beyond what this step requires. \
Do NOT call the same tool again with identical arguments when cached results from this \
procedure are already available above. \
Do NOT end with follow-up questions.
"""

PROCEDURE_STEP_SYSTEM_PROMPT = """\
You are executing one step of a saved pricing-analyst procedure. \
Focus ONLY on completing the current step described in the instruction below. \
Call tools only if the step instruction explicitly requires them. \
Once you have the tool results needed for this step, respond with a text summary — \
do NOT issue further tool calls. Do NOT restart or broaden the analysis beyond this step. \
Format your response in markdown with tables, bold figures, and headings where appropriate.
"""


RESPOND_SYSTEM_PROMPT = """\
You are a Pricing Analyst assistant. You just completed the '{procedure}' procedure \
for the user's request. Below are the results from each step.

{step_summaries}

{memory_section}

Synthesize all step results into a single, cohesive response for the user. \
Use markdown with tables, bold figures, and headings. \
Cite specific data from the steps. Do NOT repeat raw tool outputs.
"""


def _emit_procedure_discovery_node(
    oe_url: str,
    execution_id: str,
    session_id: Optional[str],
    user_id: Optional[str],
    query: str,
    procedure_name: str,
    description: str,
    score: Optional[float],
    step_count: int,
    version: int,
    org_id: Optional[str] = None,
) -> None:
    """Emit a custom node execution for procedure discovery."""
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    base = {
        "execution_id": execution_id,
        "node_name": "procedure_discovery",
        "run_id": run_id,
        "session_id": session_id,
        "thread_id": session_id,
        "user_id": user_id,
        "org_id": org_id,
    }
    try:
        with httpx.Client(timeout=2.0) as client:
            client.post(
                f"{oe_url}/node/execution",
                json={**base, "status": "started", "timestamp": now, "inputs": {"query": query}},
            )
            client.post(
                f"{oe_url}/node/execution",
                json={
                    **base,
                    "status": "success",
                    "timestamp": now,
                    "description": f"Procedure: {procedure_name}",
                    "outputs": {
                        "procedure": procedure_name,
                        "description": description,
                        "similarity_score": score,
                        "steps": step_count,
                        "version": version,
                    },
                },
            )
    except Exception as e:
        logger.debug("Failed to emit procedure_discovery node event to OE: %s", e)


def _emit_memory_context_node(
    oe_url: str,
    execution_id: str,
    session_id: Optional[str],
    user_id: Optional[str],
    query: str,
    memory_context: str,
    duration_ms: float,
    org_id: Optional[str] = None,
) -> None:
    """Emit a custom node execution for recall_memory so it appears in the Executions tab with minimal inputs (query only)."""
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    payload_started: dict = {
        "execution_id": execution_id,
        "node_name": "recall_memory",
        "status": "started",
        "timestamp": started_at.isoformat(),
        "run_id": run_id,
        "session_id": session_id,
        "thread_id": session_id,
        "user_id": user_id,
        "org_id": org_id,
        "inputs": {"query": query},
    }
    payload_success: dict = {
        "execution_id": execution_id,
        "node_name": "recall_memory",
        "status": "success",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "session_id": session_id,
        "thread_id": session_id,
        "user_id": user_id,
        "org_id": org_id,
        "outputs": {"memory_context": memory_context},
        "duration_ms": duration_ms,
    }
    try:
        with httpx.Client(timeout=2.0) as client:
            client.post(f"{oe_url}/node/execution", json=payload_started)
            client.post(f"{oe_url}/node/execution", json=payload_success)
    except Exception as e:
        logger.debug("Failed to emit recall_memory node event to OE: %s", e)


@app.entrypoint
def build_agent(llm: Optional[BaseChatModel] = None) -> CompiledStateGraph:
    """Build the LangGraph agent with procedure-driven execution."""
    logger.info("Building LangGraph agent (procedure-driven)...")

    if llm is None:
        llm = app.llm(build_llm(temperature=0))

    tools = app.get_tools()
    llm_with_tools = llm.bind_tools(app.get_tool_schemas())

    def discover_procedure_node(state: AgentState) -> dict:
        messages = state["messages"]
        user_id = state.get("user_id") or ""
        session_id = state.get("session_id") or ""
        latest_query = _extract_latest_user_query(messages)
        memory_context = ""
        citation_index = ""
        current_procedure: Optional[dict] = None
        planning_message: Optional[AIMessage] = None

        if latest_query:
            logger.info(
                "Pricing analyst: building memory context for query: %.80s...", latest_query
            )
            t0 = time.perf_counter()
            context_result = _build_memory_context_response(
                query=latest_query,
                user_id=user_id,
                session_id=session_id or "",
            )
            duration_ms = (time.perf_counter() - t0) * 1000.0

            if context_result is not None and hasattr(context_result, "formatted_context"):
                memory_context = context_result.formatted_context
                raw_memories = getattr(context_result, "selected_memories", []) or []
                citation_index = _build_citation_index(raw_memories)
            elif context_result is not None:
                memory_context = context_result if isinstance(context_result, str) else ""

            oe_url = get_current_oe_url()
            execution_id = get_current_execution_id()
            if oe_url and execution_id:
                _emit_memory_context_node(
                    oe_url=oe_url,
                    execution_id=execution_id,
                    session_id=session_id or get_current_session_id(),
                    user_id=user_id or get_current_user_id(),
                    query=latest_query,
                    memory_context=memory_context,
                    duration_ms=duration_ms,
                    org_id=ORG_ID,
                )

        should_discover = bool(latest_query) and _should_discover_procedure(latest_query, llm)
        if should_discover and user_id:
            matches = _discover_procedures(
                query=latest_query,
                user_id=user_id,
                similarity_threshold=0.7,
            )
            if matches:
                best_match = matches[0]
                procedure_name = best_match["procedure"]
                procedure_description = best_match.get("description", "")
                if _procedure_matches_query(
                    latest_query,
                    procedure_name,
                    procedure_description,
                    llm,
                ):
                    current_procedure = _normalize_loaded_procedure(_get_procedure(procedure_name))

                if current_procedure:
                    steps = current_procedure.get("steps") or []
                    step_count = len(steps)
                    plan_lines = "\n".join(
                        f"{index + 1}. {step.get('description', f'Step {index + 1}')}"
                        for index, step in enumerate(steps)
                    )
                    planning_message = AIMessage(
                        content=(
                            f"Matched procedure **`{procedure_name}`** from memory "
                            f"(v{current_procedure.get('version', 1)}).\n\n"
                            f"**Plan — {step_count} step{'s' if step_count != 1 else ''}:**\n"
                            f"{plan_lines}\n\n"
                            "Working through each step now..."
                        )
                    )
                    disc_oe_url = get_current_oe_url()
                    disc_execution_id = get_current_execution_id()
                    if disc_oe_url and disc_execution_id:
                        _emit_procedure_discovery_node(
                            oe_url=disc_oe_url,
                            execution_id=disc_execution_id,
                            session_id=session_id or get_current_session_id(),
                            user_id=user_id or get_current_user_id(),
                            query=latest_query,
                            procedure_name=procedure_name,
                            description=procedure_description,
                            score=best_match.get("score"),
                            step_count=step_count,
                            version=current_procedure.get("version", 1),
                            org_id=ORG_ID,
                        )

        return {
            "messages": [planning_message] if planning_message else [],
            "memory_context": memory_context,
            "citation_index": citation_index,
            "current_procedure": current_procedure,
            "procedure_request": latest_query,
            "procedure_step_index": 0,
            "step_tool_calls": 0,
            "step_results": [],
            "procedure_artifacts": {},
        }

    MAX_TOOL_ROUNDS_PER_STEP = 6
    STEP_INSTRUCTION_MARKER = "[Executing step"

    def execute_step_node(state: AgentState) -> dict:
        procedure = state.get("current_procedure")
        index = state.get("procedure_step_index", 0)
        messages = list(state["messages"])
        memory_context = state.get("memory_context", "")
        procedure_request = state.get("procedure_request", "")
        previous_results = state.get("step_results", [])
        tool_round = state.get("step_tool_calls", 0)
        procedure_artifacts = _refresh_procedure_artifacts(
            messages,
            state.get("procedure_artifacts", {}),
        )

        steps = procedure.get("steps", []) if procedure else []
        current_step: Optional[dict[str, Any]] = None
        if steps and index < len(steps):
            current_step = steps[index]
            assert current_step is not None
            step_content = current_step.get("content", current_step.get("description", ""))
            step_description = current_step.get("description", f"Step {index + 1}")
        else:
            step_content = procedure.get("content", "") if procedure else ""
            step_description = "Execute procedure"

        step_allowed_tools = _resolve_step_allowed_tools(procedure, current_step)
        memory_section = ""
        if memory_context:
            memory_section = f"## Memory Context\n{_sanitize_memory_context(memory_context)}\n\n"

        request_section = ""
        if procedure_request and index == 0:
            request_section = f"## Current User Request\n{procedure_request}\n\n"

        cached_artifacts_section = _build_cached_artifact_section(
            procedure_artifacts,
            step_allowed_tools,
        )

        previous_results_section = ""
        if previous_results:
            previous_results_section = "## Previous Step Results\n" + "\n\n".join(
                f"**Step {result['step'] + 1}** ({result.get('description', '')}): "
                f"{result['output'][:300]}"
                for result in previous_results
            )
            previous_results_section += "\n\n"

        total_steps = len(steps) if steps else 1
        step_instruction = STEP_INSTRUCTION_TEMPLATE.format(
            step_num=index + 1,
            total_steps=total_steps,
            step_description=step_description,
            step_content=step_content,
            request_section=request_section,
            memory_section=memory_section,
            cached_artifacts_section=cached_artifacts_section,
            previous_results_section=previous_results_section,
        )

        is_tool_continuation = bool(messages) and isinstance(messages[-1], ToolMessage)

        if is_tool_continuation:
            step_start = 0
            for msg_index in range(len(messages) - 1, -1, -1):
                if (
                    isinstance(messages[msg_index], HumanMessage)
                    and isinstance(messages[msg_index].content, str)
                    and STEP_INSTRUCTION_MARKER in messages[msg_index].content
                ):
                    step_start = msg_index
                    break

            llm_messages = [
                SystemMessage(content=PROCEDURE_STEP_SYSTEM_PROMPT),
                *messages[step_start:],
            ]
        else:
            llm_messages = [
                SystemMessage(content=PROCEDURE_STEP_SYSTEM_PROMPT),
                HumanMessage(content=step_instruction),
            ]

        at_safety_cap = tool_round >= MAX_TOOL_ROUNDS_PER_STEP
        if at_safety_cap:
            logger.warning(
                "Step %d/%d hit safety cap (%d tool rounds), forcing text response",
                index + 1,
                total_steps,
                tool_round,
            )
            step_llm = llm
        elif step_allowed_tools is not None:
            step_schemas = [
                schema for schema in app.get_tool_schemas() if schema.name in step_allowed_tools
            ]
            step_llm = llm.bind_tools(step_schemas) if step_schemas else llm
        else:
            step_llm = llm_with_tools

        response = step_llm.invoke(llm_messages)
        if getattr(response, "tool_calls", None) and not at_safety_cap:
            filtered_tool_calls, duplicate_summaries = _filter_duplicate_tool_calls(
                list(response.tool_calls),
                procedure_artifacts,
            )
            if duplicate_summaries:
                logger.info(
                    "Step %d/%d suppressed %d duplicate tool call(s) from cached procedure context",
                    index + 1,
                    total_steps,
                    len(duplicate_summaries),
                )
                if filtered_tool_calls:
                    response = _copy_ai_message_with_tool_calls(response, filtered_tool_calls)
                    return {
                        "messages": [response],
                        "step_tool_calls": tool_round + 1,
                        "procedure_artifacts": procedure_artifacts,
                    }

                duplicate_notice = HumanMessage(
                    content=(
                        "Cached results from this procedure run already cover the exact tool call(s) "
                        "you attempted to repeat:\n"
                        + "\n".join(duplicate_summaries)
                        + "\n\nUse those cached results and finish the current step without "
                        "calling the same tool again."
                    )
                )
                response = llm.invoke([*llm_messages, duplicate_notice])
            else:
                return {
                    "messages": [response],
                    "step_tool_calls": tool_round + 1,
                    "procedure_artifacts": procedure_artifacts,
                }

        content = response.content
        if isinstance(content, str):
            raw_content = content
        elif isinstance(content, list):
            raw_content = "\n".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
                if not isinstance(block, dict) or block.get("type") == "text"
            ).strip() or str(content)
        else:
            raw_content = str(content)

        cleaned = re.sub(
            r"^(?:\*{0,2}\s*)?(?:\[Executing\s+)?[Ss]tep\s+\d+/\d+[\s:—–-][^\n]*\]?\n{0,2}",
            "",
            raw_content,
        ).lstrip()
        labeled_response = AIMessage(
            content=f"**Step {index + 1}/{total_steps} — {step_description}**\n\n{cleaned}"
        )
        new_result = {
            "step": index,
            "description": step_description,
            "output": raw_content[:500],
        }
        return {
            "messages": [labeled_response],
            "procedure_step_index": index + 1,
            "step_tool_calls": 0,
            "step_results": previous_results + [new_result],
            "procedure_artifacts": procedure_artifacts,
        }

    def respond_node(state: AgentState) -> dict:
        procedure = state.get("current_procedure") or {}
        step_results = state.get("step_results", [])
        memory_context = state.get("memory_context", "")
        messages = list(state["messages"])

        step_summaries = "\n\n".join(
            f"### Step {result['step'] + 1}: {result.get('description', '')}\n{result['output']}"
            for result in step_results
        )
        memory_section = (
            f"## Memory Context\n{_sanitize_memory_context(memory_context)}"
            if memory_context
            else ""
        )
        system_content = RESPOND_SYSTEM_PROMPT.format(
            procedure=procedure.get("procedure", "unknown"),
            step_summaries=step_summaries,
            memory_section=memory_section,
        )

        llm_messages = [SystemMessage(content=system_content)] + messages
        response = llm.invoke(llm_messages)
        if isinstance(response.content, str):
            response.content = _renumber_citations(response.content)
        return {"messages": [response]}

    def agent_node(state: AgentState) -> dict:
        messages = list(state["messages"])
        user_id = state.get("user_id") or ""
        session_id = state.get("session_id") or ""
        memory_context = state.get("memory_context", "")
        citation_index = state.get("citation_index", "")
        human_count = sum(1 for message in messages if isinstance(message, HumanMessage))

        system_prompt = SYSTEM_PROMPT
        if memory_context:
            available_sources = (
                f"\n## Available Sources\n{citation_index}" if citation_index else ""
            )
            prompt_memory_context = _sanitize_memory_context(memory_context)
            system_prompt += """
            {available_sources}

            ## Background Context (internal — do not reproduce these headings or labels in your output)
            {memory_context}

            **IMPORTANT**:
            If the background context above contains information that directly answers the user's question \
            (e.g., account profiles, revenue by category, Tier 1 benchmarks), \
            use that information to respond WITHOUT calling tools.
            Use the source numbers from Available Sources when citing data. Never copy section titles, \
            memory-type labels, or retrieval headers from the context above into your response or Sources list.
            Only call tools when you need data not available in the background context above.
            """.format(memory_context=prompt_memory_context, available_sources=available_sources)

        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=system_prompt)] + messages
        else:
            messages = [SystemMessage(content=system_prompt)] + messages[1:]

        response = llm_with_tools.invoke(messages)
        has_tool_calls = bool(getattr(response, "tool_calls", None))

        # NOTE: Temp for 03/05 demo. Mongomem has an auto extraction service.
        if not has_tool_calls:
            completed_messages = list(messages) + [response]
            if _should_extract_conversation_summary(
                has_tool_calls=has_tool_calls,
                human_count=human_count,
                user_id=user_id,
                session_id=session_id,
                memory_enabled=_memory_engine() is not None,
            ):
                assert user_id and session_id
                _queue_conversation_summary(completed_messages, user_id, session_id)

            if _should_extract_conversation_procedure(
                has_tool_calls=has_tool_calls,
                human_count=human_count,
                user_id=user_id,
                session_id=session_id,
                memory_enabled=_memory_engine() is not None,
            ):
                assert user_id and session_id
                _queue_procedural_memory(completed_messages, user_id, session_id)

        if isinstance(response.content, str):
            response.content = _renumber_citations(response.content)

        return {
            "messages": [response],
            "memory_context": memory_context,
            "citation_index": citation_index,
        }

    def route_after_discover(state: AgentState) -> Literal["execute_step", "agent"]:
        procedure = state.get("current_procedure")
        if procedure and (procedure.get("steps") or procedure.get("content")):
            return "execute_step"
        return "agent"

    def route_after_step(state: AgentState) -> Literal["tools", "execute_step", "respond"]:
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "tools"

        procedure = state.get("current_procedure")
        if procedure:
            steps = procedure.get("steps", [])
            index = state.get("procedure_step_index", 0)
            if steps and index < len(steps):
                return "execute_step"

        return "respond"

    def route_after_tools(state: AgentState) -> Literal["execute_step", "agent"]:
        if state.get("current_procedure"):
            return "execute_step"
        return "agent"

    def route_after_agent(state: AgentState) -> Literal["tools", "end"]:
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        return "end"

    logger.info("  -> Compiling procedure-driven graph...")
    builder = StateGraph(AgentState)
    builder.add_node("discover", discover_procedure_node)
    builder.add_node("execute_step", execute_step_node)
    builder.add_node("respond", respond_node)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "discover")
    builder.add_conditional_edges(
        "discover",
        route_after_discover,
        {"execute_step": "execute_step", "agent": "agent"},
    )
    builder.add_conditional_edges(
        "execute_step",
        route_after_step,
        {"tools": "tools", "execute_step": "execute_step", "respond": "respond"},
    )
    builder.add_conditional_edges(
        "tools",
        route_after_tools,
        {"execute_step": "execute_step", "agent": "agent"},
    )
    builder.add_conditional_edges("agent", route_after_agent, {"tools": "tools", "end": END})
    builder.add_edge("respond", END)

    graph = builder.compile(checkpointer=app.checkpointer())
    logger.info("Procedure-driven agent graph compiled")
    return graph


# =============================================================================
# Run
# =============================================================================


def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info(f"Starting {APP_NAME} (Magenta SDK)")
    logger.info("=" * 60)

    app.run()


if __name__ == "__main__":
    main()
