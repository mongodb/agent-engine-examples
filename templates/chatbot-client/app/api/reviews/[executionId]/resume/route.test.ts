import { afterEach, describe, expect, it, vi } from "vitest";
import { POST } from "@/app/api/reviews/[executionId]/resume/route";
import { LOCAL_REVIEW_API_UNAVAILABLE_MESSAGE } from "@/lib/agentic-api";
import { resetAgenticAuthForTests } from "@/lib/agentic-auth";
import { restoreEnvironmentVariable } from "@/test/environment";

const originalApiUrl = process.env.MONGODB_AGENTIC_API_URL;
const originalClientId = process.env.MONGODB_AGENTIC_CLIENT_ID;
const originalClientSecret = process.env.MONGODB_AGENTIC_CLIENT_SECRET;

function request(body: unknown): Request {
  return new Request("http://localhost/api/reviews/execution-1/resume", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

afterEach(() => {
  restoreEnvironmentVariable("MONGODB_AGENTIC_API_URL", originalApiUrl);
  restoreEnvironmentVariable("MONGODB_AGENTIC_CLIENT_ID", originalClientId);
  restoreEnvironmentVariable("MONGODB_AGENTIC_CLIENT_SECRET", originalClientSecret);
  resetAgenticAuthForTests();
  vi.unstubAllGlobals();
});

describe("POST /api/reviews/[executionId]/resume", () => {
  it("uses the execution endpoint for decision resumes", async () => {
    process.env.MONGODB_AGENTIC_API_URL =
      "https://agentic.example.com/api/v1/projects/project-1/workspaces/workspace-1/invokeStream";
    process.env.MONGODB_AGENTIC_CLIENT_ID = "agp_sa_id_example";
    process.env.MONGODB_AGENTIC_CLIENT_SECRET = "agp_sa_sk_example";
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({ access_token: "service-account-token", expires_in: 3_600 })
      )
      .mockResolvedValueOnce(Response.json({ success: true }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await POST(
      request({ type: "decision", decision: "approve", reviewer_notes: "Reviewed" }),
      { params: Promise.resolve({ executionId: "execution-1" }) }
    );

    expect(response.status).toBe(200);
    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toBe(
      "https://agentic.example.com/api/v1/projects/project-1/executions/execution-1/resume?workspace_id=workspace-1"
    );
    expect(JSON.parse(init.body)).toEqual({
      decision: "approve",
      reviewer_notes: "Reviewed",
    });
    expect((init.headers as Headers).get("Authorization")).toBe(
      "Bearer service-account-token"
    );
  });

  it("uses synchronous invoke and session identity for native interrupts", async () => {
    process.env.MONGODB_AGENTIC_API_URL =
      "http://localhost:3000/invoke/stream?workspace=insurance-agent";
    const fetchMock = vi.fn().mockResolvedValue(Response.json({ success: true }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await POST(
      request({
        type: "interrupt",
        session_id: "session-1",
        resume_map: { region: "us-east-1" },
      }),
      { params: Promise.resolve({ executionId: "execution-1" }) }
    );

    expect(response.status).toBe(200);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("http://localhost:3000/invoke?workspace=insurance-agent");
    expect((init.headers as Headers).get("X-Session-ID")).toBe("session-1");
    expect(JSON.parse(init.body)).toEqual({
      session_id: "session-1",
      resume_map: { region: "us-east-1" },
    });
  });

  it.each([
    [{ type: "interrupt", session_id: "session/1", resume_map: { answer: true } }],
    [{ type: "interrupt", session_id: "session-1", resume_map: {} }],
    [{ type: "interrupt", session_id: "session-1", resume_map: [] }],
    [{ type: "decision", decision: "" }],
  ])("rejects an invalid resume body", async (body) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await POST(request(body), {
      params: Promise.resolve({ executionId: "execution-1" }),
    });

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects an execution ID that can rewrite the upstream path", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await POST(
      request({ type: "decision", decision: "approve" }),
      { params: Promise.resolve({ executionId: "../../health" }) }
    );

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns the upstream error status and message", async () => {
    process.env.MONGODB_AGENTIC_API_URL = "http://localhost:3000/invoke/stream";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json({ error: "Resume rejected" }, { status: 409 })
      )
    );

    const response = await POST(
      request({ type: "decision", decision: "approve" }),
      { params: Promise.resolve({ executionId: "execution-1" }) }
    );

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toEqual({ error: "Resume rejected" });
  });

  it("makes a rejected interrupt answer actionable", async () => {
    process.env.MONGODB_AGENTIC_API_URL = "http://localhost:3000/invoke/stream";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json(
          {
            error: "continue input cannot be applied to this session",
            code: "RESUME_INPUT_INVALID",
          },
          { status: 409 }
        )
      )
    );

    const response = await POST(
      request({
        type: "interrupt",
        session_id: "session-1",
        resume_map: { "interrupt-1": { decision: "approved" } },
      }),
      { params: Promise.resolve({ executionId: "execution-1" }) }
    );

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toEqual({
      error:
        "The platform rejected this continuation. Refresh reviews to confirm the execution is still suspended, then verify the interrupt IDs and response shape.",
    });
  });

  it("keeps the upstream error when a local interrupt resume 404s", async () => {
    process.env.MONGODB_AGENTIC_API_URL = "http://localhost:8080/invoke/stream";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(Response.json({ error: "Session not found" }, { status: 404 }))
    );

    const response = await POST(
      request({
        type: "interrupt",
        session_id: "session-1",
        resume_map: { "interrupt-1": { decision: "approved" } },
      }),
      { params: Promise.resolve({ executionId: "execution-1" }) }
    );

    expect(response.status).toBe(404);
    await expect(response.json()).resolves.toEqual({ error: "Session not found" });
  });

  it("explains that a local decision 404 means the URL is not the playground UI port", async () => {
    process.env.MONGODB_AGENTIC_API_URL = "http://localhost:8080/invoke/stream";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("404 page not found", { status: 404 }))
    );

    const response = await POST(
      request({ type: "decision", decision: "approve" }),
      { params: Promise.resolve({ executionId: "execution-1" }) }
    );

    expect(response.status).toBe(404);
    await expect(response.json()).resolves.toEqual({
      error: LOCAL_REVIEW_API_UNAVAILABLE_MESSAGE,
    });
  });

  it("returns a server error when the upstream request fails", async () => {
    process.env.MONGODB_AGENTIC_API_URL = "http://localhost:3000/invoke/stream";
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Network unavailable")));

    const response = await POST(
      request({ type: "decision", decision: "approve" }),
      { params: Promise.resolve({ executionId: "execution-1" }) }
    );

    expect(response.status).toBe(500);
    await expect(response.json()).resolves.toEqual({ error: "Network unavailable" });
  });
});
