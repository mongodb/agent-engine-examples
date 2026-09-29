"""
Insurance agent starter template built on the SDK for Google ADK 2.

This template demonstrates:
- @app.tool for structured tool registration
- agent.yaml-driven runtime features and tool placement
- MongoDB-backed policy and claim storage
- Native ADK human-in-the-loop: LongRunningFunctionTool review waits and
  require_confirmation policy binding, suspended and resumed by the platform

The platform launches this code in supported runtime roles:
    RUNNER_MODE=aer            -> Full ADK execution
    RUNNER_MODE=tool           -> Tool function execution
    RUNNER_MODE=memory-server  -> Long-term memory service
"""

from __future__ import annotations

import json
import logging
import os
import random
import sys
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.long_running_tool import LongRunningFunctionTool
from google.genai import types
from agent_engine_sdk_adk import App

from insurance_agent.llm import build_llm
from insurance_agent.policy_store import (
    Claim,
    Policy,
    create_claim_store,
    create_policy_store,
)

# =============================================================================
# Logging Setup
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)
load_dotenv()

# =============================================================================
# Configuration
# =============================================================================

# MongoDB config - application's own database for policies, claims, etc.
MONGODB_URI = os.environ.get("MONGODB_URI", "")
MONGODB_DATABASE = os.environ.get("MONGODB_DATABASE", "insurance_agent")


# =============================================================================
# Runtime Setup
# =============================================================================
APP_NAME = "insurance-agent-adk"

app = App(
    app_name=APP_NAME,
    app_version="1.0.0",
)
logger.info("✅ App created")

policy_store = create_policy_store(MONGODB_URI, MONGODB_DATABASE)
claim_store = create_claim_store(MONGODB_URI, MONGODB_DATABASE)


def _current_user_id() -> str:
    """Return the current request's user id; the platform always supplies one."""
    user_id = app.get_current_user_id()
    if not user_id:
        raise RuntimeError("user_id is required but missing from the current request")
    return user_id


# =============================================================================
# Risk Assessment Constants (for claims analysis)
# =============================================================================

# Keywords in claim description that indicate high risk
HIGH_RISK_KEYWORDS = ["total loss", "totaled", "stolen", "theft", "fire", "flood"]

# Keywords in claim description that indicate medium risk
MEDIUM_RISK_KEYWORDS = ["significant damage", "major repair", "structural"]

# Numeric thresholds for risk classification
LOW_RISK_THRESHOLD = 1000  # Claims under $1,000 (with low risk) can be auto-approved
MEDIUM_RISK_THRESHOLD = 5000  # Claims $5,000+ classify as medium risk
HIGH_RISK_THRESHOLD = 15000  # Claims $15,000+ classify as high risk

# Risk score thresholds (0.0 - 1.0)
MEDIUM_RISK_SCORE = 0.4
HIGH_RISK_SCORE = 0.6

HUMAN_REVIEW_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["decision"],
    "properties": {
        "decision": {
            "type": "string",
            "title": "Decision",
            "description": "Approve or deny this claim.",
            "enum": ["approved", "denied"],
        },
        "reviewer_notes": {
            "type": "string",
            "title": "Reviewer notes",
        },
    },
    "additionalProperties": False,
}


class _SchemaLongRunningFunctionTool(LongRunningFunctionTool):
    """Expose the human response schema on an ADK long-running declaration."""

    def __init__(
        self,
        func: Callable[..., Any],
        response_schema: dict[str, Any],
    ) -> None:
        super().__init__(func)
        self._response_schema = response_schema

    def _get_declaration(self) -> types.FunctionDeclaration | None:
        declaration = super()._get_declaration()
        if declaration is not None:
            declaration.response_json_schema = dict(self._response_schema)
        return declaration


INSURANCE_TERMS: dict[str, dict[str, Any]] = {
    "deductible": {
        "term": "deductible",
        "definition": "The amount you pay out of pocket before insurance coverage applies.",
        "related_terms": ["premium", "coverage"],
    },
    "premium": {
        "term": "premium",
        "definition": "The recurring amount you pay to keep a policy in force.",
        "related_terms": ["deductible", "coverage"],
    },
    "liability": {
        "term": "liability",
        "definition": "Coverage for damage or injury you cause to other people or their property.",
        "related_terms": ["collision", "comprehensive"],
    },
    "collision": {
        "term": "collision",
        "definition": "Coverage for damage to your vehicle from a crash with another vehicle or object.",
        "related_terms": ["liability", "comprehensive"],
    },
    "comprehensive": {
        "term": "comprehensive",
        "definition": "Full coverage including theft, weather, vandalism, and most non-collision damage.",
        "related_terms": ["collision", "liability"],
    },
}


# =============================================================================
# Tools - Using @app.tool for secure, logged execution
# =============================================================================


