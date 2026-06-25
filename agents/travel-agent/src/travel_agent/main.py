"""Travel Agent — travel disruption and re-accommodation demo built on Runner SDK."""

import atexit
import json
import logging
import os
import re
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Literal, Optional, cast

import httpx
from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from magenta_sdklanggraph import App
from pydantic import BaseModel, Field
from runner_shared.context import (
    get_current_execution_id,
    get_current_oe_url,
    get_current_session_id,
    get_current_user_id,
)
from runner_shared.models import SuspendPayload

from . import tools as travel_tools
from .llm import build_llm
from .prompts import (
    DIRECT_RESPONSE_SYSTEM_PROMPT,
    FINAL_REPORT_SYSTEM_PROMPT,
    PROCEDURAL_EXTRACTION_PROMPT,
    build_resolution_system_prompt,
)
from .state import AgentState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)
load_dotenv()

TRAVEL_PROCEDURE_NAME = "travel-disruption-reaccommodation-playbook"
TRAVEL_REVIEW_DECISIONS = ["approve", "reject"]
_ALLOWED_PROCEDURE_STEP_TYPES = {"instruction", "code", "validation", "prompt", "human_input"}

APP_NAME = "Travel Agent"
ORG_ID = os.environ.get("ORG_ID")

app = App(
    app_name=APP_NAME,
    org_id=ORG_ID or None,
)
logger.info("TenantRuntime created")


def _validate_env() -> None:
    missing = [
        name
        for name, value in (
            ("ORG_ID", os.environ.get("ORG_ID")),
            ("PROJECT_ID", os.environ.get("PROJECT_ID")),
            ("APP_ID", os.environ.get("APP_ID")),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"{', '.join(missing)} must be set in the environment")


def _supervisor_review_reasons(
    *,
    estimated_cost: float,
    downgrade: bool,
    traveler_flags: str,
) -> list[str]:
    reasons: list[str] = []
    normalized_flags = traveler_flags.lower()
    if downgrade:
        reasons.append("premium traveler downgrade")
    if "umnr" in normalized_flags:
        reasons.append("unaccompanied minor requires approval")
    if "wchr" in normalized_flags or "meda" in normalized_flags:
        reasons.append("special assistance traveler requires approval")
    if estimated_cost > travel_tools.partner_cost_threshold():
        reasons.append("cost exceeds configured review threshold")
    return reasons


def _usd(amount: float) -> str:
    return f"${amount:,.2f}"


def _review_field(
    key: str,
    label: str,
    value: Any,
    field_format: str = "text",
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "value": value,
        "format": field_format,
    }


def _reaccommodation_review_presentation(
    *,
    pnr: str,
    option_id: str,
    traveler_name: str,
    reason: str,
    estimated_cost: float,
    hotel_cost: float,
    voucher_cost: float,
    downgrade: bool,
    traveler_flags: str,
    review_reasons: list[str],
    summary: str,
    instructions: str,
) -> dict[str, Any]:
    flags = [flag.strip().upper() for flag in re.split(r"[,\s]+", traveler_flags) if flag.strip()]
    displayed_reasons = review_reasons or [
        "Supervisor approval requested by the recovery workflow."
    ]

    return {
        "schema_version": "review-presentation/v1",
        "title": "Travel reaccommodation review",
        "summary": summary,
        "sections": [
            {
                "title": "Traveler",
                "fields": [
                    _review_field("traveler_name", "Traveler", traveler_name),
                    _review_field("pnr", "PNR", pnr),
                    _review_field("traveler_flags", "Traveler flags", flags or ["None"], "list"),
                    _review_field("downgrade", "Cabin downgrade", downgrade, "boolean"),
                ],
            },
            {
                "title": "Recovery option",
                "fields": [
                    _review_field("option_id", "Recommended option", option_id),
                    _review_field("estimated_cost", "Estimated cost", _usd(estimated_cost)),
                    _review_field("hotel_cost", "Hotel support", _usd(hotel_cost)),
                    _review_field("voucher_cost", "Voucher support", _usd(voucher_cost)),
                ],
            },
            {
                "title": "Review guidance",
                "fields": [
                    _review_field("review_reasons", "Review reasons", displayed_reasons, "list"),
                    _review_field("reason", "Agent rationale", reason, "long_text"),
                    _review_field("instructions", "Instructions", instructions, "long_text"),
                ],
            },
        ],
        "actions": [
            {"id": "approve", "label": "Approve"},
            {"id": "reject", "label": "Reject"},
        ],
    }


_PROCEDURE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="travel_procedure")
atexit.register(lambda: _PROCEDURE_EXECUTOR.shutdown(wait=False, cancel_futures=True))


class ProcedureStep(BaseModel):
    step_type: str = Field(
        default="instruction",
        description="One of: instruction, code, validation, prompt, human_input",
    )
    description: str = Field(description="Short label for the step")
    content: str = Field(description="Detailed instructions")


class ProceduralExtraction(BaseModel):
    procedure: Optional[str] = Field(
        default=None,
        description="Kebab-case procedure name, or null if none extracted",
    )
    description: str = Field(default="", description="When to use this procedure")
    content: str = Field(default="", description="Full step-by-step as markdown")
    tags: str = Field(default="", description="Comma-separated tags")
    trigger_conditions: list[str] = Field(
        default_factory=list,
        description="Short phrases describing when to apply the procedure",
    )
    steps: list[ProcedureStep] = Field(default_factory=list)


