import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "@/app/api/reviews/route";
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

describe("GET /api/reviews", () => {
  it("lists suspended executions through the configured workspace", async () => {
    process.env.MONGODB_AGENTIC_API_URL =
      "https://agent-engine.example.com/api/v1/projects/project-1/workspaces/workspace-1/invokeStream";
    process.env.MONGODB_AGENTIC_CLIENT_ID = "agp_sa_id_example";
    process.env.MONGODB_AGENTIC_CLIENT_SECRET = "agp_sa_sk_example";
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({ access_token: "service-account-token", expires_in: 3_600 })
      )
      .mockResolvedValueOnce(
        Response.json({
          executions: [{ execution_id: "execution-1", status: "suspended" }],
        })
      );
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET();

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      reviews: [{ execution_id: "execution-1", status: "suspended" }],
      count: 1,
    });
    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toContain("workspace_id=workspace-1");
    expect((init.headers as Headers).get("Authorization")).toBe(
      "Bearer service-account-token"
    );
  });

  it("returns an upstream error without logging its response body", async () => {
    process.env.MONGODB_AGENTIC_API_URL = "http://localhost:3000/invoke/stream";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(Response.json({ error: "Access denied" }, { status: 403 }))
    );
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);

    const response = await GET();

    expect(response.status).toBe(403);
    await expect(response.json()).resolves.toEqual({ error: "Access denied" });
    expect(consoleSpy).not.toHaveBeenCalled();
  });

  it("explains that a local 404 means the URL is not the playground UI port", async () => {
    process.env.MONGODB_AGENTIC_API_URL = "http://localhost:8080/invoke/stream";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("404 page not found", { status: 404 }))
    );

    const response = await GET();

    expect(response.status).toBe(404);
    await expect(response.json()).resolves.toEqual({
      error: LOCAL_REVIEW_API_UNAVAILABLE_MESSAGE,
    });
  });

  it("keeps the upstream error for a deployed 404", async () => {
    process.env.MONGODB_AGENTIC_API_URL =
      "https://agent-engine.example.com/api/v1/projects/project-1/workspaces/workspace-1/invokeStream";
    process.env.MONGODB_AGENTIC_CLIENT_ID = "agp_sa_id_example";
    process.env.MONGODB_AGENTIC_CLIENT_SECRET = "agp_sa_sk_example";
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          Response.json({ access_token: "service-account-token", expires_in: 3_600 })
        )
        .mockResolvedValueOnce(Response.json({ error: "Project not found" }, { status: 404 }))
    );

    const response = await GET();

    expect(response.status).toBe(404);
    await expect(response.json()).resolves.toEqual({ error: "Project not found" });
  });
});
