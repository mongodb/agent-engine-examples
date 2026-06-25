"""
Insurance Agent - Example application demonstrating the Runner SDK.

This is a port of the agent-runtime insurance-agent example to the Runner SDK.
It demonstrates:
- @app.tool decorator for secure, logged tools
- Long-term memory for customer information (optional)
- MongoDB checkpointing for conversation state
- LangGraph for agent orchestration
- Policy catalogs for tool configuration
- OpenTelemetry tracing (optional)

The same code runs in all three modes:
    RUNNER_MODE=aer            → Full LangGraph execution
    RUNNER_MODE=tool           → Tool function execution
    RUNNER_MODE=memory-server  → Long-term memory service

See env.example for configuration options.
"""

import json
import logging
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Annotated, Literal, Optional, TypedDict, cast

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from magenta_sdklanggraph import App
from runner_shared.models import SuspendPayload

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


def _build_runtime_llm() -> BaseChatModel:
    """Create the default chat model from agent.yaml hints + environment secrets."""
    llm_config = getattr(app, "llm_config", None)
    configured_provider = (getattr(llm_config, "provider", None) or "").strip().lower()
    configured_model = getattr(llm_config, "model", None)
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    anthropic_base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    openai_base_url = os.environ.get("OPENAI_BASE_URL", "")

    def _build_gemini() -> BaseChatModel:
        from langchain_google_genai import ChatGoogleGenerativeAI

        model_name = configured_model or "gemini-2.5-flash"
        return cast(Any, ChatGoogleGenerativeAI)(
            api_key=gemini_key,
            model=model_name,
            temperature=0,
        )

    def _build_openai() -> BaseChatModel:
        from langchain_openai import ChatOpenAI

        model_name = configured_model or "gpt-5.4-mini"
        kwargs: dict[str, Any] = {
            "api_key": openai_key,
            "model": model_name,
            "temperature": 0,
        }
        if openai_base_url:
            if "grove-foundry" in openai_base_url:
                kwargs["base_url"] = openai_base_url.split("/v1")[0] + "/v1"
                kwargs["default_headers"] = {"api-key": openai_key}
            else:
                kwargs["base_url"] = openai_base_url.rstrip("/")
        return cast(Any, ChatOpenAI)(**kwargs)

    def _build_anthropic() -> BaseChatModel:
        from langchain_anthropic import ChatAnthropic

        model_name = configured_model or "claude-sonnet-4-6"
        kwargs = {
            "api_key": anthropic_key,
            "model_name": model_name,
            "temperature": 0,
        }
        if anthropic_base_url:
            if "grove-foundry" in anthropic_base_url:
                kwargs["base_url"] = anthropic_base_url.split("/v1")[0].rstrip("/")
                kwargs["default_headers"] = {"api-key": anthropic_key}
            else:
                kwargs["base_url"] = anthropic_base_url.rstrip("/")
        return cast(Any, ChatAnthropic)(**kwargs)

    builders = {
        "gemini": (gemini_key, _build_gemini),
        "openai": (openai_key, _build_openai),
        "anthropic": (anthropic_key, _build_anthropic),
    }

    if configured_provider:
        if configured_provider not in builders:
            raise RuntimeError(
                "Unsupported config.provider in agent.yaml. Use one of: anthropic, gemini, openai."
            )
        provider_key, provider_builder = builders[configured_provider]
        if provider_key:
            return provider_builder()
        raise RuntimeError(
            f"agent.yaml config.provider is set to {configured_provider!r}, "
            "but the matching API key is missing from .env."
        )

    for provider_key, provider_builder in builders.values():
        if provider_key:
            return provider_builder()

    raise RuntimeError(
        "No LLM API key found. Set GEMINI_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY."
    )


# =============================================================================
# Runtime Setup
# =============================================================================
APP_NAME = "insurance-agent"
ORG_ID = os.environ.get("ORG_ID", "123456789012345678901234")

app = App(
    app_name=APP_NAME,
    org_id=ORG_ID,
)
logger.info("✅ App created")

policy_store = create_policy_store(MONGODB_URI, MONGODB_DATABASE)
claim_store = create_claim_store(MONGODB_URI, MONGODB_DATABASE)

# =============================================================================
# Risk Assessment Constants (for claims analysis)
# =============================================================================

