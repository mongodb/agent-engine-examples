/**
 * Unit tests for assessClaimRisk — the pure risk-scoring logic factored out of
 * the analyze_claim_risk tool. Covers the low/medium/high branches, the numeric
 * boundary thresholds, and the keyword-triggered paths.
 */

import { describe, expect, it } from "vitest";

import { assessClaimRisk } from "../src/insurance_agent_ts/riskAnalysis.js";
import type { Claim, Policy } from "../src/insurance_agent_ts/policyStore.js";

function makePolicy(overrides: Partial<Policy> = {}): Policy {
  return {
    policy_number: "POL-10000",
    user_id: "user-1",
    holder_name: "Test Holder",
    holder_email: "test@example.com",
    type: "Auto Insurance",
    coverage: "Standard",
    coverage_limit: 50000,
    premium: "$120/month",
    risk_score: 0.3,
    car_model: "Camry",
    car_year: 2022,
    car_edition: "SE",
    ...overrides,
  };
}

function makeClaim(overrides: Partial<Claim> = {}): Claim {
  return {
    claim_id: "CLM-10000",
    policy_number: "POL-10000",
    user_id: "user-1",
    claim_type: "collision",
    claim_amount: 1000,
    description: "Minor fender bender in a parking lot",
    ...overrides,
  };
}

describe("assessClaimRisk", () => {
  it("returns low risk / auto_approve for a small, clean claim", () => {
    const result = assessClaimRisk(makeClaim(), makePolicy(), []);
    expect(result.risk_assessment).toBe("low");
    expect(result.recommendation).toBe("auto_approve");
  });

  it("returns medium risk at the medium dollar threshold", () => {
    const result = assessClaimRisk(
      makeClaim({ claim_amount: 5000 }),
      makePolicy(),
      [],
    );
    expect(result.risk_assessment).toBe("medium");
    expect(result.recommendation).toBe("review_recommended");
  });

  it("stays low just below the medium dollar threshold", () => {
    const result = assessClaimRisk(
      makeClaim({ claim_amount: 4999 }),
      makePolicy(),
      [],
    );
    expect(result.risk_assessment).toBe("low");
  });

  it("returns high risk at the high dollar threshold", () => {
    const result = assessClaimRisk(
      makeClaim({ claim_amount: 15000 }),
      makePolicy(),
      [],
    );
    expect(result.risk_assessment).toBe("high");
    expect(result.recommendation).toBe("manual_review_required");
  });

  it("escalates to high when the claim exceeds the coverage limit", () => {
    const result = assessClaimRisk(
      makeClaim({ claim_amount: 6000 }),
      makePolicy({ coverage_limit: 5000 }),
      [],
    );
    expect(result.risk_assessment).toBe("high");
    expect(result.factors.some((f) => f.includes("exceeds coverage limit"))).toBe(true);
  });

  it("escalates to high on a high policy risk score", () => {
    const result = assessClaimRisk(
      makeClaim(),
      makePolicy({ risk_score: 0.6 }),
      [],
    );
    expect(result.risk_assessment).toBe("high");
  });

  it("escalates to medium on an elevated policy risk score", () => {
    const result = assessClaimRisk(
      makeClaim(),
      makePolicy({ risk_score: 0.4 }),
      [],
    );
    expect(result.risk_assessment).toBe("medium");
  });

  it("escalates to medium after two or more previous claims", () => {
    const prior = [makeClaim({ claim_id: "CLM-1" }), makeClaim({ claim_id: "CLM-2" })];
    const result = assessClaimRisk(makeClaim(), makePolicy(), prior);
    expect(result.risk_assessment).toBe("medium");
  });

  it("detects a high-risk keyword in the description", () => {
    const result = assessClaimRisk(
      makeClaim({ description: "Vehicle was stolen from the driveway" }),
      makePolicy(),
      [],
    );
    expect(result.risk_assessment).toBe("high");
    expect(result.factors.some((f) => f.includes("High-risk keywords"))).toBe(true);
  });

  it("detects a high-risk keyword from the claim_type", () => {
    const result = assessClaimRisk(
      makeClaim({ claim_type: "theft", description: "car gone" }),
      makePolicy(),
      [],
    );
    expect(result.risk_assessment).toBe("high");
  });

  it("detects a medium-risk keyword in the description", () => {
    const result = assessClaimRisk(
      makeClaim({ description: "Structural damage to the frame" }),
      makePolicy(),
      [],
    );
    expect(result.risk_assessment).toBe("medium");
  });

  it("uses defaults when optional policy fields are missing", () => {
    const policy = makePolicy({ risk_score: undefined, coverage_limit: undefined });
    const result = assessClaimRisk(makeClaim(), policy, []);
    expect(result.risk_assessment).toBe("low");
  });
});