def _build_configured_llm(temperature: float = 0.0) -> BaseChatModel:
    """Create the provider client configured by agent.yaml."""
    llm_config = getattr(app, "llm_config", None)
    return build_llm(
        provider=getattr(llm_config, "provider", None),
        model=getattr(llm_config, "model", None),
        temperature=temperature,
    )


get_disruption_event = app.tool(is_local=False)(travel_tools.get_disruption_event)
list_impacted_pnrs = app.tool(is_local=False)(travel_tools.list_impacted_pnrs)
get_passenger_context = app.tool(is_local=False)(travel_tools.get_passenger_context)
get_booking_record = app.tool(is_local=False)(travel_tools.get_booking_record)
get_irrops_policy = app.tool(is_local=False)(travel_tools.get_irrops_policy)
search_alternative_inventory = app.tool(is_local=False)(travel_tools.search_alternative_inventory)
score_reaccommodation_options = app.tool(is_local=False)(travel_tools.score_reaccommodation_options)


@app.tool(is_local=True)
def hold_reaccommodation_option(pnr: str, option_id: str) -> str:
    """Place a temporary hold on the selected recovery option."""
    return travel_tools.hold_reaccommodation_option(pnr=pnr, option_id=option_id)


@app.tool(is_local=True)
def reissue_ticket(pnr: str, option_id: str) -> str:
    """Reissue a disrupted passenger onto the chosen replacement option."""
    return travel_tools.reissue_ticket(pnr=pnr, option_id=option_id)


@app.tool(is_local=True)
def create_travel_voucher(pnr: str, voucher_type: str, amount: float) -> str:
    """Issue a meal or goodwill voucher to the traveler."""
    return travel_tools.create_travel_voucher(pnr=pnr, voucher_type=voucher_type, amount=amount)


@app.tool(is_local=True)
def book_hotel(pnr: str, city: str, nights: int) -> str:
    """Book overnight accommodation for an impacted traveler."""
    return travel_tools.book_hotel(pnr=pnr, city=city, nights=nights)


@app.tool(is_local=True)
def send_trip_update(pnr: str, channel: str, summary: str) -> str:
    """Send the final disruption update to the traveler."""
    return travel_tools.send_trip_update(pnr=pnr, channel=channel, summary=summary)


@app.tool(is_local=True)
def request_supervisor_approval(
    pnr: str,
    option_id: str,
    traveler_name: str,
    reason: str,
    estimated_cost: float = 0.0,
    hotel_cost: float = 0.0,
    voucher_cost: float = 0.0,
    downgrade: bool = False,
    traveler_flags: str = "",
) -> str:
    """Suspend execution and request supervisor approval for exception handling."""
    review_reasons = _supervisor_review_reasons(
        estimated_cost=estimated_cost,
        downgrade=downgrade,
        traveler_flags=traveler_flags,
    )

    task_id = f"REACCOM-{pnr}-{option_id}"
    summary = (
        f"Review reaccommodation for {traveler_name} ({pnr}) onto option {option_id} "
        f"with estimated cost {_usd(estimated_cost)}."
    )
    instructions = (
        f"Review reaccommodation request for {traveler_name} ({pnr}). "
        f"Option {option_id} costs {_usd(estimated_cost)}. Approve or reject with notes."
    )
    return SuspendPayload(
        suspend_reason="reaccommodation_approval_required",
        suspend_context={
            "task_id": task_id,
            "decision_type": "travel_reaccommodation",
            "summary": summary,
            "pnr": pnr,
            "option_id": option_id,
            "traveler_name": traveler_name,
            "reason": reason,
            "estimated_cost": estimated_cost,
            "hotel_cost": hotel_cost,
            "voucher_cost": voucher_cost,
            "downgrade": downgrade,
            "traveler_flags": traveler_flags,
            "review_reasons": review_reasons,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "allowed_decisions": TRAVEL_REVIEW_DECISIONS,
            "instructions": instructions,
            "review_presentation": _reaccommodation_review_presentation(
                pnr=pnr,
                option_id=option_id,
                traveler_name=traveler_name,
                reason=reason,
                estimated_cost=estimated_cost,
                hotel_cost=hotel_cost,
                voucher_cost=voucher_cost,
                downgrade=downgrade,
                traveler_flags=traveler_flags,
                review_reasons=review_reasons,
                summary=summary,
                instructions=instructions,
            ),
        },
    ).to_json()


_MEMORY_TYPE_HEADER_RE = re.compile(
    r"^#{1,3}\s*(?:Semantic Memory|Taxonomic Memory|Episodic Memory)\s*$",
    re.MULTILINE,
)
_SOURCE_DISPLAY_NAMES = {
    "policy_portal": "Policy Portal",
    "partner_inventory": "Partner Inventory",
    "airport_ops": "Airport Operations",
    "traveler_service": "Traveler Service",
}
_STEP_HANDLER_RE = re.compile(r"handler:([a-z_]+)")
_PNR_RE = re.compile(r"\bTRV-\d{5}\b")
_DISRUPTION_RE = re.compile(r"\bDISR-\d{4}\b")
_FLIGHT_RE = re.compile(r"\bTA\d{3,4}\b", re.IGNORECASE)
_LIMIT_RE = re.compile(r"(?:next|top)(?:\s+(\d+))?\s+passengers")
_PROCEDURE_DISCOVERY_TOKENS = ("cancel", "re-accommodate", "reaccommodate", "irrops", "weather")


