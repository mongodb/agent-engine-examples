import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  getAgentEngineHeaders,
  resetAgentEngineAuthForTests,
} from "@/lib/agent-engine-auth";
import { getAgentEngineTarget } from "@/lib/agent-engine-api";
import { restoreEnvironmentVariable } from "@/test/environment";

const originalClientId = process.env.MONGODB_AGENTIC_CLIENT_ID;
const originalClientSecret = process.env.MONGODB_AGENTIC_CLIENT_SECRET;
const deployedTarget = getAgentEngineTarget(
  "https://agent-engine.example.com/api/v1/projects/project-1/workspaces/workspace-1/invokeStream"
);
const localTarget = getAgentEngineTarget("http://localhost:3000/invoke/stream");

beforeEach(() => {
  delete process.env.MONGODB_AGENTIC_CLIENT_ID;
  delete process.env.MONGODB_AGENTIC_CLIENT_SECRET;
  resetAgentEngineAuthForTests();
});

afterEach(() => {
  restoreEnvironmentVariable("MONGODB_AGENTIC_CLIENT_ID", originalClientId);
  restoreEnvironmentVariable("MONGODB_AGENTIC_CLIENT_SECRET", originalClientSecret);
  resetAgentEngineAuthForTests();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function configureServiceAccount() {
  process.env.MONGODB_AGENTIC_CLIENT_ID = "agp_sa_id_example";
  process.env.MONGODB_AGENTIC_CLIENT_SECRET = "agp_sa_sk_example";
}

describe("getAgentEngineHeaders", () => {
  it("does not require authentication for a local target", async () => {
    const headers = await getAgentEngineHeaders(localTarget, "session-1");

    expect(headers.get("Authorization")).toBeNull();
    expect(headers.get("X-Session-ID")).toBe("session-1");
  });

  it("requires complete service account credentials for a deployed target", async () => {
    process.env.MONGODB_AGENTIC_CLIENT_ID = "agp_sa_id_example";

    await expect(getAgentEngineHeaders(deployedTarget)).rejects.toThrow(
      "MONGODB_AGENTIC_CLIENT_ID and MONGODB_AGENTIC_CLIENT_SECRET are required"
    );
  });

  it("mints and reuses a service account access token", async () => {
    configureServiceAccount();
    const fetchMock = vi.fn().mockResolvedValue(
      Response.json({
        access_token: "access-token-1",
        token_type: "Bearer",
        expires_in: 3_600,
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const firstHeaders = await getAgentEngineHeaders(deployedTarget, "session-1");
    const secondHeaders = await getAgentEngineHeaders(deployedTarget);

    expect(firstHeaders.get("Authorization")).toBe("Bearer access-token-1");
    expect(firstHeaders.get("X-Session-ID")).toBe("session-1");
    expect(secondHeaders.get("Authorization")).toBe("Bearer access-token-1");
    expect(fetchMock).toHaveBeenCalledOnce();

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("https://agent-engine.example.com/api/v1/oauth/token");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe(
      `Basic ${Buffer.from("agp_sa_id_example:agp_sa_sk_example").toString("base64")}`
    );
    expect(init.headers["Content-Type"]).toBe("application/x-www-form-urlencoded");
    expect(String(init.body)).toBe("grant_type=client_credentials");
  });

  it("shares one token request across concurrent callers", async () => {
    configureServiceAccount();
    let resolveToken!: (response: Response) => void;
    const tokenResponse = new Promise<Response>((resolve) => {
      resolveToken = resolve;
    });
    const fetchMock = vi.fn().mockReturnValue(tokenResponse);
    vi.stubGlobal("fetch", fetchMock);

    const firstHeaders = getAgentEngineHeaders(deployedTarget);
    const secondHeaders = getAgentEngineHeaders(deployedTarget);

    expect(fetchMock).toHaveBeenCalledOnce();
    resolveToken(
      Response.json({ access_token: "shared-access-token", expires_in: 3_600 })
    );

    const [first, second] = await Promise.all([firstHeaders, secondHeaders]);
    expect(first.get("Authorization")).toBe("Bearer shared-access-token");
    expect(second.get("Authorization")).toBe("Bearer shared-access-token");
  });

  it("refreshes a cached token before it expires", async () => {
    configureServiceAccount();
    let currentTime = 1_000;
    vi.spyOn(Date, "now").mockImplementation(() => currentTime);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({ access_token: "access-token-1", expires_in: 100 })
      )
      .mockResolvedValueOnce(
        Response.json({ access_token: "access-token-2", expires_in: 100 })
      );
    vi.stubGlobal("fetch", fetchMock);

    const firstHeaders = await getAgentEngineHeaders(deployedTarget);
    currentTime = 91_001;
    const refreshedHeaders = await getAgentEngineHeaders(deployedTarget);

    expect(firstHeaders.get("Authorization")).toBe("Bearer access-token-1");
    expect(refreshedHeaders.get("Authorization")).toBe("Bearer access-token-2");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("rejects an unsuccessful token exchange without exposing its body", async () => {
    configureServiceAccount();
    const response = Response.json({ error: "secret details" }, { status: 401 });
    const cancel = vi.spyOn(response.body!, "cancel");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(response)
    );

    await expect(getAgentEngineHeaders(deployedTarget)).rejects.toThrow(
      "Service account token request failed with HTTP 401."
    );
    expect(cancel).toHaveBeenCalledOnce();
  });

  it.each([
    ["invalid JSON", new Response("not JSON")],
    ["a missing access token", Response.json({ expires_in: 3_600 })],
    [
      "an empty access token",
      Response.json({ access_token: "", expires_in: 3_600 }),
    ],
    [
      "a missing expiry",
      Response.json({ access_token: "access-token-1" }),
    ],
    [
      "a non-numeric expiry",
      Response.json({ access_token: "access-token-1", expires_in: "3600" }),
    ],
    [
      "a non-finite expiry",
      new Response('{"access_token":"access-token-1","expires_in":1e400}'),
    ],
    [
      "a non-positive expiry",
      Response.json({ access_token: "access-token-1", expires_in: 0 }),
    ],
  ])("rejects a token response with %s", async (_description, response) => {
    configureServiceAccount();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

    await expect(getAgentEngineHeaders(deployedTarget)).rejects.toThrow(
      "Service account token response is invalid."
    );
  });
});
