"""
Recruiting Assistant Agent — Runner SDK example for Indeed cross-session intelligence demo.

Demonstrates:
- @app.tool decorator for secure, logged tools
- LangGraph for agent orchestration
- Memory-driven insight generation and cross-session learning
- Human-in-the-loop outreach approval via SuspendPayload
- Org-wide shared intelligence across recruiters

The platform launches this code in supported runtime roles:
    RUNNER_MODE=aer            -> Full LangGraph execution
    RUNNER_MODE=tool           -> Tool function execution
    RUNNER_MODE=memory-server  -> Long-term memory service

See env.example for configuration options.
"""

import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional, TypedDict

import httpx
from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from magenta_sdklanggraph import App
from pydantic import SecretStr
from runner_shared.context import (
    get_current_execution_id,
    get_current_oe_url,
    get_current_session_id,
    get_current_user_id,
)
from runner_shared.models import SuspendPayload
from runner_shared.utils import normalize_content

from . import tools as recruiting_tools

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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "")
CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "")
CEREBRAS_MODEL = os.environ.get("CEREBRAS_MODEL", "qwen-3-235b-a22b-instruct-2507")

APP_NAME = "Recruiting Assistant Agent"
ORG_ID = os.environ.get("ORG_ID", "").strip()
PROJECT_ID = os.environ.get("PROJECT_ID", "").strip()
AGENT_ID = os.environ.get("WORKSPACE_ID", "recruiting-assistant-agent").strip()

app = App(
    app_name=APP_NAME,
    enable_tracing=ENABLE_TRACING,
    enable_memory=ENABLE_MEMORY,
    enable_guardrails=ENABLE_GUARDRAILS,
)
logger.info("TenantRuntime created")


def _runtime_org_id() -> str:
    return os.environ.get("ORG_ID", ORG_ID).strip()


def _runtime_project_id() -> str:
    return os.environ.get("PROJECT_ID", PROJECT_ID).strip()


