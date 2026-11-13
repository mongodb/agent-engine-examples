import "server-only";

import type { PendingReview } from "@/lib/reviews";
import type { ChatHistory, ChatHistoryMessage } from "@/lib/session";
import { isSafeSessionId } from "@/lib/session";

export { isSafeSessionId } from "@/lib/session";

const DEPLOYED_INVOKE_PATH =
  /^\/api\/v1\/projects\/([^/]+)\/workspaces\/([^/]+)\/(invoke|invokeStream)\/?$/;
const LOCAL_INVOKE_PATH = /^\/invoke(?:\/stream)?\/?$/;

export type AgentEngineTarget = {
  mode: "deployed" | "local";
  invokeUrl: URL;
  streamUrl: URL;
  reviewsUrl: URL;
  sessionMessagesUrl: (sessionId: string) => URL;
  decisionResumeUrl: (executionId: string) => URL;
};

function isLoopback(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]";
}

function decodePathSegment(value: string, label: string): string {
  try {
    const decoded = decodeURIComponent(value);
    if (!decoded || decoded.includes("/")) {
      throw new Error();
    }
    return decoded;
  } catch {
    throw new Error(`MONGODB_AGENTIC_API_URL has an invalid ${label}.`);
  }
}

function synchronousInvokeUrl(configuredUrl: URL): URL {
  const url = new URL(configuredUrl);
  if (url.pathname.endsWith("/invokeStream")) {
    url.pathname = url.pathname.replace(/\/invokeStream\/?$/, "/invoke");
  } else if (url.pathname.endsWith("/invoke/stream")) {
    url.pathname = url.pathname.replace(/\/invoke\/stream\/?$/, "/invoke");
  }
  return url;
}

function streamingInvokeUrl(configuredUrl: URL): URL {
  const url = new URL(configuredUrl);
  if (url.pathname.match(DEPLOYED_INVOKE_PATH)?.[3] === "invoke") {
    url.pathname = url.pathname.replace(/\/invoke\/?$/, "/invokeStream");
  } else if (url.pathname.endsWith("/invoke")) {
    url.pathname = url.pathname.replace(/\/invoke\/?$/, "/invoke/stream");
  }
  return url;
}

export function getAgentEngineTarget(rawUrl: string | undefined): AgentEngineTarget {
  if (!rawUrl?.trim()) {
    throw new Error("MONGODB_AGENTIC_API_URL is required.");
  }

  let configuredUrl: URL;
  try {
    configuredUrl = new URL(rawUrl);
  } catch {
    throw new Error("MONGODB_AGENTIC_API_URL must be an absolute URL.");
  }

  if (!["http:", "https:"].includes(configuredUrl.protocol)) {
    throw new Error("MONGODB_AGENTIC_API_URL must use http or https.");
  }
  if (configuredUrl.username || configuredUrl.password || configuredUrl.hash) {
    throw new Error("MONGODB_AGENTIC_API_URL must not contain credentials or a fragment.");
  }

  const loopback = isLoopback(configuredUrl.hostname);
  if (configuredUrl.protocol !== "https:" && !loopback) {
    throw new Error(
      "MONGODB_AGENTIC_API_URL must use https unless it targets a loopback host."
    );
  }

  const canonicalMatch = configuredUrl.pathname.match(DEPLOYED_INVOKE_PATH);
  const isLocalPath = LOCAL_INVOKE_PATH.test(configuredUrl.pathname);
  if (!canonicalMatch && !isLocalPath) {
    throw new Error(
      "MONGODB_AGENTIC_API_URL must end in /invoke, /invoke/stream, or a project workspace invoke endpoint."
    );
  }

  const invokeUrl = synchronousInvokeUrl(configuredUrl);
  const streamUrl = streamingInvokeUrl(configuredUrl);
  const canonicalProject = canonicalMatch
    ? decodePathSegment(canonicalMatch[1], "project ID")
    : undefined;
  const canonicalWorkspace = canonicalMatch
    ? decodePathSegment(canonicalMatch[2], "workspace ID")
    : undefined;
  const local = isLocalPath || loopback;

  if (local) {
    const workspace = configuredUrl.searchParams.get("workspace") || canonicalWorkspace;
    const reviewsUrl = new URL("/api/v1/executions", configuredUrl.origin);
    reviewsUrl.searchParams.set("status", "suspended");
    reviewsUrl.searchParams.set("limit", "50");
    if (workspace) reviewsUrl.searchParams.set("workspace", workspace);

    return {
      mode: "local",
      invokeUrl,
      streamUrl,
      reviewsUrl,
      sessionMessagesUrl: (sessionId) => {
        const url = new URL(
          `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`,
          configuredUrl.origin
        );
        if (workspace) url.searchParams.set("workspace", workspace);
        return url;
      },
      decisionResumeUrl: (executionId) => {
        const url = new URL(
          `/api/v1/executions/${encodeURIComponent(executionId)}/resume`,
          configuredUrl.origin
        );
        if (workspace) url.searchParams.set("workspace", workspace);
        return url;
      },
    };
  }

  if (!canonicalProject || !canonicalWorkspace) {
    throw new Error("Deployed URLs must include project and workspace IDs.");
  }

  const reviewsUrl = new URL(
    `/api/v1/projects/${encodeURIComponent(canonicalProject)}/executions`,
    configuredUrl.origin
  );
  reviewsUrl.searchParams.set("status", "suspended");
  reviewsUrl.searchParams.set("limit", "50");
  reviewsUrl.searchParams.set("workspace_id", canonicalWorkspace);

  return {
    mode: "deployed",
    invokeUrl,
    streamUrl,
    reviewsUrl,
    sessionMessagesUrl: (sessionId) => {
      const url = new URL(
        `/api/v1/projects/${encodeURIComponent(canonicalProject)}/sessions/${encodeURIComponent(sessionId)}/messages`,
        configuredUrl.origin
      );
      url.searchParams.set("workspace_id", canonicalWorkspace);
      return url;
    },
    decisionResumeUrl: (executionId) => {
      const url = new URL(
        `/api/v1/projects/${encodeURIComponent(canonicalProject)}/executions/${encodeURIComponent(executionId)}/resume`,
        configuredUrl.origin
      );
      url.searchParams.set("workspace_id", canonicalWorkspace);
      return url;
    },
  };
}

