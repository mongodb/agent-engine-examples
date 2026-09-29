import { describe, expect, it } from "vitest";
import {
  initialResumeMap,
  parseResumeMap,
  type PendingInterrupt,
} from "@/lib/reviews";

const claimInterrupt: PendingInterrupt = {
  id: "claim-review-1",
  value: null,
  responseSchema: {
    type: "object",
    required: ["decision"],
    properties: {
      decision: { type: "string", title: "Decision", enum: ["approved", "denied"] },
    },
    additionalProperties: false,
  },
};

describe("native interrupt resume maps", () => {
  it("seeds required fields without preselecting a decision", () => {
    expect(initialResumeMap([claimInterrupt])).toBe(
      JSON.stringify({ "claim-review-1": { decision: null } }, null, 2)
    );
  });

  it("validates keys and supported response fields", () => {
    expect(parseResumeMap('{"wrong-id":true}', [claimInterrupt])).toEqual({
      error:
        "Resume map keys must exactly match the pending interrupt IDs. Missing: claim-review-1. Unexpected: wrong-id.",
    });
    expect(
      parseResumeMap('{"claim-review-1":{"decision":"approve"}}', [claimInterrupt])
    ).toEqual({ error: 'Decision must be one of: "approved", "denied".' });
  });

  it("leaves unsupported schemas to the platform validator", () => {
    const interrupt: PendingInterrupt = {
      id: "object-choice",
      value: null,
      responseSchema: {
        type: "object",
        required: ["choice"],
        properties: {
          choice: { enum: [{ kind: "approve" }, { kind: "deny" }] },
        },
        additionalProperties: false,
      },
    };
    const resumeMap = { "object-choice": { choice: { kind: "approve" } } };

    expect(parseResumeMap(JSON.stringify(resumeMap), [interrupt])).toEqual({
      resumeMap,
    });
  });
});