def _validate_env() -> None:
    missing = [
        name
        for name, value in (
            ("ORG_ID", _runtime_org_id()),
            ("PROJECT_ID", _runtime_project_id()),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"{', '.join(missing)} must be set in the environment")


def _build_runtime_llm(temperature: float = 0.0) -> BaseChatModel:
    """Create the default chat model from environment configuration."""
    cerebras_api_key = os.environ.get("CEREBRAS_API_KEY", CEREBRAS_API_KEY)
    cerebras_model = os.environ.get("CEREBRAS_MODEL", CEREBRAS_MODEL)
    gemini_api_key = os.environ.get("GEMINI_API_KEY", GEMINI_API_KEY)
    gemini_model = os.environ.get("GEMINI_MODEL", GEMINI_MODEL)
    openai_api_key = os.environ.get("OPENAI_API_KEY", OPENAI_API_KEY)
    openai_model = os.environ.get("OPENAI_MODEL", OPENAI_MODEL)
    openai_base_url = os.environ.get("OPENAI_BASE_URL", OPENAI_BASE_URL)
    provider = _select_llm_provider(cerebras_api_key, gemini_api_key, openai_api_key)

    if provider == "cerebras":
        from langchain_cerebras import ChatCerebras

        logger.info("Using Cerebras LLM: %s, temperature=%s", cerebras_model, temperature)
        if "qwen-3-32b" in cerebras_model.lower():
            return ChatCerebras(
                api_key=SecretStr(cerebras_api_key),
                model=cerebras_model,
                temperature=temperature,
                disable_reasoning=True,
            )
        return ChatCerebras(
            api_key=SecretStr(cerebras_api_key),
            model=cerebras_model,
            temperature=temperature,
        )

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        logger.info("Using Gemini LLM: %s, temperature=%s", gemini_model, temperature)
        return ChatGoogleGenerativeAI(
            api_key=SecretStr(gemini_api_key),
            model=gemini_model,
            temperature=temperature,
            thinking_budget=0,
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        logger.info("Using OpenAI LLM: %s, temperature=%s", openai_model, temperature)
        return ChatOpenAI(
            **_openai_llm_kwargs(openai_api_key, openai_model, temperature, openai_base_url)
        )

    raise ValueError(
        "No LLM API key configured. Set one of: CEREBRAS_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY"
    )


def _select_llm_provider(
    cerebras_api_key: str,
    gemini_api_key: str,
    openai_api_key: str,
) -> Literal["cerebras", "gemini", "openai"] | None:
    if cerebras_api_key:
        return "cerebras"
    if gemini_api_key:
        return "gemini"
    if openai_api_key:
        return "openai"
    return None


def _openai_llm_kwargs(
    openai_api_key: str,
    openai_model: str,
    temperature: float,
    openai_base_url: str,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "api_key": SecretStr(openai_api_key),
        "model": openai_model,
        "temperature": temperature,
    }
    if openai_base_url:
        if "grove-foundry" in openai_base_url:
            kwargs["base_url"] = openai_base_url.split("/v1")[0] + "/v1"
            kwargs["default_headers"] = {"api-key": openai_api_key}
        else:
            kwargs["base_url"] = openai_base_url.rstrip("/")
        if "/openai/deployments/" in openai_base_url.lower():
            api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
            kwargs["default_query"] = {"api-version": api_version}
            kwargs["default_headers"] = {"api-key": openai_api_key}
    return kwargs


def _get_llm(temperature: float = 0.0) -> BaseChatModel:
    """Create the wrapped chat model used by the agent graph."""
    return app.llm(_build_runtime_llm(temperature=temperature))


def _empty_policy_result() -> dict[str, Any]:
    return {
        "require_review": False,
        "triggered": False,
        "reasons": [],
        "triggered_conditions": [],
    }


def _validate_policy(context: dict[str, Any]) -> dict[str, Any]:
    """Validate structured context with the guardrails server when available."""
    guardrails_client = getattr(app._runtime, "guardrails_client", None)
    if guardrails_client is None:
        return _empty_policy_result()

    payload = {"org_id": _runtime_org_id(), "context": context}
    try:
        from runner_shared.context import get_validation_context

        result = guardrails_client.validate_output(
            json.dumps(payload, sort_keys=True),
            context=get_validation_context(),
        )
        if getattr(result, "validation_passed", True):
            return _empty_policy_result()

        reasons = getattr(result, "errors", None) or [getattr(result, "validator_name", "policy")]
        return {
            "require_review": True,
            "triggered": True,
            "reasons": [str(reason) for reason in reasons],
            "triggered_conditions": [getattr(result, "validator_name", "policy")],
        }
    except Exception as exc:  # noqa: BLE001 - outreach approval should still suspend
        logger.warning("Policy validation failed: %s", exc)
        result = _empty_policy_result()
        result["validation_error"] = str(exc)
        return result


# =============================================================================
# Tools — @app.tool for secure, logged execution
# =============================================================================

search_candidates = app.tool(is_local=False)(recruiting_tools.search_candidates)
generate_outreach_batch = app.tool(is_local=False)(recruiting_tools.generate_outreach_batch)
analyze_funnel_insight = app.tool(is_local=False)(recruiting_tools.analyze_funnel_insight)
send_email = app.tool(is_local=False)(recruiting_tools.send_email)


@app.tool(is_local=True)
def request_outreach_approval(
    candidate_ids: str,
    role: str,
    company: str,
    outreach_summary: str,
    outreach_drafts: str = "[]",
) -> str:
    """Submit outreach drafts for manager/recruiter approval before sending.

    Suspends the agent execution until a human reviewer approves or rejects
    the outreach batch. Use this after generate_outreach_batch produces drafts.
    Validates the batch against policy guardrails before suspending.

    Args:
        candidate_ids: Comma-separated candidate IDs in the batch (e.g. "C001,C002,C003")
        role: Role being recruited for
        company: Hiring company name
        outreach_summary: Brief summary of the outreach batch for the reviewer
        outreach_drafts: JSON array of draft objects from generate_outreach_batch.
            Each object should have: candidate_id, candidate_name, email, subject, body.
    """
    ids = [cid.strip() for cid in candidate_ids.split(",") if cid.strip()]
    task_id = f"OUTREACH-{uuid.uuid4().hex[:8].upper()}"
    company = company if company and company.lower() not in ("your company", "") else "MongoDB"

    all_candidates = {c["candidate_id"]: c for c in recruiting_tools._candidates()}
    candidates_detail = []
    for cid in ids:
        cand = all_candidates.get(cid, {})
        candidates_detail.append(
            {
                "candidate_id": cid,
                "name": cand.get("name", cid),
                "email": cand.get("email", ""),
                "current_title": cand.get("current_title", ""),
                "current_company": cand.get("current_company", ""),
            }
        )

    guardrail_result = _validate_policy(
        {
            "batch_size": len(ids),
            "role": role,
            "company": company,
        }
    )

    guardrail_triggered = bool(guardrail_result.get("require_review"))
    guardrail_reasons = guardrail_result.get("reasons", [])

    if guardrail_triggered:
        logger.info("Guardrail triggered for outreach approval: reasons=%s", guardrail_reasons)

    candidate_names = [c["name"] for c in candidates_detail]

    try:
        parsed_drafts = json.loads(outreach_drafts) if outreach_drafts else []
    except (json.JSONDecodeError, TypeError):
        parsed_drafts = []

    drafts_for_review = [
        {
            "candidate_id": d.get("candidate_id", ""),
            "candidate_name": d.get("candidate_name", ""),
            "email": d.get("email", ""),
            "subject": d.get("subject", ""),
            "body": d.get("body", ""),
        }
        for d in parsed_drafts
        if isinstance(d, dict) and d.get("candidate_id") in ids
    ]

    return SuspendPayload(
        suspend_reason="outreach_approval_required",
        suspend_context={
            "task_id": task_id,
            "decision_type": "outreach_batch_approval",
            "candidate_ids": ids,
            "candidates": candidates_detail,
            "outreach_drafts": drafts_for_review,
            "role": role,
            "company": company,
            "outreach_summary": outreach_summary,
            "guardrail_triggered": guardrail_triggered,
            "guardrail_reasons": guardrail_reasons,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "instructions": (
                f"Review outreach batch for {role} at {company}. "
                f"Candidates: {', '.join(candidate_names)}. Approve or reject with notes."
            ),
        },
    ).to_json()


def save_conversation_summary(title: str, summary: str, tags: str = "") -> str:
    """Save a summary of the current conversation as an episodic memory.

    Args:
        title: Brief title for this conversation
        summary: Summary of what was discussed and any outcomes
        tags: Comma-separated tags for categorization
    """
    user_id = app.get_current_user_id()
    if not user_id:
        return json.dumps(
            {"status": "error", "message": "No active user context available."},
            indent=2,
        )

    session_id = get_current_session_id()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    episode_id = app.memory.save_episode(
        title=title,
        content=summary,
        summary=summary,
        participants=["Recruiter", "Recruiting Assistant Agent (AI)"],
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


def save_learned_insight(insight_text: str, tags: list[str], visibility: str = "org") -> str:
    """Persist a learned insight as a semantic memory.

    Called when the agent detects actionable feedback from the recruiter
    (e.g. preference for startup experience, negative signal for research-only).

    Args:
        insight_text: Structured insight content to persist
        tags: Categorisation tags
        visibility: "private" for recruiter-specific, "org" for shared
    """
    user_id = app.get_current_user_id()
    if not user_id:
        return json.dumps(
            {"status": "error", "message": "No active user context available."},
            indent=2,
        )

    saved = app.memory.save_semantic(
        text=insight_text,
        label="learned_recruiter_insight",
        source="recruiter_feedback",
        user_id=user_id,
        visibility=visibility,
        metadata={"tags": tags, "created_at": datetime.now(timezone.utc).isoformat()},
    )

    if saved:
        return json.dumps({"status": "saved", "visibility": visibility}, indent=2)
    return json.dumps({"status": "error", "message": "Failed to save insight."}, indent=2)


# =============================================================================
# Guardrail explanation helper
# =============================================================================

_GUARDRAIL_EXPLANATION_PROMPT = """\
You are a helpful recruiting assistant. Your previous response was blocked by a \
content safety policy called "{policy_name}".

The user's question was:
"{user_question}"

Write a short, friendly response (3-5 sentences) that:
1. Acknowledges you can't help with that specific request and briefly explains why \
(in plain language — do NOT mention the policy name or internal system details).
2. Suggests 2-3 specific alternative questions the user CAN ask you instead, \
related to their original intent.

Keep a warm, professional tone. Use markdown formatting for the alternatives.\
"""


def _generate_guardrail_explanation(
    blocked_content: str,
    messages: list,
    llm: Any,
) -> AIMessage:
    """Use the LLM to produce a friendly, context-aware guardrail rejection."""
    import re

    policy_match = re.search(r"\[GUARDRAIL_BLOCKED:([^\]]+)\]", blocked_content)
    policy_name = policy_match.group(1) if policy_match else "content policy"

    user_question = ""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            user_question = normalize_content(getattr(msg, "content", "")) or ""
            break

    explanation_prompt = _GUARDRAIL_EXPLANATION_PROMPT.format(
        policy_name=policy_name,
        user_question=user_question,
    )

    try:
        result = llm.invoke(
            [
                SystemMessage(content=explanation_prompt),
            ]
        )
        return AIMessage(
            content=normalize_content(getattr(result, "content", "")) or blocked_content
        )
    except Exception:
        logger.warning("Failed to generate guardrail explanation, using fallback")
        return AIMessage(
            content=(
                "I'm not able to help with that specific request due to a content "
                "policy, but I'm happy to assist with other recruiting questions! "
                "Try asking about candidate skills, role fit, or outreach strategies."
            )
        )


# =============================================================================
# Agent Definition
# =============================================================================


class _AgentStateOptional(TypedDict, total=False):
    user_id: Optional[str]
    session_id: Optional[str]
    memory_context: str


class AgentState(_AgentStateOptional):
    messages: Annotated[list[BaseMessage], add_messages]


SYSTEM_PROMPT = """\
You are a Recruiting Assistant for technical hiring at **MongoDB**. You help \
MongoDB's recruiting team find, evaluate, and reach out to engineering candidates \
using data-driven insights. All outreach is sent on behalf of MongoDB's \
engineering recruiting team.

## Your Capabilities
- **search_candidates**: Search the candidate pool with skill filters and preference-based \
ranking. Returns scored results with match evidence.
- **generate_outreach_batch**: Create personalised outreach drafts for multiple candidates. \
Tailors messages to each candidate's background. Use "MongoDB" as the company.
- **request_outreach_approval**: Submit outreach drafts for manager review before sending \
(human-in-the-loop). Outreach CANNOT be sent without approval.
- **send_email**: Send approved outreach emails to candidates. Accepts comma-separated \
candidate IDs to send all emails in one call.
- **analyze_funnel_insight**: Analyse hiring funnel data for a role family and location. \
Returns drop-off stages, rejection reasons, and actionable recommendations.

## Outreach Workflow
When the recruiter asks to draft or send outreach to candidates, you MUST follow this \
exact sequence. Do NOT deviate or skip any step:
1. Call `generate_outreach_batch` with the candidate IDs, role, and company="MongoDB". \
**You MUST NOT write outreach email content yourself — ALWAYS use this tool.**
2. Display the full email drafts to the recruiter. For EACH candidate, show: \
the candidate name, email address, subject line, and the complete email body. \
Do NOT call any other tools in this step — your ONLY job is to present the drafts \
as formatted text so the recruiter can read them. STOP here and wait.
3. When the recruiter confirms or asks to send, call `request_outreach_approval` \
with the candidate_ids, role, company="MongoDB", a brief outreach_summary, and \
the outreach_drafts (pass the full `drafts` array from the `generate_outreach_batch` \
response as a JSON string). This is MANDATORY — outreach cannot be sent without \
manager approval.
4. After manager approval, call `send_email` ONCE with ALL approved candidate IDs \
(comma-separated) and the subject line. This sends all emails in a single call. \
Then confirm to the recruiter that the emails have been sent.
5. After rejection, explain the rejection reason and suggest adjustments.

**CRITICAL**: Never skip step 2. The recruiter MUST see the email drafts before approval \
is requested. Never call `request_outreach_approval` in the same turn as \
`generate_outreach_batch`. Never compose email text in your response — always delegate \
to `generate_outreach_batch`.

## Insight Generation
When a recruiter provides feedback on search results (e.g. "too academic", "need startup \
experience", "prefer candidates who shipped products"), you MUST:
1. Acknowledge the feedback explicitly
2. Extract structured preferences: what to boost, what to avoid, and why
3. Indicate that these preferences are being saved as durable insights
4. Immediately apply them to the next search without the recruiter repeating themselves

## Understanding Background Context
Your background context may contain different types of information. Interpret them as follows:

**Candidate Profiles** (source: candidate_profile)
Factual profiles of real candidates in the talent pool. Use as ground truth when answering \
candidate queries. Always link to the candidate's resume_ref when referencing them. \
Rank candidates by how well their skills and experience match the recruiter's query.

**Org-Wide Hiring Insights** (source: funnel_analytics)
Data-driven insights from historical hiring pipeline analytics (e.g. drop-off rates, \
rejection reasons, bottleneck stages). Proactively surface these when relevant to the \
current query. Cite the data period and candidate count when available.

**Domain Terminology** (source: taxonomic)
Definitions of recruiting terms in context. Use to interpret terminology correctly and \
to expand search criteria across role families.

**Learned Preferences** (source: recruiter_feedback)
Preferences extracted from prior recruiter interactions. Apply them automatically without \
requiring the recruiter to repeat themselves. Show "Applied insight: <description>" on \
results that benefited from a learned preference.

## Response Format
Always format responses in **markdown**:
- **Tables**: Use markdown tables for candidate comparisons and rankings
- **Lists**: Use bullet lists for recommendations and insights
- **Emphasis**: Use **bold** for key scores, names, and metrics
- **Headings**: Use ## or ### for distinct sections

## Citation Format
When your response uses data from background knowledge or tool results, add numbered \
footnote markers [1], [2], etc. at the end of the relevant sentence.

At the END of your response, add a divider and a **Sources** section:

---
**Sources**
- [1] Source System — Description

Rules:
- Only cite sources from Available Sources or tool results
- For tool results: [N] Candidate Search — {role} Results
- NEVER use memory-type labels (Semantic Memory, Episodic Memory, etc.) as source names
- Number sources sequentially starting from [1]

## Candidate Profile Links
In the **Sources** section, make candidate names clickable using their `profile_url` \
from the search results. ONLY add links to "Candidate Profiles" sources — never to \
Domain Terminology, Hiring Funnel Analytics, or other non-candidate sources. Do NOT \
put profile links in the results table.

Example:
- [1] Domain Terminology — Role Families: Robotics
- [2] Candidate Profiles — [Marcus Chen](profile_url from search_candidates)
- [3] Candidate Profiles — [Raj Patel](profile_url from search_candidates)

## Important
Do NOT end responses with follow-up questions like "Would you like me to…?" or "Shall I…?". \
Deliver your analysis and stop. The user will ask if they need more.
"""


_MEMORY_TYPE_HEADER_RE = re.compile(
    r"^#{1,3}\s*(?:Semantic Memory|Taxonomic Memory|Episodic Memory)\s*$",
    re.MULTILINE,
)


def _sanitize_memory_context(text: str) -> str:
    """Strip memory-type section headers so the LLM can't latch onto them as source names."""
    return _MEMORY_TYPE_HEADER_RE.sub("", text)


def _renumber_citations(text: str) -> str:
    """Renumber citation markers to be sequential starting from [1]."""
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
    "candidate_profile": "Candidate Profiles",
    "recruiter_feedback": "Recruiter Insights",
    "funnel_analytics": "Hiring Funnel Analytics",
    "data_ontology": "Domain Terminology",
}


def _build_citation_index(raw_memories: list) -> str:
    """Build a numbered citation index from raw mongomem MemoryChunk objects."""
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
            ctx_meta = meta.get("contextual_metadata", {})
            display_name = (
                ctx_meta.get("candidate_name", "") if src_key == "candidate_profile" else ""
            )
        elif mem_type == "taxonomic":
            src_key = "data_ontology"
            lbl_key = f"{meta.get('domain', '')}:{meta.get('term', '')}"
            display_name = ""
        else:
            continue

        dedup_key = (src_key, lbl_key)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        idx += 1
        src = _SOURCE_DISPLAY_NAMES.get(src_key, src_key)
        lbl = display_name or lbl_key.replace("_", " ").title()
        lines.append(f"[{idx}] {src} — {lbl}")
    return "\n".join(lines)


def _extract_latest_user_query(messages: list[BaseMessage]) -> str:
    """Extract the most recent user message content from the message list."""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return msg.content if isinstance(msg.content, str) else str(msg.content)
    return ""


def _emit_memory_context_node(
    oe_url: str,
    execution_id: str,
    session_id: Optional[str],
    user_id: Optional[str],
    query: str,
    memory_context: str,
    duration_ms: float,
) -> None:
    """Emit a custom node execution for memory_context to the OE."""
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    payload_started: dict = {
        "execution_id": execution_id,
        "node_name": "memory_context",
        "status": "started",
        "timestamp": started_at.isoformat(),
        "run_id": run_id,
        "session_id": session_id,
        "thread_id": session_id,
        "user_id": user_id,
        "inputs": {"query": query},
    }
    payload_success: dict = {
        "execution_id": execution_id,
        "node_name": "memory_context",
        "status": "success",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "session_id": session_id,
        "thread_id": session_id,
        "user_id": user_id,
        "outputs": {"memory_context": memory_context},
        "duration_ms": duration_ms,
    }
    try:
        with httpx.Client(timeout=2.0) as client:
            client.post(f"{oe_url}/node/execution", json=payload_started)
            client.post(f"{oe_url}/node/execution", json=payload_success)
    except Exception as e:
        logger.debug("Failed to emit memory_context node event to OE: %s", e)


def _memory_engine() -> Any | None:
    """Return the underlying MemoryEngine when memory is enabled."""
    return getattr(app._runtime, "memory_engine", None)


def _build_memory_context_response(
    *,
    query: str,
    user_id: str,
    session_id: str,
) -> Any | None:
    """Build memory context with selected memories when the engine exposes them."""
    memory_engine = _memory_engine()
    if memory_engine is not None and session_id:
        try:
            return memory_engine.build_context(
                query=query,
                session_id=session_id,
                org_id=_runtime_org_id(),
                user_id=user_id,
                include_memories=True,
            )
        except Exception as exc:  # noqa: BLE001 - memory recall should fail open
            logger.warning("Failed to build rich memory context: %s", exc)

    try:
        return app.memory.build_context(
            query=query,
            user_id=user_id,
            thread_id=session_id or None,
        )
    except Exception as exc:  # noqa: BLE001 - memory recall should fail open
        logger.warning("Failed to build memory context: %s", exc)
        return None


@app.entrypoint
def build_agent(llm: Optional[BaseChatModel] = None) -> CompiledStateGraph:
    """Build the LangGraph agent.

    Args:
        llm: LLM to use. Defaults to the configured wrapped model when None.
    """
    _validate_env()
    logger.info("Building LangGraph agent...")

    if llm is None:
        llm = _get_llm(temperature=0)

    tools = app.get_tools()
    llm_with_tools = llm.bind_tools(app.get_tool_schemas())

    def agent_node(state: AgentState) -> AgentState:
        messages = state["messages"]
        user_id = state.get("user_id") or ""
        session_id = state.get("session_id") or ""

        latest_user_query = _extract_latest_user_query(messages)
        is_first_pass = not messages or not isinstance(messages[-1], ToolMessage)
        citation_index = ""

        if is_first_pass and user_id and latest_user_query:
            memory_context = ""
            logger.info(
                "Recruiting assistant: building memory context for query: %.80s...",
                latest_user_query,
            )
            t0 = time.perf_counter()
            ctx_result: Any = _build_memory_context_response(
                query=latest_user_query,
                user_id=user_id,
                session_id=session_id,
            )
            duration_ms = (time.perf_counter() - t0) * 1000.0

            if hasattr(ctx_result, "formatted_context"):
                memory_context = str(getattr(ctx_result, "formatted_context", "") or "")
                raw_memories = getattr(ctx_result, "selected_memories", []) or []
                citation_index = _build_citation_index(raw_memories)
            else:
                memory_context = ctx_result if isinstance(ctx_result, str) else ""

            if memory_context:
                logger.info(
                    "Recruiting assistant: memory context built (%d chars, %d sources)",
                    len(memory_context),
                    len(citation_index.splitlines()) if citation_index else 0,
                )
            else:
                logger.info("Recruiting assistant: no memory context available")

            oe_url = get_current_oe_url()
            execution_id = get_current_execution_id()
            if oe_url and execution_id:
                _emit_memory_context_node(
                    oe_url=oe_url,
                    execution_id=execution_id,
                    session_id=session_id or get_current_session_id(),
                    user_id=user_id or get_current_user_id(),
                    query=latest_user_query,
                    memory_context=memory_context,
                    duration_ms=duration_ms,
                )
        else:
            memory_context = state.get("memory_context", "")

        human_count = sum(1 for m in messages if isinstance(m, HumanMessage))
        logger.info("Recruiting assistant: human_count=%d", human_count)

        system_prompt = SYSTEM_PROMPT
        if memory_context:
            available_sources = (
                f"\n## Available Sources\n{citation_index}" if citation_index else ""
            )
            prompt_memory_context = _sanitize_memory_context(memory_context)
            system_prompt += """
            {available_sources}

            ## Background Context (internal — do not reproduce these headings or labels)
            {memory_context}

            **IMPORTANT**:
            If the background context above contains information that directly answers the \
user's question, use that information to respond WITHOUT calling tools.
            Use the source numbers from Available Sources when citing data. Never copy section \
titles, memory-type labels, or retrieval headers into your response or Sources list.
            Only call tools when you need data not available in the background context above.
            """.format(memory_context=prompt_memory_context, available_sources=available_sources)

        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=system_prompt)] + list(messages)
        else:
            messages = [SystemMessage(content=system_prompt)] + list(messages[1:])

        response = llm_with_tools.invoke(messages)
        response = app.validate_llm_response(response)

        content_str = normalize_content(getattr(response, "content", ""))
        guardrail_blocked = bool(content_str and "[GUARDRAIL_BLOCKED:" in content_str)

        if guardrail_blocked:
            response = _generate_guardrail_explanation(content_str, messages, llm)

        # Extract insights only when the response was NOT blocked by guardrails
        if is_first_pass and not guardrail_blocked:
            _maybe_extract_insight(latest_user_query, llm)

        # Auto-save conversation summary after sufficient interaction
        if not getattr(response, "tool_calls", None) and human_count >= 2 and human_count % 2 == 0:
            _save_auto_summary(messages, llm)

        if isinstance(response.content, str):
            response.content = _renumber_citations(response.content)

        return {"messages": [response], "memory_context": memory_context}

    def should_continue(state: AgentState) -> Literal["tools", "end"]:
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else "end"

    logger.info("  -> Compiling graph...")
    builder = StateGraph(AgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", should_continue, {"tools": "tools", "end": END})
    builder.add_edge("tools", "agent")

    graph = builder.compile(checkpointer=app.checkpointer())
    logger.info("Agent graph compiled")
    return graph


# =============================================================================
# Insight extraction helpers
# =============================================================================

_FEEDBACK_PATTERNS = [
    r"too (academic|research|theoretical)",
    r"need.*(startup|shipped|production|hands.?on)",
    r"prefer.*(startup|shipped|production|practical)",
    r"not .*(right|good|fit).*(too|because|they)",
    r"avoid.*(research.?only|publications.?heavy|academic)",
    r"I (want|like|prefer|need)",
]
_FEEDBACK_RE = re.compile("|".join(_FEEDBACK_PATTERNS), re.IGNORECASE)


def _maybe_extract_insight(user_message: str, llm: BaseChatModel) -> None:
    """Detect recruiter feedback and persist it as a semantic memory insight."""
    if not _FEEDBACK_RE.search(user_message):
        return

    try:
        resp = llm.invoke(
            [
                SystemMessage(
                    content=(
                        "You are a preference extraction engine. The recruiter just gave feedback. "
                        "Extract structured preferences as JSON with keys: "
                        '"boost" (list of signals to rank higher), '
                        '"avoid" (list of signals to rank lower), '
                        '"reasoning" (why, in one sentence). '
                        "Respond ONLY with valid JSON, no markdown fencing."
                    )
                ),
                HumanMessage(content=user_message),
            ]
        )
        raw = resp.content
        if isinstance(raw, list):
            raw = "".join(
                block.get("text", "") if isinstance(block, dict) else str(block) for block in raw
            )
        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", raw, re.DOTALL)
        if fence_match:
            raw = fence_match.group(1)

        parsed = json.loads(raw)
        insight_text = json.dumps(parsed, indent=2)
        tags = parsed.get("boost", []) + parsed.get("avoid", [])

        save_learned_insight(
            insight_text=insight_text,
            tags=[str(t) for t in tags],
            visibility="private",
        )
        logger.info("Learned insight extracted and saved from recruiter feedback")
    except Exception:
        logger.warning("Failed to extract insight from feedback", exc_info=True)


def _save_auto_summary(messages: list[BaseMessage], llm: BaseChatModel) -> None:
    """Auto-save a conversation summary as an episodic memory."""
    try:
        conv_text = "\n".join(
            f"{'User' if isinstance(m, HumanMessage) else 'Agent'}: "
            + (m.content[:300] if isinstance(m.content, str) else str(m.content)[:300])
            for m in messages
            if isinstance(m, HumanMessage) or (m.content and not getattr(m, "tool_calls", None))
        )
        summary_resp = llm.invoke(
            [
                SystemMessage(
                    content=(
                        "Summarize this recruiting conversation focusing on actionable insights. "
                        "Cover: (1) roles searched, (2) key preferences learned, "
                        "(3) candidates discussed, (4) outreach decisions, "
                        "(5) any funnel insights surfaced. "
                        "Write as if briefing a colleague. "
                        "Also produce a short title (<=10 words) and comma-separated tags. "
                        'Respond as JSON: {"title": "...", "summary": "...", "tags": "..."}'
                    )
                ),
                HumanMessage(content=conv_text),
            ]
        )
        raw = summary_resp.content
        if isinstance(raw, list):
            raw = "".join(
                block.get("text", "") if isinstance(block, dict) else str(block) for block in raw
            )
        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", raw, re.DOTALL)
        if fence_match:
            raw = fence_match.group(1)
        parsed = json.loads(raw)
        save_conversation_summary(
            title=parsed["title"],
            summary=parsed["summary"],
            tags=parsed.get("tags", ""),
        )
        logger.info("Conversation summary auto-saved")
    except Exception:
        logger.warning("Failed to auto-save conversation summary", exc_info=True)


# =============================================================================
# Run
# =============================================================================


def main():
    """Main entry point."""
    _validate_env()
    logger.info("=" * 60)
    logger.info(f"Starting {APP_NAME} (Runner SDK)")
    logger.info("=" * 60)

    app.run()


if __name__ == "__main__":
    main()
