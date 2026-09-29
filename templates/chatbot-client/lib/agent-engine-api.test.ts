import { describe, expect, it } from "vitest";
import {
  getAgentEngineTarget,
  isSafeExecutionId,
  isSafeSessionId,
  normalizeChatHistory,
  normalizeReviews,
} from "@/lib/agent-engine-api";

describe("getAgentEngineTarget", () => {
  it("derives deployed review and synchronous invoke URLs", () => {
    const target = getAgentEngineTarget(
      "https://agent-engine.example.com/api/v1/projects/project-1/workspaces/workspace-1/invokeStream"
    );

    expect(target.mode).toBe("deployed");
    expect(target.invokeUrl.href).toBe(
      "https://agent-engine.example.com/api/v1/projects/project-1/workspaces/workspace-1/invoke"
    );
    expect(target.streamUrl.href).toBe(
      "https://agent-engine.example.com/api/v1/projects/project-1/workspaces/workspace-1/invokeStream"
    );
    expect(target.reviewsUrl.href).toBe(
      "https://agent-engine.example.com/api/v1/projects/project-1/executions?status=suspended&limit=50&workspace_id=workspace-1"
    );
    expect(target.sessionMessagesUrl("session-1").href).toBe(
      "https://agent-engine.example.com/api/v1/projects/project-1/sessions/session-1/messages?workspace_id=workspace-1"
    );
    expect(target.decisionResumeUrl("execution-1").href).toBe(
      "https://agent-engine.example.com/api/v1/projects/project-1/executions/execution-1/resume?workspace_id=workspace-1"
    );
  });

  it("preserves the workspace selector for a multi-agent local URL", () => {
    const target = getAgentEngineTarget(
      "http://localhost:3000/invoke/stream?workspace=insurance-agent"
    );

    expect(target.mode).toBe("local");
    expect(target.invokeUrl.href).toBe(
      "http://localhost:3000/invoke?workspace=insurance-agent"
    );
    expect(target.reviewsUrl.searchParams.get("workspace")).toBe("insurance-agent");
    expect(target.sessionMessagesUrl("session-1").href).toBe(
      "http://localhost:3000/api/v1/sessions/session-1/messages?workspace=insurance-agent"
    );
    expect(target.decisionResumeUrl("execution-1").searchParams.get("workspace")).toBe(
      "insurance-agent"
    );
  });

  it("derives review URLs without a selector for a single-agent local URL", () => {
    const target = getAgentEngineTarget("http://localhost:3000/invoke/stream");

    expect(target.mode).toBe("local");
    expect(target.invokeUrl.href).toBe("http://localhost:3000/invoke");
    expect(target.streamUrl.href).toBe("http://localhost:3000/invoke/stream");
    expect(target.reviewsUrl.href).toBe(
      "http://localhost:3000/api/v1/executions?status=suspended&limit=50"
    );
    expect(target.sessionMessagesUrl("session-1").href).toBe(
      "http://localhost:3000/api/v1/sessions/session-1/messages"
    );
    expect(target.decisionResumeUrl("execution-1").href).toBe(
      "http://localhost:3000/api/v1/executions/execution-1/resume"
    );
  });

  it("maps a local custom-parser route onto the local review API", () => {
    const target = getAgentEngineTarget(
      "http://127.0.0.1:3000/api/v1/projects/000000000000000000000002/workspaces/parser-agent/invokeStream"
    );

    expect(target.mode).toBe("local");
    expect(target.reviewsUrl.pathname).toBe("/api/v1/executions");
    expect(target.reviewsUrl.searchParams.get("workspace")).toBe("parser-agent");
  });

  it.each([
    undefined,
    "not a url",
    "file:///tmp/invoke",
    "http://agent-engine.example.com/invoke",
    "http://agent-engine.example.com/api/v1/projects/project-1/workspaces/workspace-1/invokeStream",
    "https://agent-engine.example.com/not-an-invoke-route",
    "https://user:password@agent-engine.example.com/invoke",
  ])("rejects unsupported configuration %s", (url) => {
    expect(() => getAgentEngineTarget(url)).toThrow();
  });
});

describe("request validation", () => {
  it("rejects path-rewriting execution IDs and malformed session IDs", () => {
    expect(isSafeExecutionId("execution-1")).toBe(true);
    expect(isSafeExecutionId("../../health")).toBe(false);
    expect(isSafeExecutionId("..%2F..%2Fhealth")).toBe(false);
    expect(isSafeSessionId("session-1_valid")).toBe(true);
    expect(isSafeSessionId("session/1")).toBe(false);
  });

  it("keeps only suspended executions from a valid list response", () => {
    expect(
      normalizeReviews({
        executions: [
          {
            execution_id: "suspended-1",
            status: "suspended",
            session_id: "session-1",
            suspend_reason: "awaiting_review",
            suspend_context: { summary: "Review this" },
            created_at: "2026-09-15T12:00:00Z",
            updated_at: "2026-09-15T12:01:00Z",
            org_id: "internal-org",
            result: { secret: true },
          },
          { execution_id: "complete-1", status: "completed" },
          { status: "suspended" },
        ],
      })
    ).toEqual([
      {
        execution_id: "suspended-1",
        status: "suspended",
        session_id: "session-1",
        suspend_reason: "awaiting_review",
        suspend_context: { summary: "Review this" },
        created_at: "2026-09-15T12:00:00Z",
        updated_at: "2026-09-15T12:01:00Z",
      },
    ]);
  });

  it("normalizes supported user and assistant history messages", () => {
    expect(
      normalizeChatHistory(
        {
          messages: [
            { id: "message-1", role: "human", content: "Hello" },
            {
              role: "ai",
              content: [{ type: "text", text: "Hi there" }],
            },
            { role: "tool", content: "internal result" },
          ],
          latest_status: "suspended",
          history_status: "ready",
        },
        "session-1"
      )
    ).toEqual({
      messages: [
        { id: "message-1", role: "user", content: "Hello" },
        {
          id: "message-session-1-1",
          role: "assistant",
          content: "Hi there",
        },
      ],
      latestStatus: "suspended",
      historyStatus: "ready",
    });
  });
});
