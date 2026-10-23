import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "@/app/api/chat/history/route";
import { LOCAL_REVIEW_API_UNAVAILABLE_MESSAGE } from "@/lib/agent-engine-api";
import { resetAgentEngineAuthForTests } from "@/lib/agent-engine-auth";
import { restoreEnvironmentVariable } from "@/test/environment";

const originalApiUrl = process.env.MONGODB_AGENTIC_API_URL;
const originalClientId = process.env.MONGODB_AGENTIC_CLIENT_ID;
const originalClientSecret = process.env.MONGODB_AGENTIC_CLIENT_SECRET;

afterEach(() => {
  restoreEnvironmentVariable("MONGODB_AGENTIC_API_URL", originalApiUrl);
  restoreEnvironmentVariable("MONGODB_AGENTIC_CLIENT_ID", originalClientId);
  restoreEnvironmentVariable("MONGODB_AGENTIC_CLIENT_SECRET", originalClientSecret);
  resetAgentEngineAuthForTests();
  vi.unstubAllGlobals();
});

describe("GET /api/chat/history", () => {
  it("loads and normalizes the configured workspace session", async () => {
    process.env.MONGODB_AGENTIC_API_URL =
      "http://localhost:3000/invoke/stream?workspace=insurance-agent";
    const fetchMock = vi.fn().mockResolvedValue(
      Response.json({
        messages: [
          { id: "message-1", role: "human", content: "Hello" },
          { id: "message-2", role: "ai", content: "Hi there" },
        ],
        latest_status: "completed",
        history_status: "ready",
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(
      new Request("http://app.local/api/chat/history?session_id=session-1")
    );

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      messages: [
        { id: "message-1", role: "user", content: "Hello" },
        { id: "message-2", role: "assistant", content: "Hi there" },
      ],
      latestStatus: "completed",
      historyStatus: "ready",
    });
    expect(String(fetchMock.mock.calls[0][0])).toBe(
      "http://localhost:3000/api/v1/sessions/session-1/messages?workspace=insurance-agent"
    );
  });

  it("rejects unsafe session IDs before making an upstream request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(
      new Request("http://app.local/api/chat/history?session_id=..%2Fhealth")
    );

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("explains that a local 404 means the URL is not the playground UI port", async () => {
    process.env.MONGODB_AGENTIC_API_URL = "http://localhost:8080/invoke/stream";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("404 page not found", { status: 404 }))
    );

    const response = await GET(
      new Request("http://app.local/api/chat/history?session_id=session-1")
    );

    expect(response.status).toBe(404);
    await expect(response.json()).resolves.toEqual({
      error: LOCAL_REVIEW_API_UNAVAILABLE_MESSAGE,
    });
  });
});
