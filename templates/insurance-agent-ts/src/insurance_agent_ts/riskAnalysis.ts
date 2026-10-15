/**
 * Claim risk-scoring rules, factored out of the tools so the logic is
 * unit-testable and the `analyze_claim_risk` tool stays thin.
 */

import type { Claim, Policy } from "./policyStore.js";

// Keywords in a claim description that indicate high / medium risk.
const HIGH_RISK_KEYWORDS = ["total loss", "totaled", "stolen", "theft", "fire", "flood"];
const MEDIUM_RISK_KEYWORDS = ["significant damage", "major repair", "structural"];

// Numeric thresholds (dollars).
const LOW_RISK_THRESHOLD = 1000;
const MEDIUM_RISK_THRESHOLD = 5000;
const HIGH_RISK_THRESHOLD = 15000;

// Policy risk-score thresholds (0.0 - 1.0).
const MEDIUM_RISK_SCORE = 0.4;
const HIGH_RISK_SCORE = 0.6;

export interface RiskAssessment {
  risk_assessment: "low" | "medium" | "high";
  confidence: number;
  recommendation: "auto_approve" | "review_recommended" | "manual_review_required";
  reasoning: string;
  factors: string[];
}

/**
 * Assess a claim against its policy and prior claims. Pure function — the
 * caller persists the result and decides on the approval path.
 */
export function assessClaimRisk(
  claim: Claim,
  policy: Policy,
  previousClaims: Claim[],
): RiskAssessment {
  const claimAmount = claim.claim_amount ?? 0;
  const policyRiskScore = policy.risk_score ?? 0.3;
  const coverageLimit = policy.coverage_limit ?? 50000;
  const descriptionLower = `${claim.description} ${claim.claim_type}`.toLowerCase();

  const isHighRisk =
    claimAmount >= HIGH_RISK_THRESHOLD ||
    policyRiskScore >= HIGH_RISK_SCORE ||
    claimAmount > coverageLimit ||
    HIGH_RISK_KEYWORDS.some((kw) => descriptionLower.includes(kw));

  const isMediumRisk =
    claimAmount >= MEDIUM_RISK_THRESHOLD ||
    policyRiskScore >= MEDIUM_RISK_SCORE ||
    previousClaims.length >= 2 ||
    MEDIUM_RISK_KEYWORDS.some((kw) => descriptionLower.includes(kw));

  if (isHighRisk) {
    const factors: string[] = [];
    if (claimAmount > coverageLimit) {
      factors.push(
        `Claim amount ($${claimAmount}) exceeds coverage limit ($${coverageLimit})`,
      );
    }
    if (policyRiskScore >= HIGH_RISK_SCORE) {
      factors.push(`High policy risk score: ${policyRiskScore}`);
    }
    if (claimAmount >= HIGH_RISK_THRESHOLD) {
      factors.push(`High claim amount: $${claimAmount}`);
    }
    if (HIGH_RISK_KEYWORDS.some((kw) => descriptionLower.includes(kw))) {
      factors.push("High-risk keywords detected in claim description");
    }
    return {
      risk_assessment: "high",
      confidence: 0.65,
      recommendation: "manual_review_required",
      reasoning:
        "Multiple risk indicators detected. Human review strongly recommended.",
      factors,
    };
  }

  if (isMediumRisk) {
    const factors: string[] = [];
    if (claimAmount >= MEDIUM_RISK_THRESHOLD) {
      factors.push(`Elevated claim amount: $${claimAmount}`);
    }
    if (previousClaims.length >= 2) {
      factors.push(`Multiple previous claims: ${previousClaims.length}`);
    }
    if (policyRiskScore >= MEDIUM_RISK_SCORE) {
      factors.push(`Elevated policy risk score: ${policyRiskScore}`);
    }
    return {
      risk_assessment: "medium",
      confidence: 0.72,
      recommendation: "review_recommended",
      reasoning: "Claim amount is elevated or patterns warrant review.",
      factors,
    };
  }

  // Deterministic routing boundary: only low-risk claims under the
  // auto-approval threshold may resolve without human review.
  const autoApprove = claimAmount < LOW_RISK_THRESHOLD;
  return {
    risk_assessment: "low",
    confidence: 0.85,
    recommendation: autoApprove ? "auto_approve" : "review_recommended",
    reasoning: autoApprove
      ? "Claim amount is within normal range, no fraud indicators detected."
      : "Low-risk analysis, but the claim amount requires human review.",
    factors: [
      autoApprove
        ? `Claim amount ($${claimAmount}) is reasonable`
        : `Claim amount ($${claimAmount}) requires human review`,
      `Policy risk score (${policyRiskScore}) is low`,
      "No concerning patterns in claim description",
    ],
  };
}