def _extract_latest_user_query(messages: list[BaseMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return msg.content if isinstance(msg.content, str) else str(msg.content)
    return ""


def _execution_user_id(state: AgentState) -> Optional[str]:
    """Return the OE execution principal, falling back to graph state for tests."""
    return get_current_user_id() or state.get("user_id")


def _sanitize_memory_context(text: str) -> str:
    return _MEMORY_TYPE_HEADER_RE.sub("", text)


def _serialize_message(message: BaseMessage) -> dict[str, str]:
    if isinstance(message, HumanMessage):
        role = "user"
    elif isinstance(message, ToolMessage):
        role = "tool"
    else:
        role = "assistant"
    return {"role": role, "content": _stringify(message.content)}


def _emit_oe_node(
    node_name: str,
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    duration_ms: float = 0,
    *,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> None:
    """Post synthetic OE node events for memory recall/extraction debug lanes."""
    oe_url = get_current_oe_url()
    execution_id = get_current_execution_id()
    if not oe_url or not execution_id:
        return

    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    base: dict[str, Any] = {
        "execution_id": execution_id,
        "node_name": node_name,
        "run_id": run_id,
        "session_id": session_id or get_current_session_id(),
        "thread_id": session_id or get_current_session_id(),
        "user_id": user_id or get_current_user_id(),
        "org_id": ORG_ID,
    }
    try:
        with httpx.Client(timeout=2.0) as client:
            client.post(
                f"{oe_url}/node/execution",
                json={
                    **base,
                    "status": "started",
                    "timestamp": started_at.isoformat(),
                    "inputs": inputs,
                },
            )
            client.post(
                f"{oe_url}/node/execution",
                json={
                    **base,
                    "status": "success",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "outputs": outputs,
                    "duration_ms": duration_ms,
                },
            )
    except Exception as exc:  # noqa: BLE001 - best effort debug instrumentation
        logger.debug("Failed to emit %s node event to OE: %s", node_name, exc)


def _emit_recall_memory_node(
    query: str,
    memory_context: str,
    *,
    duration_ms: float = 0,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> None:
    """Emit retrieved memory context for the DebugPanel memory recall lane."""
    if not memory_context.strip():
        return

    _emit_oe_node(
        "recall_memory",
        {"query": query, "user_id": user_id or get_current_user_id()},
        {
            "memory_context": memory_context,
            "has_context": True,
            "context_length": len(memory_context),
        },
        duration_ms=duration_ms,
        session_id=session_id,
        user_id=user_id,
    )


def _build_citation_index(raw_memories: list[Any]) -> str:
    lines = []
    seen: set[tuple[str, str]] = set()
    idx = 0
    for memory in raw_memories or []:
        meta = getattr(memory, "metadata", None) or {}
        mem_type = getattr(memory, "source", "")
        if mem_type == "semantic":
            source = meta.get("source", "")
            label = meta.get("label", "")
        elif mem_type == "taxonomic":
            source = "policy_portal"
            label = f"{meta.get('domain', '')}:{meta.get('term', '')}"
        else:
            continue
        key = (source, label)
        if key in seen:
            continue
        seen.add(key)
        idx += 1
        source_name = _SOURCE_DISPLAY_NAMES.get(source, source or "Memory")
        label_name = label.replace("_", " ").replace("-", " ").title()
        lines.append(f"[{idx}] {source_name} - {label_name}")
    return "\n".join(lines)


def _current_handler(procedure: Optional[dict[str, Any]], index: int) -> Optional[str]:
    if not procedure:
        return None
    steps = procedure.get("steps") or []
    if index >= len(steps):
        return None
    description = steps[index].get("description", "")
    match = _STEP_HANDLER_RE.search(description)
    return match.group(1) if match else None


def _format_procedural_memory_context(procedure_name: str, procedure: dict[str, Any]) -> str:
    """Format a matched travel workflow as a typed procedural memory section."""
    lines = ["## Procedural Memory", f"Procedure: {procedure_name or 'unknown'}"]
    description = str(procedure.get("description") or "").strip()
    if description:
        lines.extend(["", description])

    trigger_conditions = procedure.get("trigger_conditions")
    if isinstance(trigger_conditions, list):
        triggers = [str(trigger).strip() for trigger in trigger_conditions if str(trigger).strip()]
        if triggers:
            lines.extend(["", "Trigger conditions:"])
            lines.extend(f"- {trigger}" for trigger in triggers[:5])

    steps = procedure.get("steps") if isinstance(procedure.get("steps"), list) else []
    if steps:
        lines.extend(["", "Saved workflow steps:"])
        for index, step in enumerate(steps):
            description = str(step.get("description") or f"Step {index + 1}").strip()
            lines.append(f"{index + 1}. {description}")

    return "\n".join(lines).strip()


def _procedure_remaining(state: AgentState) -> bool:
    procedure = state.get("current_procedure")
    if not procedure:
        return False
    return state.get("procedure_step_index", 0) < len(procedure.get("steps") or [])


def _stringify(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        ).strip()
    return str(content)


def _extract_batch_limit(query: str) -> Optional[int]:
    lower = query.lower()
    match = _LIMIT_RE.search(lower)
    if match:
        value = match.group(1)
        return int(value) if value else 10
    if "exception queue" in lower or "batch triage" in lower:
        return 10
    return None


def _resolve_disruption_id(query: str, travel_context: dict[str, Any]) -> str:
    disruption_match = _DISRUPTION_RE.search(query)
    if disruption_match:
        return disruption_match.group(0)

    flight_match = _FLIGHT_RE.search(query.upper())
    if flight_match:
        flight_number = flight_match.group(0).upper()
        for disruption in travel_tools.DISRUPTIONS.values():
            if str(disruption.get("flight_number") or "").upper() == flight_number:
                return str(disruption["disruption_id"])

    return str(travel_context.get("disruption_id") or travel_tools.DEFAULT_DISRUPTION_ID)


def _procedural_memory_payload(
    state: AgentState,
    *,
    travel_context: Optional[dict[str, Any]] = None,
    query: Optional[str] = None,
) -> dict[str, Any]:
    current_context = dict(travel_context or state.get("travel_context") or {})
    messages = list(state.get("messages") or [])
    latest_query = query or _extract_latest_user_query(messages)
    recommended = dict(current_context.get("recommended_option") or {})
    policy = dict(current_context.get("policy") or {})
    batch_queue = dict(current_context.get("batch_queue") or {})

    return {
        "question": latest_query,
        "session_id": state.get("session_id") or get_current_session_id(),
        "workflow_observed": [
            {
                "handler": "impact_flow",
                "goal": "Assess the disrupted flight, summarize the impact, and identify the first passenger to handle.",
                "evidence": {
                    "disruption_id": current_context.get("disruption_id"),
                    "hero_pnr": current_context.get("hero_pnr"),
                    "priority_pnrs": list(current_context.get("priority_pnrs") or [])[:10],
                    "batch_queue_counts": {
                        "auto_rebook": len(batch_queue.get("auto_rebook") or []),
                        "approval_queue": len(batch_queue.get("approval_queue") or []),
                        "manual_review": len(batch_queue.get("manual_review") or []),
                    },
                },
            },
            {
                "handler": "reaccommodation_flow",
                "goal": "Inspect one passenger, apply policy constraints, and rank compliant alternatives.",
                "evidence": {
                    "selected_pnr": current_context.get("selected_pnr"),
                    "passenger_name": current_context.get("passenger_name"),
                    "traveler_flags": current_context.get("traveler_flags"),
                    "recommended_option": {
                        "option_id": recommended.get("option_id"),
                        "carrier": recommended.get("carrier"),
                        "flight_number": recommended.get("flight_number"),
                        "cabin": recommended.get("cabin"),
                        "same_day_arrival": recommended.get("same_day_arrival"),
                        "approval_required": recommended.get("approval_required"),
                    },
                    "policy": {
                        "partner_auto_approval_threshold_usd": policy.get(
                            "partner_auto_approval_threshold_usd"
                        ),
                        "overnight_hotel_cap_usd": policy.get("overnight_hotel_cap_usd"),
                        "manual_review_required": policy.get("manual_review_required"),
                    },
                },
            },
            {
                "handler": "resolution_flow",
                "goal": "Execute the chosen option, request approval when needed, and finish servicing actions.",
                "evidence": {
                    "resolution_completed": bool(current_context.get("resolution_completed")),
                    "selected_pnr": current_context.get("selected_pnr"),
                    "recommended_option_id": recommended.get("option_id"),
                    "approval_required": recommended.get("approval_required"),
                    "hotel_eligible": recommended.get("hotel_eligible"),
                    "voucher_amount": recommended.get("voucher_amount"),
                },
            },
        ],
        "conversation": [_serialize_message(message) for message in messages[-12:]],
    }


def _default_travel_procedure_steps() -> list[dict[str, str]]:
    return [
        {
            "step_type": "instruction",
            "description": "Assess disruption and prioritize passengers | handler:impact_flow",
            "content": (
                "Review the disrupted flight, summarize the cause and impact, and identify the highest-priority passenger "
                "to handle first."
            ),
        },
        {
            "step_type": "instruction",
            "description": "Evaluate one passenger and rank compliant options | handler:reaccommodation_flow",
            "content": (
                "Inspect the selected PNR, traveler tier, SSRs, booking details, and policy constraints. "
                "Rank the best reaccommodation options and identify the top compliant recommendation."
            ),
        },
        {
            "step_type": "instruction",
            "description": "Execute the recovery plan and close the case | handler:resolution_flow",
            "content": (
                "Place a hold on the recommended option, request supervisor approval when required, reissue the itinerary, "
                "issue hotel or voucher support if eligible, notify the traveler, and confirm the final disposition."
            ),
        },
    ]


def _default_travel_procedure_content(steps: list[dict[str, str]]) -> str:
    lines = [
        "Use this playbook for cancelled or severely disrupted flights that require rapid passenger triage and reaccommodation.",
        "",
    ]
    for index, step in enumerate(steps, start=1):
        lines.append(f"{index}. **{step['description'].split('|')[0].strip()}**")
        lines.append(f"   {step['content']}")
    return "\n".join(lines).strip()


def _normalize_procedure_step(step: ProcedureStep) -> dict[str, str]:
    step_type = re.sub(r"[\s-]+", "_", step.step_type.strip().lower()) or "instruction"
    if step_type not in _ALLOWED_PROCEDURE_STEP_TYPES:
        step_type = "instruction"
    return {
        "step_type": step_type,
        "description": step.description.strip(),
        "content": step.content.strip(),
    }


def _canonicalize_procedural_extraction(parsed: ProceduralExtraction) -> dict[str, Any]:
    procedure_name = (parsed.procedure or "").strip()
    description = parsed.description.strip()
    trigger_conditions = [item.strip() for item in parsed.trigger_conditions if item.strip()]
    tags = [item.strip() for item in parsed.tags.split(",") if item.strip()]
    steps = [
        _normalize_procedure_step(step)
        for step in parsed.steps
        if step.description.strip() and step.content.strip()
    ]
    content = parsed.content.strip()

    if procedure_name == TRAVEL_PROCEDURE_NAME:
        if len(steps) != 3:
            logger.info(
                "Procedure %s returned %d usable steps; applying canonical 3-step fallback",
                procedure_name,
                len(steps),
            )
            steps = _default_travel_procedure_steps()
        if not description:
            description = (
                "Use for flight cancellations or major disruptions that require disruption triage, individual passenger "
                "recovery, and approval-aware execution."
            )
        if not trigger_conditions:
            trigger_conditions = [
                "Cancelled or severely disrupted flight",
                "Impacted passengers need rapid re-accommodation",
                "Operations needs an approval-aware recovery path",
            ]
        if not tags:
            tags = ["travel", "irrops", "reaccommodation", "playbook"]
        if not content:
            content = _default_travel_procedure_content(steps)

    return {
        "procedure": procedure_name,
        "description": description,
        "content": content,
        "trigger_conditions": trigger_conditions,
        "tags": tags,
        "steps": steps,
    }


def _save_procedural_memory(session_payload: dict[str, Any], user_id: str, session_id: str) -> None:
    """Extract and save a reusable travel disruption procedure from a completed demo session."""
    question = str(session_payload.get("question") or "").strip()
    if not question:
        return

    try:
        parsed = cast(
            ProceduralExtraction,
            _build_configured_llm(temperature=0)
            .with_structured_output(ProceduralExtraction)
            .invoke(
                [
                    SystemMessage(content=PROCEDURAL_EXTRACTION_PROMPT),
                    HumanMessage(
                        content=f"Travel disruption session workflow:\n{json.dumps(session_payload, indent=2)}"
                    ),
                ]
            ),
        )
        if not parsed.procedure:
            logger.info("No reusable travel procedure extracted for session %s", session_id)
            return

        normalized = _canonicalize_procedural_extraction(parsed)
        steps = normalized["steps"]
        if not steps:
            logger.info(
                "Extracted procedure %s had no valid steps; skipping save", parsed.procedure
            )
            return

        saved = app.memory.save_procedure(
            procedure=normalized["procedure"],
            description=normalized["description"],
            content=normalized["content"],
            user_id=user_id,
            steps=steps,
            trigger_conditions=normalized["trigger_conditions"],
            tags=normalized["tags"],
            visibility="org",
            agent_id=os.environ.get("APP_ID"),
            extraction_source="travel_session",
            source_format="json",
            source_path=f"session:{session_id}",
            update_existing=True,
        )

        if saved:
            logger.info(
                "Saved procedural memory: %s (%d steps)", normalized["procedure"], len(steps)
            )
    except Exception:
        logger.warning("Failed to extract procedural memory", exc_info=True)


def _should_queue_procedural_learning(
    state: AgentState,
    travel_context: dict[str, Any],
) -> bool:
    if state.get("current_procedure"):
        return False
    if travel_context.get("procedural_learning_queued"):
        return False
    return bool(
        travel_context.get("resolution_completed")
        and travel_context.get("selected_pnr")
        and travel_context.get("recommended_option")
        and travel_context.get("disruption_id")
    )


def _queue_procedural_learning(
    state: AgentState,
    travel_context: dict[str, Any],
    *,
    pnr: str,
) -> None:
    if not _should_queue_procedural_learning(state, travel_context):
        return

    user_id = _execution_user_id(state)
    session_id = state.get("session_id") or get_current_session_id()
    if not user_id or not session_id:
        return

    query = _extract_latest_user_query(state["messages"])
    try:
        _PROCEDURE_EXECUTOR.submit(
            _save_procedural_memory,
            _procedural_memory_payload(state, travel_context=travel_context, query=query),
            user_id,
            session_id,
        )
        travel_context["procedural_learning_queued"] = True
        _emit_oe_node(
            "memory_extraction",
            {
                "trigger": "travel_resolution_completed",
                "selected_pnr": pnr,
                "disruption_id": travel_context.get("disruption_id"),
            },
            {
                "queued_episodic": False,
                "queued_semantic": False,
                "queued_procedural": True,
            },
            session_id=session_id,
            user_id=user_id,
        )
    except Exception:
        logger.warning("Failed to queue procedural extraction", exc_info=True)


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    header = "| " + " | ".join(headers) + " |"
    divider = "| " + " | ".join(["---"] * len(headers)) + " |"
    body = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([header, divider, *body])


def _flow_result(
    state: AgentState,
    flow_name: str,
    content: str,
    context_updates: Optional[dict[str, Any]] = None,
    *,
    wrap_in_heading: bool = True,
) -> dict[str, Any]:
    if wrap_in_heading:
        title = flow_name.replace("_", " ").title()
        message = AIMessage(content=f"## {title}\n\n{content}")
    else:
        message = AIMessage(content=content)
    travel_context = dict(state.get("travel_context") or {})
    if context_updates:
        travel_context.update(context_updates)
    result: dict[str, Any] = {
        "messages": [message],
        "travel_context": travel_context,
        "current_flow": flow_name,
    }
    if (
        state.get("current_procedure")
        and _current_handler(state.get("current_procedure"), state.get("procedure_step_index", 0))
        == flow_name
    ):
        step_results = list(state.get("step_results") or [])
        step_results.append({"flow": flow_name, "output": _stringify(message.content)})
        result["step_results"] = step_results
        result["procedure_step_index"] = state.get("procedure_step_index", 0) + 1
    return result


def _classify_route(query: str, state: AgentState) -> str:
    current_handler = _current_handler(
        state.get("current_procedure"), state.get("procedure_step_index", 0)
    )
    if current_handler:
        return current_handler
    lower = query.lower()
    if any(token in lower for token in ["cancel", "disruption", "impacted", "affected passengers"]):
        return "impact_flow"
    if _extract_batch_limit(query) is not None:
        return "impact_flow"
    if "pnr" in lower or any(
        token in lower for token in ["handle", "options", "reaccommodate", "rebook", "alternative"]
    ):
        return "reaccommodation_flow"
    if any(token in lower for token in ["book", "hotel", "voucher", "notify", "ticket", "approve"]):
        return "resolution_flow"
    return "direct_response"


def _memory_context_for_query(
    query: str,
    user_id: Optional[str],
    session_id: Optional[str],
) -> tuple[str, str]:
    if not user_id or not query:
        return "", ""
    result = app.memory.build_context(
        query=query,
        user_id=user_id,
        thread_id=session_id or None,
    )
    if hasattr(result, "formatted_context"):
        return result.formatted_context, _build_citation_index(
            getattr(result, "selected_memories", [])
        )
    return str(result), ""


def _load_replay_procedure(procedure_name: str) -> Optional[dict[str, Any]]:
    procedure = app.memory.get_procedure(procedure_name, visibility="org")
    logger.info(
        "Procedural replay lookup selected=%s loaded=%s steps=%d",
        procedure_name,
        bool(procedure),
        len(procedure.get("steps") or []) if procedure else 0,
    )
    return procedure


def _discover_replay_procedure(query: str, user_id: Optional[str]) -> Optional[dict[str, Any]]:
    lower_query = query.lower()
    if (
        not query
        or not user_id
        or not any(token in lower_query for token in _PROCEDURE_DISCOVERY_TOKENS)
    ):
        return None

    matches = app.memory.discover_procedures(
        query=query,
        user_id=user_id,
        visibility="org",
        similarity_threshold=0.65,
    )
    if not matches:
        logger.info("Procedural replay semantic discovery missed; trying canonical org playbook")
        return _load_replay_procedure(TRAVEL_PROCEDURE_NAME)

    procedure_name = str(matches[0].get("procedure") or TRAVEL_PROCEDURE_NAME)
    return _load_replay_procedure(procedure_name)


@app.entrypoint
def build_agent(llm: Optional[BaseChatModel] = None) -> CompiledStateGraph:
    _validate_env()
    if llm is None:
        llm = app.llm(_build_configured_llm(temperature=0))

    all_tools = app.get_tools()
    action_tool_names = {
        "hold_reaccommodation_option",
        "request_supervisor_approval",
        "reissue_ticket",
        "create_travel_voucher",
        "book_hotel",
        "send_trip_update",
    }
    action_schemas = [
        schema for schema in app.get_tool_schemas() if schema.name in action_tool_names
    ]
    action_llm = llm.bind_tools(action_schemas)

    def discover_node(state: AgentState) -> dict[str, Any]:
        messages = state["messages"]
        user_id = _execution_user_id(state)
        session_id = state.get("session_id") or get_current_session_id()
        query = _extract_latest_user_query(messages)
        memory_context, citation_index = _memory_context_for_query(query, user_id, session_id)
        current_procedure = None
        planning_message = None

        if query and memory_context:
            _emit_recall_memory_node(query, memory_context, session_id=session_id, user_id=user_id)

        current_procedure = _discover_replay_procedure(query, user_id)
        if current_procedure:
            _emit_recall_memory_node(
                query,
                _format_procedural_memory_context(
                    current_procedure.get("procedure", ""),
                    current_procedure,
                ),
                session_id=session_id,
                user_id=user_id,
            )
            steps = current_procedure.get("steps") or []
            plan_lines = "\n".join(
                f"{index + 1}. {step.get('description', f'Step {index + 1}')}"
                for index, step in enumerate(steps)
            )
            planning_message = AIMessage(
                content=(
                    f"Matched learned workflow **`{current_procedure.get('procedure', 'travel-irrops-playbook')}`**.\n\n"
                    f"**Planned execution:**\n{plan_lines}\n\n"
                    "Running the workflow now."
                )
            )

        return {
            "messages": [planning_message] if planning_message else [],
            "memory_context": memory_context,
            "citation_index": citation_index,
            "current_procedure": current_procedure,
            "procedure_step_index": 0,
            "step_results": [],
            "travel_context": dict(state.get("travel_context") or {}),
            "resolution_rounds": 0,
        }

    def supervisor_node(state: AgentState) -> dict[str, Any]:
        query = _extract_latest_user_query(state["messages"])
        route = _classify_route(query, state)
        return {"current_flow": route}

    def impact_flow_node(state: AgentState) -> dict[str, Any]:
        query = _extract_latest_user_query(state["messages"])
        travel_context = dict(state.get("travel_context") or {})
        disruption_id = _resolve_disruption_id(query, travel_context)
        disruption = travel_tools.get_disruption_event_data(disruption_id)
        if disruption is None:
            return _flow_result(state, "impact_flow", f"Unknown disruption `{disruption_id}`.")

        impacted = travel_tools.list_impacted_pnrs_data(disruption_id)
        prioritized = travel_tools.prioritize_impacted_pnrs(impacted)
        batch_limit = _extract_batch_limit(query)
        if batch_limit is not None:
            limit = batch_limit
            batch = travel_tools.build_batch_queue(disruption_id, limit)
            content = "\n\n".join(
                [
                    f"**Processing window:** next {limit} passengers from the disruption queue.",
                    _markdown_table(
                        ["Category", "Count", "Examples"],
                        [
                            [
                                "Auto rebook",
                                str(len(batch["auto_rebook"])),
                                ", ".join(item["pnr"] for item in batch["auto_rebook"][:3]) or "-",
                            ],
                            [
                                "Approval candidates",
                                str(len(batch["approval_queue"])),
                                ", ".join(item["pnr"] for item in batch["approval_queue"][:3])
                                or "-",
                            ],
                            [
                                "Manual-review exceptions",
                                str(len(batch["manual_review"])),
                                ", ".join(item["pnr"] for item in batch["manual_review"][:3])
                                or "-",
                            ],
                        ],
                    ),
                    "**Exception queue**: "
                    + ", ".join(item["pnr"] for item in batch["manual_review"][:5])
                    if batch["manual_review"]
                    else "**Exception queue**: none in this slice.",
                ]
            )
            travel_context.update(
                {
                    "disruption_id": disruption_id,
                    "batch_queue": batch,
                    "hero_pnr": prioritized[0]["pnr"] if prioritized else None,
                }
            )
            return _flow_result(state, "impact_flow", content, travel_context)

        counts = {"auto_rebook": 0, "approval_queue": 0, "manual_review": 0}
        for record in prioritized:
            counts[record["queue_bucket"]] += 1
        queue_bucket_names = {
            "auto_rebook": "Auto-rebook candidate",
            "approval_queue": "Approval candidate",
            "manual_review": "Manual-review exception",
        }
        top_rows = []
        for record in prioritized[:5]:
            top_rows.append(
                [
                    record["pnr"],
                    record["passenger_name"],
                    record["traveler_type"],
                    record["tier"],
                    queue_bucket_names.get(
                        record["queue_bucket"],
                        record["queue_bucket"].replace("_", " ").title(),
                    ),
                ]
            )
        content = "\n\n".join(
            [
                f"**Disruption:** {disruption['flight_number']} {disruption['origin']} -> {disruption['destination']} was cancelled due to **{disruption['cause']}**.",
                f"**Impacted passengers:** {disruption['impacted_passenger_count']} total.",
                _markdown_table(
                    ["Category", "Count"],
                    [
                        ["Auto rebook", str(counts["auto_rebook"])],
                        ["Approval candidates", str(counts["approval_queue"])],
                        ["Manual-review exceptions", str(counts["manual_review"])],
                    ],
                ),
                "**Highest-priority passengers**\n"
                + _markdown_table(
                    ["PNR", "Passenger", "Type", "Tier", "Category"],
                    top_rows,
                ),
            ]
        )
        travel_context.update(
            {
                "disruption_id": disruption_id,
                "hero_pnr": prioritized[0]["pnr"] if prioritized else None,
                "priority_pnrs": [record["pnr"] for record in prioritized[:10]],
            }
        )
        return _flow_result(state, "impact_flow", content, travel_context)

    def reaccommodation_flow_node(state: AgentState) -> dict[str, Any]:
        query = _extract_latest_user_query(state["messages"])
        travel_context = dict(state.get("travel_context") or {})
        pnr_match = _PNR_RE.search(query)
        pnr = pnr_match.group(0) if pnr_match else travel_context.get("hero_pnr")
        if not pnr:
            return _flow_result(
                state, "reaccommodation_flow", "No passenger context is available yet."
            )

        passenger = travel_tools.get_passenger_context_data(pnr)
        booking = travel_tools.get_booking_record_data(pnr)
        if passenger is None or booking is None:
            return _flow_result(state, "reaccommodation_flow", f"Unknown passenger `{pnr}`.")

        disruption = travel_tools.get_disruption_event_data(passenger["disruption_id"]) or {}
        policy = travel_tools._policy_for_profile(
            passenger, disruption.get("disruption_type", "weather_cancellation")
        )
        ranked_options = travel_tools.score_reaccommodation_options_data(pnr)
        if not ranked_options:
            return _flow_result(
                state, "reaccommodation_flow", f"No alternative inventory found for `{pnr}`."
            )
        recommended = ranked_options[0]
        rows = []
        for option in ranked_options[:3]:
            rows.append(
                [
                    option["option_id"],
                    option["carrier"],
                    option["flight_number"],
                    option["cabin"],
                    f"{option['arrival_delay_hours']}h",
                    "yes" if option["same_day_arrival"] else "no",
                    "yes" if option["approval_required"] else "no",
                ]
            )
        content = "\n\n".join(
            [
                f"**Passenger:** {passenger['passenger_name']} ({pnr}) - {passenger['tier']} / {passenger['cabin']}",
                f"**Current booking:** {booking['current_flight']} {booking['origin']} -> {booking['destination']}",
                f"**Operational notes:** {'; '.join(passenger['preference_notes'])}",
                f"**Policy view:** partner auto-approval threshold ${policy['partner_auto_approval_threshold_usd']}, hotel cap ${policy['overnight_hotel_cap_usd']}, manual review required = {'yes' if policy['manual_review_required'] else 'no'}.",
                "**Ranked recovery options**\n"
                + _markdown_table(
                    ["Option", "Carrier", "Flight", "Cabin", "Delay", "Same day", "Approval"],
                    rows,
                ),
                f"**Recommended option:** {recommended['option_id']} on {recommended['carrier']} {recommended['flight_number']} because it best balances arrival time, cabin protection, and traveler priority.",
            ]
        )
        travel_context.update(
            {
                "selected_pnr": pnr,
                "passenger_name": passenger["passenger_name"],
                "traveler_flags": ",".join(passenger.get("special_service_codes", [])),
                "booking": booking,
                "policy": policy,
                "ranked_options": ranked_options,
                "recommended_option": recommended,
            }
        )
        return _flow_result(state, "reaccommodation_flow", content, travel_context)

    def resolution_flow_node(state: AgentState) -> dict[str, Any]:
        travel_context = dict(state.get("travel_context") or {})
        recommended = travel_context.get("recommended_option")
        pnr = travel_context.get("selected_pnr")
        if not pnr or not recommended:
            return _flow_result(
                state,
                "resolution_flow",
                "No prepared passenger recommendation is available yet. Run passenger handling first.",
            )
        rounds = state.get("resolution_rounds", 0)
        if rounds >= 6:
            return _flow_result(
                state,
                "resolution_flow",
                "Resolution flow hit the safety cap before completion.",
            )

        system_prompt = build_resolution_system_prompt(travel_context)
        messages = list(state["messages"])
        if messages and isinstance(messages[-1], ToolMessage):
            llm_messages = [SystemMessage(content=system_prompt), *messages]
        else:
            llm_messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(
                    content=f"Complete the recovery plan for {pnr} using the recommended option."
                ),
            ]
        response = action_llm.invoke(llm_messages)
        if getattr(response, "tool_calls", None):
            return {
                "messages": [response],
                "current_flow": "resolution_flow",
                "resolution_rounds": rounds + 1,
            }
        travel_context["resolution_completed"] = True
        _queue_procedural_learning(state, travel_context, pnr=pnr)
        return _flow_result(
            state,
            "resolution_flow",
            _stringify(response.content),
            travel_context,
            wrap_in_heading=False,
        )

    def direct_response_node(state: AgentState) -> dict[str, Any]:
        memory_context = _sanitize_memory_context(state.get("memory_context", ""))
        travel_context = json.dumps(state.get("travel_context", {}), indent=2, default=str)
        system_prompt = DIRECT_RESPONSE_SYSTEM_PROMPT.format(
            travel_context=travel_context,
            memory_context=memory_context or "No retrieved memory.",
        )
        response = llm.invoke([SystemMessage(content=system_prompt), *state["messages"]])
        return {"messages": [response], "current_flow": "direct_response"}

    def respond_node(state: AgentState) -> dict[str, Any]:
        step_summaries = "\n\n".join(
            f"### {item['flow']}\n{item['output']}" for item in state.get("step_results", [])
        )
        system_prompt = FINAL_REPORT_SYSTEM_PROMPT.format(
            step_summaries=step_summaries or "No step outputs recorded.",
            memory_context=_sanitize_memory_context(state.get("memory_context", ""))
            or "No retrieved memory.",
        )
        response = llm.invoke([SystemMessage(content=system_prompt), *state["messages"]])
        return {"messages": [response], "current_flow": "respond"}

    def route_after_supervisor(
        state: AgentState,
    ) -> Literal["impact_flow", "reaccommodation_flow", "resolution_flow", "direct_response"]:
        route = state.get("current_flow", "direct_response")
        return route  # type: ignore[return-value]

    def route_after_impact(state: AgentState) -> Literal["supervisor", "end"]:
        return "supervisor" if _procedure_remaining(state) else "end"

    def route_after_reaccommodation(state: AgentState) -> Literal["supervisor", "end"]:
        return "supervisor" if _procedure_remaining(state) else "end"

    def route_after_resolution(
        state: AgentState,
    ) -> Literal["tools", "supervisor", "respond", "end"]:
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        if _procedure_remaining(state):
            return "supervisor"
        if state.get("current_procedure"):
            return "respond"
        return "end"

    def route_after_tools(state: AgentState) -> Literal["resolution_flow"]:
        return "resolution_flow"

    builder = StateGraph(AgentState)
    builder.add_node("discover", discover_node)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("impact_flow", impact_flow_node)
    builder.add_node("reaccommodation_flow", reaccommodation_flow_node)
    builder.add_node("resolution_flow", resolution_flow_node)
    builder.add_node("direct_response", direct_response_node)
    builder.add_node("respond", respond_node)
    builder.add_node("tools", ToolNode(all_tools))

    builder.add_edge(START, "discover")
    builder.add_edge("discover", "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {
            "impact_flow": "impact_flow",
            "reaccommodation_flow": "reaccommodation_flow",
            "resolution_flow": "resolution_flow",
            "direct_response": "direct_response",
        },
    )
    builder.add_conditional_edges(
        "impact_flow",
        route_after_impact,
        {"supervisor": "supervisor", "end": END},
    )
    builder.add_conditional_edges(
        "reaccommodation_flow",
        route_after_reaccommodation,
        {"supervisor": "supervisor", "end": END},
    )
    builder.add_conditional_edges(
        "resolution_flow",
        route_after_resolution,
        {
            "tools": "tools",
            "supervisor": "supervisor",
            "respond": "respond",
            "end": END,
        },
    )
    builder.add_conditional_edges(
        "tools",
        route_after_tools,
        {"resolution_flow": "resolution_flow"},
    )
    builder.add_edge("direct_response", END)
    builder.add_edge("respond", END)
    return builder.compile(checkpointer=app.checkpointer())


def main() -> None:
    _validate_env()
    logger.info("Starting %s", APP_NAME)
    app.run()


if __name__ == "__main__":
    main()