@app.tool()
def lookup_policy(policy_number: str) -> str:
    """Look up an insurance policy by policy number."""
    policy = policy_store.get_policy(policy_number, _current_user_id())
    if policy:
        return json.dumps(policy, indent=2)
    return json.dumps({"error": f"No policy found: {policy_number}"})


@app.tool()
def get_quote(
    vehicle_year: int,
    vehicle_make: str,
    vehicle_model: str,
    coverage_level: str,
    driver_age: int = 0,
) -> str:
    """Generate a personalized auto insurance quote.

    Uses customer memory to apply loyalty discounts and personalize pricing.

    Args:
        vehicle_year: Year of the vehicle (e.g., 2022)
        vehicle_make: Make of the vehicle (e.g., "Toyota")
        vehicle_model: Model of the vehicle (e.g., "Camry")
        coverage_level: Coverage level (Basic, Standard, Comprehensive)
        driver_age: Primary driver's age (optional, affects pricing)
    """
    user_id = _current_user_id()

    # Base pricing by coverage level
    base_prices = {"basic": 80, "standard": 120, "comprehensive": 180}
    base = base_prices.get(coverage_level.lower(), 120)

    # Vehicle age factor (newer cars cost more to insure)
    current_year = datetime.now().year
    vehicle_age = current_year - vehicle_year
    if vehicle_age <= 2:
        base *= 1.2  # New car premium
    elif vehicle_age >= 10:
        base *= 0.85  # Older car discount

    # Age factor (young drivers pay more)
    if driver_age > 0:
        if driver_age < 25:
            base *= 1.5  # Young driver surcharge
        elif driver_age >= 55:
            base *= 0.9  # Senior discount

    # Check memory for returning customer benefits
    loyalty_discount = 0
    customer_notes: list[str] = []

    # Check if customer has existing policies (true returning customer indicator)
    existing_policies = policy_store.list_by_user_id(user_id)
    has_existing_policies = len(existing_policies) > 0

    # Check episodic memory for past conversations
    past_episodes = app.memory.search_episodes(
        query="policy quote conversation",
        user_id=user_id,
        top_k=3,
    )
    has_past_interactions = len(past_episodes) > 0 if past_episodes else False

    # Only apply loyalty discount if customer has existing policies OR past interactions
    if has_existing_policies or has_past_interactions:
        customer_notes.append("Returning customer - 10% loyalty discount applied")
        loyalty_discount = 0.10

        # Check semantic memory for good driving record
        context: Any = app.memory.build_context(
            query="driving history record accidents",
            user_id=user_id,
        )
        memory_context_raw = getattr(context, "formatted_context", context)
        memory_context = memory_context_raw if isinstance(memory_context_raw, str) else ""
        if memory_context:
            if (
                "clean driving" in memory_context.lower()
                or "no accidents" in memory_context.lower()
            ):
                customer_notes.append("Good driver discount - additional 5% off")
                loyalty_discount += 0.05

    # Apply loyalty discount
    final_price = base * (1 - loyalty_discount)

    quote: dict[str, Any] = {
        "quote_id": f"QT-{random.randint(100000, 999999)}",
        "vehicle": f"{vehicle_year} {vehicle_make} {vehicle_model}",
        "coverage_level": coverage_level.title(),
        "monthly_premium": f"${round(final_price, 2)}",
        "annual_premium": f"${round(final_price * 12, 2)}",
        "valid_until": (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d"),
        "deductible": "$500" if coverage_level.lower() != "basic" else "$1000",
    }

    if customer_notes:
        quote["applied_discounts"] = customer_notes

    if not has_existing_policies and not has_past_interactions:
        quote["tip"] = (
            "Purchase a policy to become a returning customer and get loyalty discounts on future quotes!"
        )

    return json.dumps(quote, indent=2)


@app.tool()
def create_policy(
    holder_name: str,
    holder_email: str,
    policy_type: str,
    coverage: str,
    premium: str,
    car_model: str,
    car_year: int,
    car_edition: str,
    deductible: str = "$500",
) -> str:
    """Create a new insurance policy for a customer.

    The platform asks the customer to confirm before the policy is bound.

    Args:
        holder_name: Full name of the policy holder
        holder_email: Email address of the policy holder
        policy_type: Type of insurance (Auto, Home, Life)
        coverage: Coverage level (Basic, Standard, Comprehensive)
        premium: Monthly premium amount (e.g., "$150/month")
        car_model: Vehicle model (e.g., "Camry", "Model 3")
        car_year: Vehicle year (e.g., 2024)
        car_edition: Vehicle edition/trim (e.g., "SE", "Long Range")
        deductible: Deductible amount (default: "$500")
    """
    user_id = _current_user_id()
    policy_number = f"POL-{random.randint(10000, 99999)}"

    policy = Policy(
        policy_number=policy_number,
        user_id=user_id,
        holder_name=holder_name,
        holder_email=holder_email,
        type=f"{policy_type} Insurance",
        coverage=coverage,
        premium=premium,
        deductible=deductible,
        car_model=car_model,
        car_year=car_year,
        car_edition=car_edition,
    )

    try:
        created = policy_store.create_policy(policy)
        return json.dumps({"status": "created", "policy": created}, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@app.tool()
def list_customer_policies() -> str:
    """List all insurance policies for the current customer.

    Uses the current user_id to identify the customer.
    """
    user_id = _current_user_id()
    policies = policy_store.list_by_user_id(user_id)
    if policies:
        return json.dumps({"count": len(policies), "policies": policies}, indent=2)
    return json.dumps({"count": 0, "message": "No policies found for this customer"})


# =============================================================================
# Claims Tools - Filing, analysis, and resolution
# =============================================================================


@app.tool()
def file_claim(
    policy_number: str,
    claim_type: str,
    claim_amount: float,
    description: str,
) -> str:
    """File a new insurance claim for a policy.

    Use this when a customer wants to file a claim for an incident.

    Args:
        policy_number: The policy number to file the claim against (e.g., "POL-12345")
        claim_type: Type of claim (collision, theft, comprehensive, glass, vandalism)
        claim_amount: Estimated or actual claim amount in dollars
        description: Description of the incident
    """
    user_id = _current_user_id()

    # Verify the policy exists and belongs to this user; a policy belonging to
    # another customer is indistinguishable from a missing one.
    policy = policy_store.get_policy(policy_number, user_id)
    if not policy:
        return json.dumps({"error": f"Policy not found: {policy_number}"})

    if policy.get("status") != "Active":
        return json.dumps({"error": f"Policy is not active: {policy.get('status')}"})

    # Generate claim ID
    claim_id = f"CLM-{random.randint(10000, 99999)}"

    # Create the claim
    claim = Claim(
        claim_id=claim_id,
        policy_number=policy_number.upper(),
        user_id=user_id,
        claim_type=claim_type.lower(),
        claim_amount=claim_amount,
        description=description,
    )

    try:
        created = claim_store.create_claim(claim)
        return json.dumps(
            {
                "status": "filed",
                "claim": created,
                "message": f"Claim {claim_id} has been filed successfully. "
                "It will now be analyzed for risk assessment.",
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@app.tool()
def analyze_claim_risk(claim_id: str) -> str:
    """Analyze a claim for risk assessment.

    This tool analyzes the claim against its policy details and historical
    patterns to determine the risk level and recommendation. The policy is
    derived from the claim itself, so the analysis cannot combine a claim with
    an unrelated policy.

    Args:
        claim_id: The claim ID to analyze (e.g., "CLM-123456")

    Returns:
        Risk assessment with recommendation (auto_approve, review_recommended, manual_review_required)
    """
    user_id = _current_user_id()

    # Get claim details
    claim = claim_store.get_claim(claim_id, user_id)
    if not claim:
        return json.dumps({"error": f"Claim not found: {claim_id}"})

    # Get policy details for risk analysis from the claim's own policy
    policy_number = claim.get("policy_number", "")
    policy = policy_store.get_policy(policy_number, user_id)
    if not policy:
        return json.dumps({"error": f"Policy not found: {policy_number}"})

    # Build context for risk analysis
    claim_amount = claim.get("claim_amount", 0)
    claim_type = claim.get("claim_type", "")
    description = claim.get("description", "")
    policy_risk_score = policy.get("risk_score", 0.3)
    coverage_limit = policy.get("coverage_limit", 50000)

    # Check for previous claims on this policy
    previous_claims = claim_store.list_by_policy(policy_number, user_id)
    previous_claims = [c for c in previous_claims if c.get("claim_id") != claim_id.upper()]

    # Combined text for keyword analysis
    description_lower = f"{description} {claim_type}".lower()

    # Determine risk level based on numeric thresholds and keywords
    is_high_risk = (
        claim_amount >= HIGH_RISK_THRESHOLD
        or policy_risk_score >= HIGH_RISK_SCORE
        or claim_amount > coverage_limit
        or any(kw in description_lower for kw in HIGH_RISK_KEYWORDS)
    )

    is_medium_risk = (
        claim_amount >= MEDIUM_RISK_THRESHOLD
        or policy_risk_score >= MEDIUM_RISK_SCORE
        or len(previous_claims) >= 2  # Multiple previous claims
        or any(kw in description_lower for kw in MEDIUM_RISK_KEYWORDS)
    )

    # Build analysis result
    if is_high_risk:
        result: dict[str, Any] = {
            "risk_assessment": "high",
            "confidence": 0.65,
            "recommendation": "manual_review_required",
            "reasoning": "Multiple risk indicators detected. Human review strongly recommended.",
            "factors": [],
        }
        if claim_amount > coverage_limit:
            result["factors"].append(
                f"Claim amount (${claim_amount}) exceeds coverage limit (${coverage_limit})"
            )
        if policy_risk_score >= HIGH_RISK_SCORE:
            result["factors"].append(f"High policy risk score: {policy_risk_score}")
        if claim_amount >= HIGH_RISK_THRESHOLD:
            result["factors"].append(f"High claim amount: ${claim_amount}")
        if any(kw in description_lower for kw in HIGH_RISK_KEYWORDS):
            result["factors"].append("High-risk keywords detected in claim description")
    elif is_medium_risk:
        result = {
            "risk_assessment": "medium",
            "confidence": 0.72,
            "recommendation": "review_recommended",
            "reasoning": "Claim amount is elevated or patterns warrant review.",
            "factors": [],
        }
        if claim_amount >= MEDIUM_RISK_THRESHOLD:
            result["factors"].append(f"Elevated claim amount: ${claim_amount}")
        if len(previous_claims) >= 2:
            result["factors"].append(f"Multiple previous claims: {len(previous_claims)}")
        if policy_risk_score >= MEDIUM_RISK_SCORE:
            result["factors"].append(f"Elevated policy risk score: {policy_risk_score}")
    else:
        # Deterministic routing boundary: only low-risk claims under the
        # auto-approval threshold may resolve without human review.
        auto_approve = claim_amount < LOW_RISK_THRESHOLD
        result = {
            "risk_assessment": "low",
            "confidence": 0.85,
            "recommendation": "auto_approve" if auto_approve else "review_recommended",
            "reasoning": (
                "Claim amount is within normal range, no fraud indicators detected."
                if auto_approve
                else "Low-risk analysis, but the claim amount requires human review."
            ),
            "factors": [
                (
                    f"Claim amount (${claim_amount}) is reasonable"
                    if auto_approve
                    else f"Claim amount (${claim_amount}) requires human review"
                ),
                f"Policy risk score ({policy_risk_score}) is low",
                "No concerning patterns in claim description",
            ],
        }

    # Update claim with risk assessment
    claim_store.update_claim(
        claim_id,
        user_id,
        {
            "risk_assessment": result["risk_assessment"],
            "risk_confidence": result["confidence"],
            "recommendation": result["recommendation"],
            "status": "under_review",
        },
    )

    return json.dumps(result, indent=2)


@app.tool(redact_fields=["recipient_email"])
def send_notification(
    recipient_email: str,
    subject: str,
    body: str,
    notification_type: str = "email",
) -> str:
    """Send notification to customer about claim resolution or policy updates.

    Use this after a claim has been resolved to notify the customer of the outcome.

    Args:
        recipient_email: Customer's email address
        subject: Email subject line
        body: Email body content
        notification_type: Type of notification (email, sms). Default: email

    Note: The recipient_email is redacted in execution logs for privacy.
    """
    notification_id = f"NOTIF-{uuid.uuid4().hex[:8].upper()}"

    return json.dumps(
        {
            "notification_id": notification_id,
            "status": "sent",
            "recipient": recipient_email,
            "type": notification_type,
            "subject": subject,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "message": "Notification sent successfully.",
        },
        indent=2,
    )


@app.tool()
def check_claim_status(claim_id: str) -> str:
    """Check the current status of an insurance claim.

    Args:
        claim_id: The claim ID to check (e.g., "CLM-123456")

    Returns:
        Current claim status, risk assessment, and timeline information.
    """
    claim = claim_store.get_claim(claim_id, _current_user_id())
    if not claim:
        return json.dumps({"error": f"Claim not found: {claim_id}"})

    # Build response with relevant information
    response: dict[str, Any] = {
        "claim_id": claim.get("claim_id"),
        "status": claim.get("status"),
        "claim_type": claim.get("claim_type"),
        "claim_amount": claim.get("claim_amount"),
        "description": claim.get("description"),
        "created_at": claim.get("created_at"),
        "updated_at": claim.get("updated_at"),
    }

    # Include risk assessment if available
    if claim.get("risk_assessment"):
        response["risk_assessment"] = {
            "level": claim.get("risk_assessment"),
            "confidence": claim.get("risk_confidence"),
            "recommendation": claim.get("recommendation"),
        }

    # Include resolution if available
    if claim.get("resolution"):
        response["resolution"] = {
            "decision": claim.get("resolution"),
            "reviewer_notes": claim.get("reviewer_notes"),
            "resolved_at": claim.get("resolved_at"),
        }

    return json.dumps(response, indent=2)


@app.tool()
def list_customer_claims() -> str:
    """List all insurance claims for the current customer.

    Returns all claims filed by the current user across all their policies.
    """
    user_id = _current_user_id()
    claims = claim_store.list_by_user_id(user_id)

    if claims:
        return json.dumps(
            {
                "count": len(claims),
                "claims": claims,
            },
            indent=2,
        )
    return json.dumps({"count": 0, "message": "No claims found for this customer"})


@app.tool()
def resolve_claim(
    claim_id: str,
    resolution: str,
    reviewer_notes: str = "",
) -> str:
    """Resolve a claim after auto-approval or human review.

    The claim store enforces the authorized path: a claim recommended for
    auto-approval can only be resolved as "approved", and any other claim
    requires the human reviewer's "approved" or "denied" decision.

    Use this after receiving human review decision or when auto-approving a
    low-risk, low-value claim.

    Args:
        claim_id: The claim ID to resolve
        resolution: The resolution decision (approved, denied)
        reviewer_notes: Notes from the reviewer (optional)
    """
    try:
        resolved = claim_store.resolve_claim(
            claim_id, _current_user_id(), resolution, reviewer_notes
        )
    except ValueError as e:
        return json.dumps({"error": str(e)})

    return json.dumps(
        {
            "status": "resolved",
            "claim_id": claim_id,
            "resolution": resolution,
            "resolved_at": resolved.get("resolved_at"),
            "message": f"Claim {claim_id} has been {resolution}.",
        },
        indent=2,
    )


# =============================================================================
# Memory Tools - Using semantic memory for customer profiles
# =============================================================================


@app.tool()
def save_customer_info(info_key: str, info_value: str) -> str:
    """Save customer information to memory for future conversations.

    Use when customer provides personal details, preferences, or any important information.
    Each piece of information is saved with a unique key, allowing multiple facts per customer.

    Args:
        info_key: Key identifying what information this is (e.g., "name", "email", "phone",
                  "vehicle_preference", "coverage_preference", "budget", "driving_history")
        info_value: The actual information to save

    Examples:
        - save_customer_info("name", "John Smith")
        - save_customer_info("email", "john@example.com")
        - save_customer_info("vehicle_preference", "Prefers SUVs, interested in Toyota RAV4")
        - save_customer_info("coverage_preference", "Wants comprehensive with low deductible")
        - save_customer_info("budget", "Monthly budget around $150-200")
    """
    user_id = _current_user_id()

    # Build descriptive text for semantic search
    profile_text = f"Customer {info_key}: {info_value}"

    result = app.memory.save_semantic(
        text=profile_text,
        label=f"{user_id}_{info_key}",
        source="insurance_agent",
        metadata={
            "type": "customer_info",
            "info_key": info_key,
        },
        user_id=user_id,
    )

    if getattr(result, "acknowledged", result):
        return json.dumps({"status": "saved", "info_key": info_key, "user_id": user_id})
    return json.dumps({"status": "error", "error": "Memory not enabled"})


@app.tool()
def recall_customer_info() -> str:
    """Recall stored information about the current customer.

    Returns all known information from the customer's profile and past interactions.
    """
    user_id = _current_user_id()

    context: Any = app.memory.build_context(
        query=f"customer profile and information for user {user_id}",
        user_id=user_id,
    )
    formatted = getattr(context, "formatted_context", context)
    memory_context = formatted if isinstance(formatted, str) else ""

    if not memory_context:
        return json.dumps(
            {
                "user_id": user_id,
                "found": False,
                "message": "No stored information found for this customer.",
            }
        )

    return json.dumps(
        {
            "user_id": user_id,
            "found": True,
            "profile": memory_context,
        }
    )


# =============================================================================
# Episodic Memory Tools - Conversation summaries and events
# =============================================================================


@app.tool()
def save_conversation_summary(
    title: str,
    summary: str,
    tags: str = "",
) -> str:
    """Save a summary of the current conversation for future reference.

    Use this at the end of significant interactions to help remember:
    - What was discussed (quotes, policy purchases, questions)
    - Key decisions made
    - Follow-up items

    This helps provide personalized service in future conversations.

    Args:
        title: Brief title for this conversation (e.g., "John Smith purchased auto policy")
        summary: Summary of what was discussed and any outcomes
        tags: Comma-separated tags for categorization (e.g., "quote,auto_insurance,new_customer")
    """
    user_id = _current_user_id()

    # Parse tags
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    # Use episodic memory for conversation summaries
    episode = app.memory.save_episode(
        title=title,
        content=summary,
        summary=summary,
        participants=["Customer", "Alex (AI Assistant)"],
        tags=tag_list,
        user_id=user_id,
        visibility="private",
    )

    if getattr(episode, "acknowledged", bool(episode)):
        return json.dumps(
            {
                "status": "saved",
                "episode_id": getattr(episode, "id", episode),
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
        }
    )


@app.tool()
def recall_past_conversations(query: str = "") -> str:
    """Recall past conversations and interactions with the customer.

    Use this to find:
    - Previous discussions about quotes or policies
    - Past questions they've asked
    - Any follow-up items from earlier conversations

    Args:
        query: Optional search query to find specific conversations
               (e.g., "policy purchase" or "teenage driver")
    """
    user_id = _current_user_id()

    search_query = query if query else f"customer interactions for {user_id}"

    episodes = app.memory.search_episodes(
        query=search_query,
        user_id=user_id,
        top_k=5,
    )

    if not episodes:
        return json.dumps(
            {
                "found": False,
                "user_id": user_id,
                "message": "No past conversations found for this customer.",
            }
        )

    # Format episodes for display
    formatted = []
    for ep in episodes:
        ep_meta = ep.metadata or {}
        formatted.append(
            {
                "title": ep_meta.get("title", ""),
                "summary": ep_meta.get("summary", ep.content[:200]),
                "tags": ep_meta.get("tags", []),
            }
        )

    return json.dumps(
        {
            "found": True,
            "user_id": user_id,
            "count": len(formatted),
            "conversations": formatted,
        },
        indent=2,
    )


# =============================================================================
# Coverage Knowledge Tools - Static glossary
# =============================================================================


@app.tool()
def explain_insurance_term(term: str) -> str:
    """Look up and explain an insurance term using the built-in glossary.

    Use this when a customer asks about insurance terminology like:
    - "What is a deductible?"
    - "What does comprehensive coverage include?"
    - "What's the difference between liability and collision?"

    Args:
        term: The insurance term to explain (e.g., "deductible", "comprehensive", "premium")
    """
    key = term.strip().lower()
    match = INSURANCE_TERMS.get(key)
    if match is None:
        for candidate in INSURANCE_TERMS.values():
            if key in candidate["term"] or key in candidate["definition"].lower():
                match = candidate
                break
    if match is None:
        return json.dumps(
            {"found": False, "term": term, "message": f"No definition found for '{term}'."}
        )
    return json.dumps({"found": True, "domain": "insurance", **match}, indent=2)


@app.tool()
def get_coverage_options() -> str:
    """Get information about available coverage levels and their differences.

    Use this to explain coverage options to customers when they're choosing
    between Basic, Standard, and Comprehensive coverage.
    """
    return json.dumps(
        {
            "source": "default",
            "coverages": [
                {"level": "Basic", "description": "Liability only - covers damages to others"},
                {
                    "level": "Standard",
                    "description": "Collision + Liability - covers your car in accidents",
                },
                {
                    "level": "Comprehensive",
                    "description": "Full coverage including theft, weather, vandalism",
                },
            ],
        },
        indent=2,
    )


# =============================================================================
# Human Review - Native ADK long-running wait
# =============================================================================


def human_review(
    claim_id: str,
    decision: str,
    reason: str,
    claim_amount: float,
    risk_level: str,
    conversation_summary: str,
    response_schema: dict[str, Any],
    policy_number: str = "",
    customer_name: str = "",
) -> None:
    """Request human review for high-risk or high-value claim decisions.

    This tool SUSPENDS execution until a human provides input.
    Use this when the claim requires manual approval due to:
    - Any claim of $1,000 or more, even with low risk assessment
    - High or medium risk assessment
    - Unusual claim patterns

    Args:
        claim_id: The claim ID requiring review
        decision: The preliminary decision (approve, deny, adjust)
        reason: Reason for requesting human review
        claim_amount: The claim amount in dollars
        risk_level: Risk level from analysis (low, medium, high)
        conversation_summary: Summary of the conversation leading to this review request.
            Include: what the customer reported, relevant policy details, and why review is needed.
        response_schema: Pass HUMAN_REVIEW_RESPONSE_SCHEMA from the instructions verbatim.
            The platform renders the reviewer's form and validates the answer against it.
        policy_number: The policy number associated with the claim (if known)
        customer_name: The customer's name (if known)
    """
    # ADK suspends on long-running function calls; the platform waits for a
    # human answer and resumes this session with the reviewer's response. The
    # function body is intentionally a no-op.
    del (
        claim_id,
        decision,
        reason,
        claim_amount,
        risk_level,
        conversation_summary,
        policy_number,
        customer_name,
    )
    return None


# =============================================================================
# Agent Definition
# =============================================================================

SYSTEM_PROMPT = f"""You are Alex, an AI insurance assistant for SecureLife Insurance, specializing in auto insurance.

## Your Role
Help customers with their complete insurance journey:
- Get personalized auto insurance quotes and purchase policies
- File and track insurance claims
- Resolve disputes with human-in-the-loop review when a claim is medium or high risk

## Response Rules
- You MUST always end every conversational turn with a natural-language response to the user.
- Tool call results are NOT visible to the user — the user cannot see tool inputs or outputs. After any tool call completes, you MUST summarize or present the results in your own words.
- Never end a turn with only a tool call and no follow-up text. If you do, the user will see a blank message.

## Capabilities

### Policy Management
- Generate personalized auto insurance quotes based on vehicle and driver information
- Create new insurance policies for customers
- Look up existing policies by policy number
- List all policies for the current customer

### Claims Processing
- File insurance claims for incidents (collision, theft, comprehensive, glass, vandalism)
- Analyze claim risk to determine approval path
- Request human review for high-risk or high-value claims
- Send notifications about claim resolutions
- Track claim status throughout the process

### Customer Service
- Remember customer details and conversation history for personalized service
- Explain insurance terminology and coverage options

## Workflow

### CRITICAL: First Contact Protocol
When a customer first says they want to buy insurance:
1. **ALWAYS ask for their personal information FIRST**: "To get started, could you please tell me your name, email, and phone number?"
2. **NEVER ask about their vehicle until you have their personal info**
3. After they provide their info, THEN ask about vehicle and coverage

### For New Customers
1. **FIRST STEP**: Greet them and ask for name, email, and phone number
2. Save each piece of information with `save_customer_info`
3. Ask about their vehicle (year, make, model) and coverage preference
4. Generate a quote with `get_quote` and explain the coverage
5. If they like the quote, call `create_policy`. The platform asks the customer to
   confirm before the policy is bound. After confirmation returns, tell them the policy number.

### For Returning Customers (when memory/policies found)
1. Recall their info with `recall_customer_info` and check `list_customer_policies`
2. Acknowledge their returning status by name if known
3. Offer loyalty discounts on new quotes
4. Help with any claims or policy questions

### For Filing Claims (follow this order exactly)
1. **CRITICAL FIRST STEP - Find Customer's Policies**: When a customer mentions ANY accident, damage, claim, or incident, your FIRST action MUST be to call `list_customer_policies`
   - This tool uses the user_id (which persists across sessions) to retrieve ALL policies for this customer
   - NEVER rely solely on memory context for policy information - ALWAYS verify with the policy store
   - If policies are found, you'll have the policy number(s), coverage type, vehicle details, and status
   - If no policies are found, inform the customer they need to purchase a policy first
   - Example: Customer says "I was in an accident" → IMMEDIATELY call `list_customer_policies` before doing anything else
2. **Verify Policy Details**: Use `lookup_policy` to get full details of the specific policy if needed
3. **Gather ALL Claim Details BEFORE Filing**: You MUST have all of these before calling `file_claim`:
   - Claim type (collision, theft, comprehensive, glass, vandalism)
   - Estimated claim amount in dollars (this is CRITICAL - never file with $0 or without knowing the amount)
   - Description of what happened (when, where, what damage occurred)
   - If the customer hasn't provided any of these, ASK for them first before filing
4. **File Claim**: Use `file_claim` with the policy number from step 1, claim type, amount, and description
5. **Analyze Risk**: Use `analyze_claim_risk` to assess the claim. This returns a `risk_assessment` field (low/medium/high).
6. **Decision Based on the Stored Recommendation (every analyzed claim takes exactly ONE of these two paths)**:
   - If `analyze_claim_risk` returned `recommendation` = "auto_approve": Auto-approve with `resolve_claim`
   - If `recommendation` is "review_recommended" or "manual_review_required": Call `human_review` exactly once
7. **After Human Review Returns**: When `human_review` suspends and then resumes, you MUST call `resolve_claim` with the claim_id, the reviewer's decision, and reviewer_notes before doing anything else, whether the decision is approved or denied. Do not invent the decision. The claim store rejects resolutions that do not match the authorized path: an auto-approved claim can only resolve as "approved", and a reviewed claim only accepts the reviewer's "approved" or "denied" decision.
8. **After Resolution**: Use `send_notification` to inform the customer of the outcome

## Risk Decision Guidelines

### Auto-Approve (No Human Review Needed)
- ONLY when `analyze_claim_risk` returns `recommendation` = "auto_approve"
  (low risk assessment AND amount under $1,000)

### Request Human Review (every other claim)
- `recommendation` = "review_recommended" or "manual_review_required"
  (any claim of $1,000 or more, any MEDIUM or HIGH risk assessment)

**CRITICAL: When calling `human_review`, you MUST provide ALL required arguments**:
1. **claim_id**: The claim ID from the file_claim result
2. **decision**: Your preliminary decision (approve, deny, adjust)
3. **reason**: Reason for requesting human review
4. **claim_amount**: The claim amount in dollars
5. **risk_level**: REQUIRED - Use the `risk_assessment` value from analyze_claim_risk (low/medium/high)
6. **conversation_summary**: Comprehensive summary including:
   - Customer identification (name, policy number if known)
   - What the customer reported (incident details, when it happened)
   - Relevant policy details (coverage type, coverage limit, risk score)
   - Why the claim requires human review (specific risk factors from the risk analysis)
   - Any concerning patterns or discrepancies noted
7. **response_schema**: REQUIRED - Pass HUMAN_REVIEW_RESPONSE_SCHEMA exactly as written here,
   verbatim with no changes: {json.dumps(HUMAN_REVIEW_RESPONSE_SCHEMA, sort_keys=True)}.
   The platform renders the reviewer's form from it.
8. **policy_number**: Optional but recommended
9. **customer_name**: Optional but recommended

The reviewer answers with {json.dumps(HUMAN_REVIEW_RESPONSE_SCHEMA, sort_keys=True)}.

## Important Behaviors
- Always `recall_customer_info` at the start to check if they're a returning customer
- Use `recall_past_conversations` to find previous discussions
- **CRITICAL - Policy Lookup Protocol**: The user_id persists across ALL sessions and conversations. This means:
  - Policies created in previous sessions are ALWAYS accessible via `list_customer_policies`
  - When a customer mentions accidents, damage, claims, incidents, or "my car", IMMEDIATELY call `list_customer_policies` as your FIRST action
  - NEVER rely only on memory context for policy information - memory is supplementary, the policy store is the source of truth
  - NEVER ask "What's your policy number?" without first calling `list_customer_policies` to check if they have policies
  - If `list_customer_policies` returns policies, use those policy numbers and details directly
  - Even in a brand new session/conversation, if the user_id is the same, their policies from previous sessions will be retrieved
- Returning customers get automatic loyalty discounts on quotes
- When customers ask about insurance terms, use `explain_insurance_term`
- Use `get_coverage_options` to explain Basic, Standard, and Comprehensive coverage
- After creating a policy, provide them with their policy number
- After filing a claim, provide them with their claim ID and next steps

## CRITICAL: Claims Processing Rules
1. NEVER file a claim until you have ALL required information: claim type, claim amount (in dollars), and description
   - If a customer reports an accident but doesn't provide an amount, ASK for it first
   - NEVER file a claim with $0 or a placeholder amount
2. Call tools ONE AT A TIME in sequence for claims
3. During a multi-step claims sequence (file → analyze → review/resolve → notify), you may call tools back-to-back without text between intermediate steps
4. After the FINAL tool in any sequence completes, you MUST provide a natural-language text response summarizing the outcome for the user
5. Always notify the customer after claim resolution using `send_notification`

## CRITICAL: Save Customer Information
When a customer provides ANY personal details or preferences, save each piece separately using `save_customer_info`:
- `save_customer_info("name", "John Smith")`
- `save_customer_info("email", "john@example.com")`
- `save_customer_info("phone", "555-123-4567")`
- `save_customer_info("vehicle_preference", "Interested in insuring a 2022 Toyota Camry SE")`
- `save_customer_info("coverage_preference", "Wants comprehensive coverage with low deductible")`
- `save_customer_info("budget", "Looking for coverage around $150/month")`
- `save_customer_info("driving_history", "Clean record, no accidents in 5 years")`

## CRITICAL: Save Conversation Summaries
You MUST call `save_conversation_summary` immediately after ANY of these events:
1. **Policy created** → title="Policy created for [name]", summary="Created [policy_type] policy [policy_number] with [coverage] coverage at [premium]"
2. **Quote provided** → title="Quote provided to [name]", summary="Provided [coverage] quote for [vehicle] at [premium]/month"
3. **Claim filed** → title="Claim filed for [name]", summary="Filed [claim_type] claim [claim_id] for $[amount] on policy [policy_number]"
4. **Claim resolved** → title="Claim resolved for [name]", summary="Claim [claim_id] was [approved/denied]. [brief reason]"
5. **Customer info saved** → title="New customer [name] registered", summary="Saved profile for [name] ([email])"
6. **Question answered** → title="Explained [topic] to customer", summary="Customer asked about [topic], explained [key points]"

Be friendly, professional, and guide customers toward the right coverage and quick claim resolutions.
"""


def _agent_tools() -> list[Any]:
    """SDK-registered tools plus native ADK HITL constructors."""
    by_name = {fn.__name__: fn for fn in app.tools()}
    create = by_name.pop(create_policy.__name__)
    return [
        # Binding a policy is irreversible, so confirm with the customer first.
        # ADK suspends for a native confirmation and the platform resumes it.
        FunctionTool(create, require_confirmation=True),
        *by_name.values(),
        # The review wait must be constructed inside the agent process so ADK
        # can suspend and resume the workflow correctly.
        _SchemaLongRunningFunctionTool(human_review, HUMAN_REVIEW_RESPONSE_SCHEMA),
    ]


@app.entrypoint
def build_agent() -> LlmAgent:
    """Build the ADK insurance agent."""
    logger.info("Building ADK agent...")
    return LlmAgent(
        name="insurance_agent",
        description="Auto-insurance assistant with native ADK human review waits.",
        instruction=SYSTEM_PROMPT.strip(),
        model=app.llm(build_llm()),
        tools=_agent_tools(),
    )


# =============================================================================
# Run
# =============================================================================


def main() -> None:
    """Main entry point."""
    logger.info("=" * 60)
    logger.info(f"🚀 Starting {APP_NAME} (Runner SDK)")
    logger.info("=" * 60)

    app.run()


if __name__ == "__main__":
    main()
