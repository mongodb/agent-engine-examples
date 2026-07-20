/**
 * Tool registrations for the insurance agent.
 *
 * Tools fall into two groups:
 *  - Policy / claim tools backed by MongoDB (`policyStore.ts`).
 *  - Memory tools backed by the platform Memory Server via `app.memory`
 *    (semantic customer facts, episodic conversation summaries, taxonomic
 *    knowledge base). In app-bound mode identity (user/session) is resolved
 *    from the ambient execution context, so these calls omit userId/sessionId.
 *
 * `is_local` flags match the Python reference: memory/store tools run remotely
 * in the Tool Pod; `human_review` and `send_notification` run locally in the AER.
 */

import { z } from "zod";
import type { App } from "@magenta/magenta-sdklanggraph-ts";

import type { ClaimStore, PolicyStore } from "./policyStore.js";
import { assessClaimRisk } from "./riskAnalysis.js";

function randInt(min: number, max: number): number {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function shortId(): string {
  return Math.random().toString(16).slice(2, 10).toUpperCase();
}

/** Coerce a `build_context` result's `formatted_context` (string | array) to text. */
function contextToText(formatted: unknown): string {
  if (typeof formatted === "string") return formatted;
  if (Array.isArray(formatted) && formatted.length > 0) {
    return JSON.stringify(formatted);
  }
  return "";
}

/**
 * Ownership guards. Policy/claim IDs live in a small, easily-guessable space, so
 * every tool that reads or mutates a record must confirm it belongs to the
 * current user before returning anything. A missing/unauthenticated user id
 * (empty string) never owns a record. Callers surface a generic not-found so the
 * existence of another user's record is not leaked.
 */
function ownsPolicy(policy: { user_id: string }, userId: string): boolean {
  return userId !== "" && policy.user_id === userId;
}

function ownsClaim(claim: { user_id: string }, userId: string): boolean {
  return userId !== "" && claim.user_id === userId;
}

/**
 * Log the full error server-side and return a generic, customer-safe message.
 * Raw driver errors can carry internal details (collection/index names,
 * connection state) that must not flow back into the agent conversation.
 */
function safeErrorMessage(context: string, err: unknown): string {
  console.error(`[insurance-agent-ts] ${context}:`, err);
  return context;
}

/** MongoDB duplicate-key error (violates a unique index). */
function isDuplicateKeyError(err: unknown): boolean {
  return (
    typeof err === "object" && err !== null && (err as { code?: unknown }).code === 11000
  );
}

const ID_RETRY_ATTEMPTS = 5;

/**
 * Run `attempt(id)` with a freshly generated id, retrying on a duplicate-key
 * error. IDs are drawn from a small numeric space, so an occasional collision is
 * expected as the collection grows — a bounded retry with a new id recovers
 * transparently instead of surfacing a raw duplicate-key error to the caller.
 */
async function withIdRetry<T>(
  generateId: () => string,
  attempt: (id: string) => Promise<T>,
): Promise<T> {
  let lastErr: unknown;
  for (let i = 0; i < ID_RETRY_ATTEMPTS; i++) {
    try {
      return await attempt(generateId());
    } catch (err) {
      if (!isDuplicateKeyError(err)) throw err;
      lastErr = err;
    }
  }
  throw lastErr;
}

export function registerTools(
  app: App,
  policyStore: PolicyStore | null,
  claimStore: ClaimStore | null,
): void {
  // ==========================================================================
  // Policy tools
  // ==========================================================================

  app.tool({
    isLocal: false,
    description: "Look up an insurance policy by policy number.",
    schema: z.object({ policy_number: z.string() }),
  })(async function lookup_policy({
    policy_number,
  }: {
    policy_number: string;
  }): Promise<string> {
    if (!policyStore) return JSON.stringify({ error: "Policy store not configured" });
    const userId = app.getCurrentUserId() ?? "";
    const policy = await policyStore.getPolicy(policy_number);
    // Generic not-found when the policy is missing OR not owned, so a guessed
    // policy number can't confirm another customer's policy exists.
    if (!policy || !ownsPolicy(policy, userId)) {
      return JSON.stringify({ error: `No policy found: ${policy_number}` });
    }
    return JSON.stringify(policy, null, 2);
  });

  app.tool({
    isLocal: false,
    description:
      "Generate a personalized auto insurance quote. Applies loyalty and good-driver " +
      "discounts for returning customers using memory and existing policies.",
    schema: z.object({
      vehicle_year: z.number().int().describe("Year of the vehicle, e.g. 2022"),
      vehicle_make: z.string().describe('Make, e.g. "Toyota"'),
      vehicle_model: z.string().describe('Model, e.g. "Camry"'),
      coverage_level: z.string().describe("Basic, Standard, or Comprehensive"),
      driver_age: z.number().int().default(0).describe("Primary driver age (optional)"),
    }),
  })(async function get_quote({
    vehicle_year,
    vehicle_make,
    vehicle_model,
    coverage_level,
    driver_age,
  }: {
    vehicle_year: number;
    vehicle_make: string;
    vehicle_model: string;
    coverage_level: string;
    driver_age: number;
  }): Promise<string> {
    const userId = app.getCurrentUserId() ?? "";

    const basePrices: Record<string, number> = {
      basic: 80,
      standard: 120,
      comprehensive: 180,
    };
    let base = basePrices[coverage_level.toLowerCase()] ?? 120;

    const vehicleAge = new Date().getFullYear() - vehicle_year;
    if (vehicleAge <= 2) base *= 1.2;
    else if (vehicleAge >= 10) base *= 0.85;

    if (driver_age > 0) {
      if (driver_age < 25) base *= 1.5;
      else if (driver_age >= 55) base *= 0.9;
    }

    let loyaltyDiscount = 0;
    const customerNotes: string[] = [];

    const existingPolicies =
      policyStore && userId ? await policyStore.listByUserId(userId) : [];
    const hasExistingPolicies = existingPolicies.length > 0;

    let hasPastInteractions = false;
    try {
      const pastEpisodes = await app.memory.searchEpisodes({
        query: "policy quote conversation",
        topK: 3,
      });
      hasPastInteractions = pastEpisodes.length > 0;
    } catch {
      hasPastInteractions = false;
    }

    if (hasExistingPolicies || hasPastInteractions) {
      customerNotes.push("Returning customer - 10% loyalty discount applied");
      loyaltyDiscount = 0.1;
      try {
        const context = await app.memory.buildContext({
          query: "driving history record accidents",
        });
        const memoryContext = contextToText(context.formatted_context).toLowerCase();
        if (
          memoryContext.includes("clean driving") ||
          memoryContext.includes("no accidents")
        ) {
          customerNotes.push("Good driver discount - additional 5% off");
          loyaltyDiscount += 0.05;
        }
      } catch {
        // Memory unavailable — skip the good-driver bonus.
      }
    }

    const finalPrice = base * (1 - loyaltyDiscount);
    const validUntil = new Date();
    validUntil.setDate(validUntil.getDate() + 30);

    const quote: Record<string, unknown> = {
      quote_id: `QT-${randInt(100000, 999999)}`,
      vehicle: `${vehicle_year} ${vehicle_make} ${vehicle_model}`,
      coverage_level:
        coverage_level.charAt(0).toUpperCase() + coverage_level.slice(1).toLowerCase(),
      monthly_premium: `$${finalPrice.toFixed(2)}`,
      annual_premium: `$${(finalPrice * 12).toFixed(2)}`,
      valid_until: validUntil.toISOString().slice(0, 10),
      deductible: coverage_level.toLowerCase() !== "basic" ? "$500" : "$1000",
    };
    if (customerNotes.length > 0) quote.applied_discounts = customerNotes;
    if (!hasExistingPolicies && !hasPastInteractions) {
      quote.tip =
        "Purchase a policy to become a returning customer and get loyalty discounts on future quotes!";
    }
    return JSON.stringify(quote, null, 2);
  });

  app.tool({
    isLocal: false,
    description: "Create a new insurance policy for the current customer.",
    schema: z.object({
      holder_name: z.string(),
      holder_email: z.string(),
      policy_type: z.string().describe("Auto, Home, or Life"),
      coverage: z.string().describe("Basic, Standard, or Comprehensive"),
      premium: z.string().describe('Monthly premium, e.g. "$150/month"'),
      car_model: z.string(),
      car_year: z.number().int(),
      car_edition: z.string().describe('Trim, e.g. "SE"'),
      deductible: z.string().default("$500"),
    }),
  })(async function create_policy(args: {
    holder_name: string;
    holder_email: string;
    policy_type: string;
    coverage: string;
    premium: string;
    car_model: string;
    car_year: number;
    car_edition: string;
    deductible: string;
  }): Promise<string> {
    if (!policyStore) return JSON.stringify({ error: "Policy store not configured" });
    const userId = app.getCurrentUserId() ?? "";
    try {
      const created = await withIdRetry(
        () => `POL-${randInt(10000, 99999)}`,
        (policyNumber) =>
          policyStore.createPolicy({
            policy_number: policyNumber,
            user_id: userId,
            holder_name: args.holder_name,
            holder_email: args.holder_email,
            type: `${args.policy_type} Insurance`,
            coverage: args.coverage,
            premium: args.premium,
            deductible: args.deductible,
            car_model: args.car_model,
            car_year: args.car_year,
            car_edition: args.car_edition,
          }),
      );
      return JSON.stringify({ status: "created", policy: created }, null, 2);
    } catch (err) {
      return JSON.stringify({ error: safeErrorMessage("Failed to create policy", err) });
    }
  });

  app.tool({
    isLocal: false,
    description: "List all insurance policies for the current customer.",
    schema: z.object({}),
  })(async function list_customer_policies(): Promise<string> {
    if (!policyStore) return JSON.stringify({ error: "Policy store not configured" });
    const userId = app.getCurrentUserId() ?? "";
    const policies = await policyStore.listByUserId(userId);
    return policies.length > 0
      ? JSON.stringify({ count: policies.length, policies }, null, 2)
      : JSON.stringify({ count: 0, message: "No policies found for this customer" });
  });

  // ==========================================================================
  // Claim tools
  // ==========================================================================

  app.tool({
    isLocal: false,
    description: "File a new insurance claim for a policy.",
    schema: z.object({
      policy_number: z.string(),
      claim_type: z
        .string()
        .describe("collision, theft, comprehensive, glass, or vandalism"),
      claim_amount: z.number().describe("Estimated or actual claim amount in dollars"),
      description: z.string().describe("Description of the incident"),
    }),
  })(async function file_claim({
    policy_number,
    claim_type,
    claim_amount,
    description,
  }: {
    policy_number: string;
    claim_type: string;
    claim_amount: number;
    description: string;
  }): Promise<string> {
    if (!policyStore || !claimStore) {
      return JSON.stringify({ error: "Policy/claim store not configured" });
    }
    const userId = app.getCurrentUserId() ?? "";
    const policy = await policyStore.getPolicy(policy_number);
    if (!policy || !ownsPolicy(policy, userId)) {
      return JSON.stringify({ error: `Policy not found: ${policy_number}` });
    }
    if (policy.status !== "Active") {
      return JSON.stringify({ error: `Policy is not active: ${policy.status}` });
    }
    try {
      const created = await withIdRetry(
        () => `CLM-${randInt(10000, 99999)}`,
        (claimId) =>
          claimStore.createClaim({
            claim_id: claimId,
            policy_number: policy_number.toUpperCase(),
            user_id: userId,
            claim_type: claim_type.toLowerCase(),
            claim_amount,
            description,
          }),
      );
      return JSON.stringify(
        {
          status: "filed",
          claim: created,
          message: `Claim ${created.claim_id} has been filed successfully. It will now be analyzed for risk assessment.`,
        },
        null,
        2,
      );
    } catch (err) {
      return JSON.stringify({ error: safeErrorMessage("Failed to file claim", err) });
    }
  });

  app.tool({
    isLocal: false,
    catalog: "openai/chat",
    description:
      "Analyze a filed claim for risk. Returns a risk_assessment (low/medium/high) and " +
      "a recommendation (auto_approve, review_recommended, manual_review_required).",
    schema: z.object({ claim_id: z.string(), policy_number: z.string() }),
  })(async function analyze_claim_risk({
    claim_id,
    policy_number,
  }: {
    claim_id: string;
    policy_number: string;
  }): Promise<string> {
    if (!policyStore || !claimStore) {
      return JSON.stringify({ error: "Policy/claim store not configured" });
    }
    const userId = app.getCurrentUserId() ?? "";
    const claim = await claimStore.getClaim(claim_id);
    if (!claim || !ownsClaim(claim, userId)) {
      return JSON.stringify({ error: `Claim not found: ${claim_id}` });
    }
    const policy = await policyStore.getPolicy(policy_number);
    if (!policy || !ownsPolicy(policy, userId)) {
      return JSON.stringify({ error: `Policy not found: ${policy_number}` });
    }

    const previousClaims = (await claimStore.listByPolicy(policy_number)).filter(
      (c) => c.claim_id !== claim_id.toUpperCase(),
    );
    const result = assessClaimRisk(claim, policy, previousClaims);

    await claimStore.updateClaim(claim_id, {
      risk_assessment: result.risk_assessment,
      risk_confidence: result.confidence,
      recommendation: result.recommendation,
      status: "under_review",
    });
    return JSON.stringify(result, null, 2);
  });

  app.tool({
    isLocal: true,
    description:
      "Suspend execution and request human review for a high-risk or high-value claim. " +
      "The graph pauses until a human resumes it with a decision.",
    schema: z.object({
      claim_id: z.string(),
      decision: z.string().describe("Preliminary decision: approve, deny, or adjust"),
      reason: z.string(),
      claim_amount: z.number(),
      risk_level: z.string().describe("low, medium, or high from analyze_claim_risk"),
      conversation_summary: z.string(),
      policy_number: z.string().default(""),
      customer_name: z.string().default(""),
    }),
  })(async function human_review(args: {
    claim_id: string;
    decision: string;
    reason: string;
    claim_amount: number;
    risk_level: string;
    conversation_summary: string;
    policy_number: string;
    customer_name: string;
  }): Promise<string> {
    if (claimStore) {
      try {
        await claimStore.updateClaim(args.claim_id, { status: "suspended_for_review" });
      } catch (err) {
        return JSON.stringify({
          error: safeErrorMessage("Failed to update claim for review", err),
        });
      }
    }
    return app.suspend("awaiting_human_review", {
      task_id: `REVIEW-${shortId()}`,
      claim_id: args.claim_id,
      policy_number: args.policy_number,
      customer_name: args.customer_name,
      decision: args.decision,
      reason: args.reason,
      claim_amount: args.claim_amount,
      risk_level: args.risk_level,
      conversation_summary: args.conversation_summary,
      created_at: new Date().toISOString(),
      instructions: "Please review the claim and provide approval or denial decision.",
    });
  });

  app.tool({
    isLocal: true,
    redactFields: ["recipient_email"],
    description:
      "Send a notification to a customer about claim resolution or policy updates. " +
      "recipient_email is redacted in execution logs.",
    schema: z.object({
      recipient_email: z.string(),
      subject: z.string(),
      body: z.string(),
      notification_type: z.string().default("email"),
    }),
  })(function send_notification({
    recipient_email,
    subject,
    notification_type,
  }: {
    recipient_email: string;
    subject: string;
    body: string;
    notification_type: string;
  }): string {
    return JSON.stringify(
      {
        notification_id: `NOTIF-${shortId()}`,
        status: "sent",
        recipient: recipient_email,
        type: notification_type,
        subject,
        sent_at: new Date().toISOString(),
        message: "Notification sent successfully.",
      },
      null,
      2,
    );
  });

  app.tool({
    isLocal: false,
    description: "Check the current status of an insurance claim.",
    schema: z.object({ claim_id: z.string() }),
  })(async function check_claim_status({
    claim_id,
  }: {
    claim_id: string;
  }): Promise<string> {
    if (!claimStore) return JSON.stringify({ error: "Claim store not configured" });
    const userId = app.getCurrentUserId() ?? "";
    const claim = await claimStore.getClaim(claim_id);
    if (!claim || !ownsClaim(claim, userId)) {
      return JSON.stringify({ error: `Claim not found: ${claim_id}` });
    }
    const response: Record<string, unknown> = {
      claim_id: claim.claim_id,
      status: claim.status,
      claim_type: claim.claim_type,
      claim_amount: claim.claim_amount,
      description: claim.description,
      created_at: claim.created_at,
      updated_at: claim.updated_at,
    };
    if (claim.risk_assessment) {
      response.risk_assessment = {
        level: claim.risk_assessment,
        confidence: claim.risk_confidence,
        recommendation: claim.recommendation,
      };
    }
    if (claim.resolution) {
      response.resolution = {
        decision: claim.resolution,
        reviewer_notes: claim.reviewer_notes,
        resolved_at: claim.resolved_at,
      };
    }
    return JSON.stringify(response, null, 2);
  });

  app.tool({
    isLocal: false,
    description: "List all insurance claims for the current customer.",
    schema: z.object({}),
  })(async function list_customer_claims(): Promise<string> {
    if (!claimStore) return JSON.stringify({ error: "Claim store not configured" });
    const userId = app.getCurrentUserId() ?? "";
    const claims = await claimStore.listByUserId(userId);
    return claims.length > 0
      ? JSON.stringify({ count: claims.length, claims }, null, 2)
      : JSON.stringify({ count: 0, message: "No claims found for this customer" });
  });

  app.tool({
    isLocal: false,
    description:
      "Resolve a claim after human review or auto-approval (approved, denied, approved_with_adjustment).",
    schema: z.object({
      claim_id: z.string(),
      resolution: z.string(),
      reviewer_notes: z.string().default(""),
    }),
  })(async function resolve_claim({
    claim_id,
    resolution,
    reviewer_notes,
  }: {
    claim_id: string;
    resolution: string;
    reviewer_notes: string;
  }): Promise<string> {
    if (!claimStore) return JSON.stringify({ error: "Claim store not configured" });
    const userId = app.getCurrentUserId() ?? "";
    const claim = await claimStore.getClaim(claim_id);
    if (!claim || !ownsClaim(claim, userId)) {
      return JSON.stringify({ error: `Claim not found: ${claim_id}` });
    }
    if (claim.resolution) {
      return JSON.stringify({
        error: `Claim ${claim_id} is already resolved (${claim.resolution}).`,
      });
    }
    try {
      const resolved = await claimStore.resolveClaim(claim_id, resolution, reviewer_notes);
      return resolved
        ? JSON.stringify(
            {
              status: "resolved",
              claim_id,
              resolution,
              resolved_at: resolved.resolved_at,
              message: `Claim ${claim_id} has been ${resolution}.`,
            },
            null,
            2,
          )
        : JSON.stringify({ error: "Failed to resolve claim" });
    } catch (err) {
      return JSON.stringify({ error: safeErrorMessage("Failed to resolve claim", err) });
    }
  });

  // ==========================================================================
  // Memory tools — semantic (customer facts)
  // ==========================================================================

  app.tool({
    isLocal: false,
    description:
      "Save a piece of customer information to long-term memory for future conversations. " +
      'Each fact has its own key (e.g. "name", "email", "vehicle_preference").',
    schema: z.object({
      info_key: z.string(),
      info_value: z.string(),
    }),
  })(async function save_customer_info({
    info_key,
    info_value,
  }: {
    info_key: string;
    info_value: string;
  }): Promise<string> {
    const userId = app.getCurrentUserId() ?? "";
    try {
      const result = await app.memory.saveSemantic({
        text: `Customer ${info_key}: ${info_value}`,
        label: `${userId}_${info_key}`,
        source: "insurance_agent_ts",
        metadata: { type: "customer_info", info_key },
      });
      return result.acknowledged
        ? JSON.stringify({ status: "saved", info_key, user_id: userId })
        : JSON.stringify({ status: "error", error: "Memory not enabled" });
    } catch (err) {
      return JSON.stringify({
        status: "error",
        error: safeErrorMessage("Failed to save customer information", err),
      });
    }
  });

  app.tool({
    isLocal: false,
    description: "Recall all stored information about the current customer.",
    schema: z.object({}),
  })(async function recall_customer_info(): Promise<string> {
    const userId = app.getCurrentUserId() ?? "";
    try {
      const context = await app.memory.buildContext({
        query: `customer profile and information for user ${userId}`,
      });
      const profile = contextToText(context.formatted_context);
      return profile
        ? JSON.stringify({ user_id: userId, found: true, profile })
        : JSON.stringify({
            user_id: userId,
            found: false,
            message: "No stored information found for this customer.",
          });
    } catch (err) {
      return JSON.stringify({
        user_id: userId,
        found: false,
        message: safeErrorMessage("Failed to recall customer information", err),
      });
    }
  });

  // ==========================================================================
  // Memory tools — episodic (conversation summaries)
  // ==========================================================================

  app.tool({
    isLocal: false,
    description:
      "Save a summary of the current conversation to episodic memory for future reference.",
    schema: z.object({
      title: z.string(),
      summary: z.string(),
      tags: z.string().default("").describe("Comma-separated tags"),
    }),
  })(async function save_conversation_summary({
    title,
    summary,
    tags,
  }: {
    title: string;
    summary: string;
    tags: string;
  }): Promise<string> {
    const tagList = tags
      ? tags.split(",").map((t) => t.trim()).filter(Boolean)
      : [];
    try {
      const episode = await app.memory.saveEpisode({
        title,
        content: summary,
        summary,
        participants: ["Customer", "Alex (AI Assistant)"],
        tags: tagList,
        visibility: "private",
      });
      return episode.acknowledged
        ? JSON.stringify(
            {
              status: "saved",
              episode_id: episode.id,
              title,
              tags: tagList,
              message: "Conversation summary saved for future reference.",
            },
            null,
            2,
          )
        : JSON.stringify({
            status: "error",
            message: "Failed to save conversation summary. Memory may not be enabled.",
          });
    } catch (err) {
      return JSON.stringify({
        status: "error",
        message: safeErrorMessage("Failed to save conversation summary", err),
      });
    }
  });

  app.tool({
    isLocal: false,
    description: "Recall past conversations and interactions with the customer.",
    schema: z.object({
      query: z.string().default("").describe("Optional search query"),
    }),
  })(async function recall_past_conversations({
    query,
  }: {
    query: string;
  }): Promise<string> {
    const userId = app.getCurrentUserId() ?? "";
    const searchQuery = query || `customer interactions for ${userId}`;
    try {
      const episodes = await app.memory.searchEpisodes({ query: searchQuery, topK: 5 });
      if (episodes.length === 0) {
        return JSON.stringify({
          found: false,
          user_id: userId,
          message: "No past conversations found for this customer.",
        });
      }
      const conversations = episodes.map((ep) => {
        const meta = ep.metadata ?? {};
        return {
          title: meta.title ?? "",
          summary: meta.summary ?? ep.content.slice(0, 200),
          tags: meta.tags ?? [],
        };
      });
      return JSON.stringify(
        { found: true, user_id: userId, count: conversations.length, conversations },
        null,
        2,
      );
    } catch (err) {
      return JSON.stringify({
        found: false,
        user_id: userId,
        message: safeErrorMessage("Failed to recall past conversations", err),
      });
    }
  });

  // ==========================================================================
  // Memory tools — taxonomic (insurance knowledge base)
  // ==========================================================================

  app.tool({
    isLocal: false,
    description: "Look up and explain an insurance term using the knowledge base.",
    schema: z.object({ term: z.string() }),
  })(async function explain_insurance_term({
    term,
  }: {
    term: string;
  }): Promise<string> {
    try {
      const results = await app.memory.searchTaxonomic({ query: term, topK: 3 });
      if (results.length === 0) {
        return JSON.stringify({
          found: false,
          term,
          message: `No definition found for '${term}'. Please ask the customer to clarify.`,
        });
      }
      const best = results[0]!.metadata ?? {};
      const response: Record<string, unknown> = {
        found: true,
        term: best.term ?? term,
        domain: best.domain ?? "insurance",
        definition: best.definition ?? "",
        related_terms: best.related_terms ?? [],
      };
      if (results.length > 1) {
        response.related_concepts = results.slice(1, 3).map((r) => {
          const m = r.metadata ?? {};
          return {
            term: m.term ?? "",
            domain: m.domain ?? "",
            definition: `${String(m.definition ?? "").slice(0, 100)}...`,
          };
        });
      }
      return JSON.stringify(response, null, 2);
    } catch (err) {
      return JSON.stringify({
        found: false,
        term,
        message: safeErrorMessage("Failed to look up insurance term", err),
      });
    }
  });

  app.tool({
    isLocal: false,
    description:
      "Get information about available coverage levels (Basic, Standard, Comprehensive).",
    schema: z.object({}),
  })(async function get_coverage_options(): Promise<string> {
    const coverageTerms = ["basic coverage", "standard coverage", "comprehensive coverage"];
    const coverages: Array<Record<string, unknown>> = [];
    try {
      for (const term of coverageTerms) {
        const results = await app.memory.searchTaxonomic({
          query: term,
          domain: "coverage_types",
          topK: 1,
        });
        if (results.length > 0) {
          const m = results[0]!.metadata ?? {};
          coverages.push({
            level: m.term ?? term,
            description: m.definition ?? "",
            related: m.related_terms ?? [],
          });
        }
      }
    } catch {
      // Fall through to the hardcoded defaults below.
    }
    if (coverages.length === 0) {
      return JSON.stringify(
        {
          source: "default",
          coverages: [
            { level: "Basic", description: "Liability only - covers damages to others" },
            {
              level: "Standard",
              description: "Collision + Liability - covers your car in accidents",
            },
            {
              level: "Comprehensive",
              description: "Full coverage including theft, weather, vandalism",
            },
          ],
        },
        null,
        2,
      );
    }
    return JSON.stringify({ source: "knowledge_base", coverages }, null, 2);
  });
}