# Keywords in claim description that indicate high risk
HIGH_RISK_KEYWORDS = ["total loss", "totaled", "stolen", "theft", "fire", "flood"]

# Keywords in claim description that indicate medium risk
MEDIUM_RISK_KEYWORDS = ["significant damage", "major repair", "structural"]

# Numeric thresholds for risk assessment
LOW_RISK_THRESHOLD = 1000  # Claims under $1,000 can be auto-approved
MEDIUM_RISK_THRESHOLD = 5000  # Claims $5,000+ need review
HIGH_RISK_THRESHOLD = 15000  # Claims $15,000+ require human review

# Risk score thresholds (0.0 - 1.0)
MEDIUM_RISK_SCORE = 0.4
HIGH_RISK_SCORE = 0.6


# =============================================================================
# Tools - Using @app.tool for secure, logged execution
# =============================================================================


@app.tool(is_local=False)
def lookup_policy(policy_number: str) -> str:
    """Look up an insurance policy by policy number."""
    policy = policy_store.get_policy(policy_number)
    if policy:
        return json.dumps(policy, indent=2)
    return json.dumps({"error": f"No policy found: {policy_number}"})


@app.tool(is_local=False)
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
    user_id = app.get_current_user_id()

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
    customer_notes = []

    # Check if customer has existing policies (true returning customer indicator)
    existing_policies = policy_store.list_by_user_id(user_id)  # type: ignore[arg-type]  # user_id is str at runtime
    has_existing_policies = len(existing_policies) > 0

    # Check episodic memory for past conversations
    past_episodes = app.memory.search_episodes(query="policy quote conversation", top_k=3)
    has_past_interactions = len(past_episodes) > 0 if past_episodes else False

    # Only apply loyalty discount if customer has existing policies OR past interactions
    if has_existing_policies or has_past_interactions:
        customer_notes.append("Returning customer - 10% loyalty discount applied")
        loyalty_discount = 0.10

        # Check semantic memory for good driving record
        memory_context = app.memory.build_context(
            query="driving history record accidents",
            user_id=user_id,  # type: ignore[arg-type]  # user_id is str at runtime
        )
        if memory_context:
            if (
                "clean driving" in memory_context.lower()
                or "no accidents" in memory_context.lower()
            ):
                customer_notes.append("Good driver discount - additional 5% off")
                loyalty_discount += 0.05

    # Apply loyalty discount
    final_price = base * (1 - loyalty_discount)

    quote = {
        "quote_id": f"QT-{random.randint(100000, 999999)}",
        "vehicle": f"{vehicle_year} {vehicle_make} {vehicle_model}",
        "coverage_level": coverage_level.title(),
        "monthly_premium": f"${round(final_price, 2)}",
        "annual_premium": f"${round(final_price * 12, 2)}",
        "valid_until": (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d"),
        "deductible": "$500" if coverage_level.lower() != "basic" else "$1000",
    }

    if customer_notes:
        quote["applied_discounts"] = customer_notes  # type: ignore[arg-type]  # list assigned to TypedDict str field

    if not has_existing_policies and not has_past_interactions:
        quote["tip"] = (
            "Purchase a policy to become a returning customer and get loyalty discounts on future quotes!"
        )

    return json.dumps(quote, indent=2)


@app.tool(is_local=False)
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
    user_id = app.get_current_user_id()
    policy_number = f"POL-{random.randint(10000, 99999)}"

    policy = Policy(
        policy_number=policy_number,
        user_id=user_id,  # type: ignore[arg-type]  # user_id is str at runtime
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


@app.tool(is_local=False)
def list_customer_policies() -> str:
    """List all insurance policies for the current customer.

    Uses the current user_id to identify the customer.
    """
    user_id = app.get_current_user_id()
    policies = policy_store.list_by_user_id(user_id)  # type: ignore[arg-type]  # user_id is str at runtime
    if policies:
        return json.dumps({"count": len(policies), "policies": policies}, indent=2)
    return json.dumps({"count": 0, "message": "No policies found for this customer"})


# =============================================================================
# Claims Tools - Filing, analysis, and resolution
# =============================================================================


@app.tool(is_local=False)
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
    user_id = app.get_current_user_id()

    # Verify the policy exists and belongs to this user
    policy = policy_store.get_policy(policy_number)
    if not policy:
        return json.dumps({"error": f"Policy not found: {policy_number}"})

    if policy.get("user_id") != user_id:
        return json.dumps({"error": "Policy does not belong to current user"})

    if policy.get("status") != "Active":
        return json.dumps({"error": f"Policy is not active: {policy.get('status')}"})

    # Generate claim ID
    claim_id = f"CLM-{random.randint(10000, 99999)}"

    # Create the claim
    claim = Claim(
        claim_id=claim_id,
        policy_number=policy_number.upper(),
        user_id=user_id,  # type: ignore[arg-type]  # user_id is str at runtime
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


@app.tool(is_local=False, catalog="openai/chat")
def analyze_claim_risk(claim_id: str, policy_number: str) -> str:
    """Analyze a claim for risk assessment.

    This tool analyzes the claim against policy details and historical patterns
    to determine the risk level and recommendation.

    Args:
        claim_id: The claim ID to analyze (e.g., "CLM-123456")
        policy_number: The associated policy number (e.g., "POL-123")

    Returns:
        Risk assessment with recommendation (auto_approve, review_recommended, manual_review_required)
    """
    # Get claim details
    claim = claim_store.get_claim(claim_id)
    if not claim:
        return json.dumps({"error": f"Claim not found: {claim_id}"})

    # Get policy details for risk analysis
    policy = policy_store.get_policy(policy_number)
    if not policy:
        return json.dumps({"error": f"Policy not found: {policy_number}"})

    # Build context for risk analysis
    claim_amount = claim.get("claim_amount", 0)
    claim_type = claim.get("claim_type", "")
    description = claim.get("description", "")
    policy_risk_score = policy.get("risk_score", 0.3)
    coverage_limit = policy.get("coverage_limit", 50000)

    # Check for previous claims on this policy
    previous_claims = claim_store.list_by_policy(policy_number)
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
        result = {
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
        result = {
            "risk_assessment": "low",
            "confidence": 0.85,
            "recommendation": "auto_approve",
            "reasoning": "Claim amount is within normal range, no fraud indicators detected.",
            "factors": [
                f"Claim amount (${claim_amount}) is reasonable",
                f"Policy risk score ({policy_risk_score}) is low",
                "No concerning patterns in claim description",
            ],
        }

    # Update claim with risk assessment
    claim_store.update_claim(
        claim_id,
        {
            "risk_assessment": result["risk_assessment"],
            "risk_confidence": result["confidence"],
            "recommendation": result["recommendation"],
            "status": "under_review",
        },
    )

    return json.dumps(result, indent=2)


@app.tool(is_local=True)
def human_review(
    claim_id: str,
    decision: str,
    reason: str,
    claim_amount: float,
    risk_level: str,
    conversation_summary: str,
    policy_number: str = "",
    customer_name: str = "",
) -> str:
    """Request human review for high-risk or high-value claim decisions.

    This tool SUSPENDS execution until a human provides input.
    Use this when the claim requires manual approval due to:
    - High claim amount ($5,000+)
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
        policy_number: The policy number associated with the claim (if known)
        customer_name: The customer's name (if known)
    """
    # Create a review task
    task_id = f"REVIEW-{uuid.uuid4().hex[:8].upper()}"

    # Update claim status to suspended
    claim_store.update_claim(claim_id, {"status": "suspended_for_review"})

    return SuspendPayload(
        suspend_reason="awaiting_human_review",
        suspend_context={
            "task_id": task_id,
            "claim_id": claim_id,
            "policy_number": policy_number,
            "customer_name": customer_name,
            "decision": decision,
            "reason": reason,
            "claim_amount": claim_amount,
            "risk_level": risk_level,
            "conversation_summary": conversation_summary,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "instructions": "Please review the claim and provide approval or denial decision.",
        },
    ).to_json()


@app.tool(is_local=True, redact_fields=["recipient_email"])
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


@app.tool(is_local=False)
def check_claim_status(claim_id: str) -> str:
    """Check the current status of an insurance claim.

    Args:
        claim_id: The claim ID to check (e.g., "CLM-123456")

    Returns:
        Current claim status, risk assessment, and timeline information.
    """
    claim = claim_store.get_claim(claim_id)
    if not claim:
        return json.dumps({"error": f"Claim not found: {claim_id}"})

    # Build response with relevant information
    response = {
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


@app.tool(is_local=False)
def list_customer_claims() -> str:
    """List all insurance claims for the current customer.

    Returns all claims filed by the current user across all their policies.
    """
    user_id = app.get_current_user_id()
    claims = claim_store.list_by_user_id(user_id)  # type: ignore[arg-type]  # user_id is str at runtime

    if claims:
        return json.dumps(
            {
                "count": len(claims),
                "claims": claims,
            },
            indent=2,
        )
    return json.dumps({"count": 0, "message": "No claims found for this customer"})


@app.tool(is_local=False)
def resolve_claim(
    claim_id: str,
    resolution: str,
    reviewer_notes: str = "",
) -> str:
    """Resolve a claim after human review or auto-approval.

    Use this after receiving human review decision or when auto-approving a low-risk claim.

    Args:
        claim_id: The claim ID to resolve
        resolution: The resolution decision (approved, denied, approved_with_adjustment)
        reviewer_notes: Notes from the reviewer (optional)
    """
    claim = claim_store.get_claim(claim_id)
    if not claim:
        return json.dumps({"error": f"Claim not found: {claim_id}"})

    # Resolve the claim
    resolved = claim_store.resolve_claim(claim_id, resolution, reviewer_notes)

    if resolved:
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

    return json.dumps({"error": "Failed to resolve claim"})


# =============================================================================
# Memory Tools - Using semantic memory for customer profiles
# =============================================================================


@app.tool(is_local=False)
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
    user_id = app.get_current_user_id()

    # Build descriptive text for semantic search
    profile_text = f"Customer {info_key}: {info_value}"

    success = app.memory.save_semantic(
        text=profile_text,
        label=f"{user_id}_{info_key}",
        source="insurance_agent",
        metadata={
            "type": "customer_info",
            "info_key": info_key,
        },
        user_id=user_id,  # type: ignore[arg-type]  # user_id is str at runtime
    )

    if success:
        return json.dumps({"status": "saved", "info_key": info_key, "user_id": user_id})
    return json.dumps({"status": "error", "error": "Memory not enabled"})


@app.tool(is_local=False)
def recall_customer_info() -> str:
    """Recall stored information about the current customer.

    Returns all known information from the customer's profile and past interactions.
    """
    user_id = app.get_current_user_id()

    memory_context = app.memory.build_context(
        query=f"customer profile and information for user {user_id}",
        user_id=user_id,  # type: ignore[arg-type]  # user_id is str at runtime
    )

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


@app.tool(is_local=False)
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
    user_id = app.get_current_user_id()

    # Parse tags
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    # Use episodic memory for conversation summaries
    episode_id = app.memory.save_episode(
        title=title,
        content=summary,
        summary=summary,
        participants=["Customer", "Alex (AI Assistant)"],
        tags=tag_list,
        user_id=user_id,  # type: ignore[arg-type]  # user_id is str at runtime
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
        }
    )


@app.tool(is_local=False)
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
    user_id = app.get_current_user_id()

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
        formatted.append(
            {
                "title": ep.get("title", ""),
                "summary": ep.get("summary", ep.get("content", "")[:200]),
                "tags": ep.get("tags", []),
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
# Taxonomic Memory Tools - Domain terminology lookup
# =============================================================================


@app.tool(is_local=False)
def explain_insurance_term(term: str) -> str:
    """Look up and explain an insurance term using the knowledge base.

    Use this when a customer asks about insurance terminology like:
    - "What is a deductible?"
    - "What does comprehensive coverage include?"
    - "What's the difference between liability and collision?"

    Args:
        term: The insurance term to explain (e.g., "deductible", "comprehensive", "premium")
    """
    results = app.memory.search_taxonomic(
        query=term,
        top_k=3,
    )

    if not results:
        return json.dumps(
            {
                "found": False,
                "term": term,
                "message": f"No definition found for '{term}'. Please ask the customer to clarify.",
            }
        )

    # Find the best match
    best_match = results[0]

    response = {
        "found": True,
        "term": best_match.get("term", term),
        "domain": best_match.get("domain", "insurance"),
        "definition": best_match.get("definition", ""),
        "related_terms": best_match.get("related_terms", []),
    }

    # Include other matches if they're relevant
    if len(results) > 1:
        response["related_concepts"] = [
            {
                "term": r.get("term", ""),
                "domain": r.get("domain", ""),
                "definition": r.get("definition", "")[:100] + "...",
            }
            for r in results[1:3]
        ]

    return json.dumps(response, indent=2)


@app.tool(is_local=False)
def get_coverage_options() -> str:
    """Get information about available coverage levels and their differences.

    Use this to explain coverage options to customers when they're choosing
    between Basic, Standard, and Comprehensive coverage.
    """
    coverage_terms = ["basic coverage", "standard coverage", "comprehensive coverage"]
    coverages = []

    for term in coverage_terms:
        results = app.memory.search_taxonomic(query=term, domain="coverage_types", top_k=1)
        if results:
            coverages.append(
                {
                    "level": results[0].get("term", term),
                    "description": results[0].get("definition", ""),
                    "related": results[0].get("related_terms", []),
                }
            )

    if not coverages:
        # Fallback to hardcoded info if taxonomic memory not populated
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

    return json.dumps(
        {
            "source": "knowledge_base",
            "coverages": coverages,
        },
        indent=2,
    )


# =============================================================================
# Agent Definition
# =============================================================================


class _AgentStateOptional(TypedDict, total=False):
    """Optional fields for agent state (not required in partial updates)."""

    user_id: Optional[str]
    session_id: Optional[str]  # Thread/session ID for conversation tracking
    memory_context: Optional[str]
    # Claim-related fields
    claim_id: Optional[str]
    policy_number: Optional[str]
    claim_amount: Optional[float]


class AgentState(_AgentStateOptional):
    """State for the insurance agent workflow.

    Uses inheritance to make optional fields truly optional (not required keys),
    while messages remains required. This allows LangGraph's partial state updates
    to type-check correctly.
    """

    messages: Annotated[list[BaseMessage], add_messages]


SYSTEM_PROMPT = """You are Alex, an AI insurance assistant for SecureLife Insurance, specializing in auto insurance.

## Your Role
Help customers with their complete insurance journey:
- Get personalized auto insurance quotes and purchase policies
- File and track insurance claims
- Resolve disputes with human-in-the-loop review when needed

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
- Explain insurance terminology and coverage options using the knowledge base

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
4. Generate a quote and explain the coverage
5. If they like the quote, create a policy for them

### For Returning Customers (when memory/policies found)
1. Recall their info and past conversations
2. Acknowledge their returning status by name if known
3. Check existing policies with `list_customer_policies`
4. Offer loyalty discounts on new quotes
5. Help with any claims or policy questions

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
6. **Decision Based on Risk Assessment**:
   - If `risk_assessment` is "low" AND claim amount < $1,000: Auto-approve with `resolve_claim`
   - If `risk_assessment` is "medium" or "high" OR claim amount >= $5,000: Call `human_review`
7. **After Human Review Returns**: When `human_review` suspends and then resumes with approval, you MUST call `resolve_claim` with the claim_id and the reviewer's decision before doing anything else.
8. **After Resolution**: Use `send_notification` to inform the customer of the outcome

## Risk Decision Guidelines

### Auto-Approve (No Human Review Needed)
- Claims under $1,000 with LOW risk assessment
- No fraud indicators detected
- Customer has clean claim history

### Request Human Review
- Claims $1,000-$5,000 OR MEDIUM risk assessment
- Claims over $5,000 OR HIGH risk assessment
- Multiple risk factors present
- Claim amount exceeds coverage limit
- Customer has elevated risk score (0.4+)
- Multiple previous claims on the policy

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
7. **policy_number**: Optional but recommended
8. **customer_name**: Optional but recommended

Example human_review call:
- claim_id="CLM-123"
- decision="approve"
- reason="High claim amount requires human approval"
- claim_amount=15000.0
- risk_level="high" (from analyze_claim_risk result)
- conversation_summary="Customer John Smith (policy POL-ABC123) reported a collision claim for $15,000 after a rear-end accident on January 15th. Policy is Comprehensive coverage with $50,000 limit and 0.35 risk score. Risk analysis returned HIGH risk with factors: (1) high claim amount exceeds $5,000 threshold, (2) elevated claim amount detected. Customer has 2 previous claims on this policy in the past year."
- policy_number="POL-ABC123"
- customer_name="John Smith"

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
- Example conversation flow:
  - User: "I was in an accident"
  - You: [FIRST ACTION: Call `list_customer_policies`] → finds POL-12345
  - You: "I'm sorry to hear about your accident. I see you have an active Comprehensive policy (POL-12345) for your 2024 Toyota. Can you tell me what happened?"
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

Example save_conversation_summary calls:
- After policy: `save_conversation_summary(title="Policy created for John Smith", summary="Created Comprehensive auto policy POL-123456 for 2022 Toyota Camry at $180/month", tags="policy_created,auto_insurance,comprehensive")`
- After claim: `save_conversation_summary(title="Claim filed for John Smith", summary="Filed collision claim CLM-789012 for $2,500 on policy POL-123456 after rear-end accident", tags="claim_filed,collision,POL-123456")`

Be friendly, professional, and guide customers toward the right coverage and quick claim resolutions.
"""


@app.entrypoint
def build_agent(llm: Optional[BaseChatModel] = None) -> CompiledStateGraph:
    """Build the LangGraph agent.

    Args:
        llm: LLM to use. Defaults to app.llm() when None. Pass an explicit
             LLM (e.g. a FakeLLM) for testing without real API calls.
    """
    logger.info("Building LangGraph agent...")

    # Get LLM from runtime
    if llm is None:
        llm = app.llm(_build_runtime_llm())

    # Get tools from runtime (automatically wrapped for SecureToolWrapper in AER mode)
    tools = app.get_tools()

    # Bind tool schemas to LLM
    llm_with_tools = llm.bind_tools(app.get_tool_schemas())

    def agent_node(state: AgentState) -> AgentState:
        from langchain_core.messages import HumanMessage

        messages = state["messages"]
        user_id = state.get("user_id", "")
        session_id = state.get("session_id", "")
        logger.info(f"User ID: {user_id}, Session ID: {session_id}")

        # Find the latest user message for memory context query
        latest_user_query = ""
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                latest_user_query = (
                    msg.content if isinstance(msg.content, str) else str(msg.content)
                )
                break

        # Build memory context.
        memory_context = ""
        if user_id and latest_user_query:
            memory_context = app.memory.build_context(
                query=latest_user_query,
                user_id=user_id,
                thread_id=session_id,
            )
            if memory_context:
                logger.info(
                    f"Built memory context (length={len(memory_context)}) for query: {latest_user_query[:50]}...\n{memory_context}"
                )
            else:
                logger.info("No memory context available")

        # Build system prompt, optionally including memory context and user ID
        system_prompt = SYSTEM_PROMPT

        # Tell the agent the current user's ID so it can use it with memory tools
        if user_id:
            system_prompt += f"\n\n## Current Session\nYou are speaking with user_id: {user_id}. Use this ID when calling memory tools."

        if memory_context:
            system_prompt += f"""

            ## Memory Context
            {memory_context}

            **IMPORTANT**:
            The memory context above provides helpful background information about the customer.

            However, for POLICY information (policy numbers, coverage details, active status),
            you MUST always call `list_customer_policies` to get the current, accurate data from
            the policy store. Memory context is supplementary - the policy store is the source of truth.

            For other information (customer preferences, past conversations, general background),
            you can use the memory context directly without calling tools.
            """

        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=system_prompt)] + list(messages)
        else:
            # Update existing system message if memory context available
            messages = [SystemMessage(content=system_prompt)] + list(messages[1:])

        # Invoke the LLM
        response = llm_with_tools.invoke(messages)

        # Apply guardrails validation to LLM response (if enabled)
        # This checks for competitor mentions, PII, and other configured rules
        response = app.validate_llm_response(response)

        return {"messages": [response]}

    def should_continue(state: AgentState) -> Literal["tools", "end"]:
        last = state["messages"][-1]
        return "tools" if hasattr(last, "tool_calls") and last.tool_calls else "end"  # type: ignore[union-attr]  # LangChain BaseMessage subclass has tool_calls

    logger.info("  → Compiling graph...")
    builder = StateGraph(AgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", should_continue, {"tools": "tools", "end": END})
    builder.add_edge("tools", "agent")

    graph = builder.compile(checkpointer=app.checkpointer())
    logger.info("✅ Agent graph compiled")
    return graph


# =============================================================================
# Run
# =============================================================================


def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info(f"🚀 Starting {APP_NAME} (Runner SDK)")
    logger.info("=" * 60)

    app.run()


if __name__ == "__main__":
    main()
