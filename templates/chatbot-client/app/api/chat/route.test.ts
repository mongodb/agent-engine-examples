import { afterEach, describe, expect, it, vi } from "vitest";
import { POST } from "@/app/api/chat/route";
import { resetAgenticAuthForTests } from "@/lib/agentic-auth";
import { restoreEnvironmentVariable } from "@/test/environment";

const originalApiUrl = process.env.MONGODB_AGENTIC_API_URL;
const originalClientId = process.env.MONGODB_AGENTIC_CLIENT_ID;
const originalClientSecret = process.env.MONGODB_AGENTIC_CLIENT_SECRET;

afterEach(() => {
  restoreEnvironmentVariable("MONGODB_AGENTIC_API_URL", originalApiUrl);
  restoreEnvironmentVariable("MONGODB_AGENTIC_CLIENT_ID", originalClientId);
  restoreEnvironmentVariable("MONGODB_AGENTIC_CLIENT_SECRET", originalClientSecret);
  resetAgenticAuthForTests();
  vi.unstubAllGlobals();
});

describe("POST /api/chat", () => {
  it("forwards session identity and emits the authoritative response session", async () => {
    process.env.MONGODB_AGENTIC_API_URL =
      "https://agentic.example.com/api/v1/projects/project-1/workspaces/workspace-1/invokeStream";
    process.env.MONGODB_AGENTIC_CLIENT_ID = "agp_sa_id_example";
    process.env.MONGODB_AGENTIC_CLIENT_SECRET = "agp_sa_sk_example";
    const upstream = new Response(
      'data: {"chunk_type":"text","content":"Hello"}\n\n',
      {
        headers: {
          "Content-Type": "text/event-stream",
          "X-Session-ID": "authoritative-session",
        },
      }
    );
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({ access_token: "service-account-token", expires_in: 3_600 })
      )
      .mockResolvedValueOnce(upstream);
    vi.stubGlobal("fetch", fetchMock);

    const response = await POST(
      new Request("http://localhost/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: [{ role: "user", parts: [{ type: "text", text: "Hi" }] }],
          session_id: "client-session",
          thread_id: "client-session",
        }),
      })
    );

    expect(response.status).toBe(200);
    const [, init] = fetchMock.mock.calls[1];
    expect((init.headers as Headers).get("Authorization")).toBe(
      "Bearer service-account-token"
    );
    expect((init.headers as Headers).get("X-Session-ID")).toBe("client-session");
    const responseBody = await response.text();
    expect(responseBody).toContain('"type":"data-session"');
    expect(responseBody).toContain('"session_id":"authoritative-session"');
  });

  it("explains when the agent pauses for human review", async () => {
    process.env.MONGODB_AGENTIC_API_URL = "http://localhost:3000/invoke/stream";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          'data: {"chunk_type":"done","metadata":{"status":"suspended","execution_id":"execution-1"}}\n\n',
          { headers: { "Content-Type": "text/event-stream" } }
        )
      )
    );

    const response = await POST(
      new Request("http://localhost/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: [{ role: "user", parts: [{ type: "text", text: "File a claim" }] }],
          session_id: "session-1",
        }),
      })
    );

    const responseBody = await response.text();
    expect(responseBody).toContain("waiting for human review");
    expect(responseBody).toContain('"type":"data-suspension"');
    expect(responseBody).not.toContain("did not return a response");
  });

  it("rejects malformed configuration before sending a request upstream", async () => {
    process.env.MONGODB_AGENTIC_API_URL = "http://agentic.example.com/invoke/stream";
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await POST(
      new Request("http://localhost/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: [{ role: "user", parts: [{ type: "text", text: "Hi" }] }],
        }),
      })
    );

    expect(response.status).toBe(500);
    await expect(response.text()).resolves.toContain("must use https");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
