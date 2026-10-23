import "server-only";

import type { AgentEngineTarget } from "@/lib/agent-engine-api";

const TOKEN_REFRESH_SKEW_MS = 60_000;
const TOKEN_REQUEST_TIMEOUT_MS = 10_000;

type CachedToken = {
  accessToken: string;
  clientId: string;
  origin: string;
  refreshAt: number;
};

type PendingToken = {
  clientId: string;
  origin: string;
  promise: Promise<CachedToken>;
};

let cachedToken: CachedToken | null = null;
let pendingToken: PendingToken | null = null;

function configuredServiceAccount(): { clientId: string; clientSecret: string } {
  const clientId = process.env.MONGODB_AGENTIC_CLIENT_ID?.trim();
  const clientSecret = process.env.MONGODB_AGENTIC_CLIENT_SECRET?.trim();

  if (!clientId || !clientSecret) {
    throw new Error(
      "MONGODB_AGENTIC_CLIENT_ID and MONGODB_AGENTIC_CLIENT_SECRET are required for deployed URLs."
    );
  }
  if (clientId.includes(":")) {
    throw new Error("MONGODB_AGENTIC_CLIENT_ID must not contain a colon.");
  }
  return { clientId, clientSecret };
}

async function mintServiceAccountToken(
  origin: string,
  clientId: string,
  clientSecret: string
): Promise<CachedToken> {
  const tokenUrl = new URL("/api/v1/oauth/token", origin);
  const authorization = Buffer.from(`${clientId}:${clientSecret}`, "utf8").toString(
    "base64"
  );
  const response = await fetch(tokenUrl, {
    method: "POST",
    headers: {
      Authorization: `Basic ${authorization}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: new URLSearchParams({ grant_type: "client_credentials" }),
    cache: "no-store",
    signal: AbortSignal.timeout(TOKEN_REQUEST_TIMEOUT_MS),
  });

  if (!response.ok) {
    await response.body?.cancel().catch(() => undefined);
    throw new Error(
      `Service account token request failed with HTTP ${response.status}.`
    );
  }

  const body: unknown = await response.json().catch(() => null);
  if (
    body === null ||
    typeof body !== "object" ||
    !("access_token" in body) ||
    typeof body.access_token !== "string" ||
    !body.access_token ||
    !("expires_in" in body) ||
    typeof body.expires_in !== "number" ||
    !Number.isFinite(body.expires_in) ||
    body.expires_in <= 0
  ) {
    throw new Error("Service account token response is invalid.");
  }

  const lifetimeMs = body.expires_in * 1_000;
  const refreshSkewMs = Math.min(TOKEN_REFRESH_SKEW_MS, lifetimeMs / 10);
  return {
    accessToken: body.access_token,
    clientId,
    origin,
    refreshAt: Date.now() + lifetimeMs - refreshSkewMs,
  };
}

async function getServiceAccountToken(
  target: AgentEngineTarget,
  clientId: string,
  clientSecret: string
): Promise<string> {
  const origin = target.invokeUrl.origin;
  if (
    cachedToken?.clientId === clientId &&
    cachedToken.origin === origin &&
    Date.now() < cachedToken.refreshAt
  ) {
    return cachedToken.accessToken;
  }

  if (pendingToken?.clientId === clientId && pendingToken.origin === origin) {
    return (await pendingToken.promise).accessToken;
  }

  const promise = mintServiceAccountToken(origin, clientId, clientSecret);
  pendingToken = { clientId, origin, promise };
  try {
    cachedToken = await promise;
    return cachedToken.accessToken;
  } finally {
    if (pendingToken?.promise === promise) pendingToken = null;
  }
}

export async function getAgentEngineHeaders(
  target: AgentEngineTarget,
  sessionId?: string
): Promise<Headers> {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (target.mode === "deployed") {
    const credentials = configuredServiceAccount();
    const token = await getServiceAccountToken(target, credentials.clientId, credentials.clientSecret);
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (sessionId) headers.set("X-Session-ID", sessionId);
  return headers;
}

export function resetAgentEngineAuthForTests() {
  cachedToken = null;
  pendingToken = null;
}