export function isSafeExecutionId(executionId: string): boolean {
  return (
    executionId.length > 0 &&
    executionId.length <= 256 &&
    executionId !== "." &&
    executionId !== ".." &&
    !/[/?#\\%]/.test(executionId)
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function normalizeMessageContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";

  return content
    .flatMap((part) => {
      if (typeof part === "string") return [part];
      if (isRecord(part) && typeof part.text === "string") return [part.text];
      return [];
    })
    .join("\n");
}

export function normalizeChatHistory(
  value: unknown,
  sessionId: string
): ChatHistory {
  if (!isRecord(value)) {
    return { messages: [], latestStatus: null, historyStatus: "unsupported" };
  }

  const candidates = Array.isArray(value.messages) ? value.messages : [];
  const messages = candidates.flatMap((candidate, index): ChatHistoryMessage[] => {
    if (!isRecord(candidate)) return [];

    const rawRole =
      typeof candidate.role === "string"
        ? candidate.role.toLowerCase()
        : typeof candidate.type === "string"
          ? candidate.type.toLowerCase()
          : "";
    const role =
      rawRole === "human" || rawRole === "user"
        ? "user"
        : rawRole === "ai" || rawRole === "assistant"
          ? "assistant"
          : null;
    const content = normalizeMessageContent(candidate.content);
    if (!role || !content.trim()) return [];

    return [
      {
        id:
          typeof candidate.id === "string" && candidate.id
            ? candidate.id
            : `message-${sessionId}-${index}`,
        role,
        content,
      },
    ];
  });

  return {
    messages,
    latestStatus:
      typeof value.latest_status === "string" ? value.latest_status : null,
    historyStatus:
      value.history_status === "ready" || value.history_status === "warming"
        ? value.history_status
        : "unsupported",
  };
}

export function normalizeReviews(value: unknown): PendingReview[] {
  if (!isRecord(value) || !Array.isArray(value.executions)) return [];

  return value.executions.flatMap((candidate): PendingReview[] => {
    if (
      !isRecord(candidate) ||
      typeof candidate.execution_id !== "string" ||
      !isSafeExecutionId(candidate.execution_id) ||
      candidate.status !== "suspended"
    ) {
      return [];
    }

    const review: PendingReview = {
      execution_id: candidate.execution_id,
      status: "suspended",
    };
    if (typeof candidate.session_id === "string" && isSafeSessionId(candidate.session_id)) {
      review.session_id = candidate.session_id;
    }
    if (typeof candidate.suspend_reason === "string") {
      review.suspend_reason = candidate.suspend_reason;
    }
    if (isRecord(candidate.suspend_context)) {
      review.suspend_context = candidate.suspend_context;
    }
    if (typeof candidate.created_at === "string") {
      review.created_at = candidate.created_at;
    }
    if (typeof candidate.updated_at === "string") {
      review.updated_at = candidate.updated_at;
    }
    return [review];
  });
}

export async function readUpstreamErrorDetails(
  response: Response
): Promise<{ message: string; code?: string }> {
  const fallback = `Agent Engine API request failed with HTTP ${response.status}.`;
  const body = await response.json().catch(() => null);
  if (!isRecord(body)) return { message: fallback };
  for (const key of ["error", "message"]) {
    if (typeof body[key] === "string" && body[key].trim()) {
      return {
        message: body[key].trim(),
        ...(typeof body.code === "string" ? { code: body.code } : {}),
      };
    }
  }
  return { message: fallback };
}

export async function readUpstreamError(response: Response): Promise<string> {
  return (await readUpstreamErrorDetails(response)).message;
}

export const LOCAL_REVIEW_API_UNAVAILABLE_MESSAGE =
  "This local URL does not serve the review API. Point MONGODB_AGENTIC_API_URL at the playground UI port printed by `agentengine dev up`, not the orchestration engine port.";

/**
 * Locally the review endpoints come from the `agentengine dev up` playground UI, while the
 * orchestration engine serves only the invoke endpoints. The CLI prints the orchestration
 * engine port whenever the playground is suppressed, which makes chat work and every
 * review call 404 against an origin that otherwise looks correct.
 */
export function isLocalReviewApiMissing(
  target: AgentEngineTarget,
  response: Response
): boolean {
  return target.mode === "local" && response.status === 404;
}
