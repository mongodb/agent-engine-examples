import {
  createUIMessageStream,
  createUIMessageStreamResponse,
} from "ai";
import { generateUUID } from "@/lib/utils";
import { getAgenticHeaders } from "@/lib/agentic-auth";
import { getAgenticTarget, isSafeSessionId } from "@/lib/agentic-api";
import { SUSPENDED_CHAT_RESPONSE } from "@/lib/session";

export const maxDuration = 60;

type AgenticStreamEvent = {
  suspended: boolean;
  text?: string;
};

function parseAgenticStreamEvent(raw: string): AgenticStreamEvent | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const payload = trimmed.startsWith("data: ") ? trimmed.slice(6) : trimmed;

  try {
    const event: unknown = JSON.parse(payload);
    if (event === null || typeof event !== "object" || Array.isArray(event)) {
      return null;
    }
    const chunk = event as Record<string, unknown>;
    const metadata =
      chunk.metadata !== null &&
      typeof chunk.metadata === "object" &&
      !Array.isArray(chunk.metadata)
        ? (chunk.metadata as Record<string, unknown>)
        : null;
    return {
      suspended:
        chunk.chunk_type === "done" && metadata?.status === "suspended",
      ...(chunk.chunk_type === "text" &&
      typeof chunk.content === "string" &&
      chunk.content.length > 0
        ? { text: chunk.content }
        : {}),
    };
  } catch {
    return null;
  }
}

function streamErrorMessage(text: string) {
  const stream = createUIMessageStream({
    execute: async ({ writer }) => {
      const partId = generateUUID();
      writer.write({ type: "text-start", id: partId });
      writer.write({ type: "text-delta", delta: text, id: partId });
      writer.write({ type: "text-end", id: partId });
    },
  });
  return createUIMessageStreamResponse({ stream });
}

async function fetchWithRetry(
  url: string | URL,
  init: RequestInit,
  { retries = 2, baseDelay = 500 } = {}
): Promise<Response> {
  for (let attempt = 0; ; attempt++) {
    try {
      const response = await fetch(url, init);
      if (response.ok || response.status < 500 || attempt >= retries)
        return response;
    } catch (error) {
      if (attempt >= retries) throw error;
    }
    await new Promise((r) => setTimeout(r, baseDelay * 2 ** attempt));
  }
}

export async function POST(request: Request) {
  let body: Record<string, unknown>;

  try {
    body = await request.json();
  } catch {
    return new Response("Invalid request body", { status: 400 });
  }

  const messages = body.messages as
    | { role: string; parts: { type: string; text?: string }[] }[]
    | undefined;

  const lastUserMessage = messages
    ?.filter((m) => m.role === "user")
    .at(-1);

  const userText =
    lastUserMessage?.parts
      ?.filter((p) => p.type === "text")
      .map((p) => p.text)
      .join("") ?? "";

  if (!userText.trim()) {
    return new Response("No message provided", { status: 400 });
  }

  const userId = typeof body.user_id === "string" ? body.user_id : undefined;
  const sessionId =
    typeof body.session_id === "string" && isSafeSessionId(body.session_id)
      ? body.session_id
      : undefined;
  const threadId =
    typeof body.thread_id === "string" && isSafeSessionId(body.thread_id)
      ? body.thread_id
      : undefined;

  let streamUrl: URL;
  let headers: Headers;
  try {
    const target = getAgenticTarget(process.env.MONGODB_AGENTIC_API_URL);
    streamUrl = target.streamUrl;
    headers = await getAgenticHeaders(target, sessionId);
  } catch (error) {
    return new Response(
      error instanceof Error ? error.message : "Invalid Agentic API configuration.",
      { status: 500 }
    );
  }

  let mongoResponse: Response;
  try {
    mongoResponse = await fetchWithRetry(streamUrl, {
      method: "POST",
      headers,
      body: JSON.stringify({
        message: userText,
        ...(userId && { user_id: userId }),
        ...(sessionId && { session_id: sessionId }),
        ...(threadId && { thread_id: threadId }),
      }),
    });
  } catch (error) {
    console.error("MongoDB Agentic API network error:", error);
    return streamErrorMessage(
      "Sorry, we couldn't reach the server. Please check your connection and try again."
    );
  }

  if (!mongoResponse.ok || !mongoResponse.body) {
    console.error("MongoDB Agentic API error:", mongoResponse.status);
    return streamErrorMessage(
      "Unfortunately, something went wrong while processing your request. Please try again."
    );
  }

  const stream = createUIMessageStream({
    execute: async ({ writer }) => {
      const responseSessionId = mongoResponse.headers.get("X-Session-ID")?.trim();
      if (responseSessionId && isSafeSessionId(responseSessionId)) {
        writer.write({
          type: "data-session",
          data: { session_id: responseSessionId },
          transient: true,
        });
      }

      const reader = mongoResponse.body!.getReader();
      const decoder = new TextDecoder();

      const partId = generateUUID();
      writer.write({ type: "text-start", id: partId });

      let hasContent = false;
      let suspended = false;
      let buffer = "";

      const writeEvent = (raw: string) => {
        const event = parseAgenticStreamEvent(raw);
        if (!event) return;
        if (event.text) {
          writer.write({ type: "text-delta", delta: event.text, id: partId });
          hasContent = true;
        }
        if (event.suspended) suspended = true;
      };

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split("\n");
          buffer = lines.pop()!;

          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed || !trimmed.startsWith("data: ")) continue;
            writeEvent(trimmed);
          }
        }
      } catch (error) {
        console.error("[Stream read error]", error);
        writer.write({
          type: "text-delta",
          delta: "\n\nSorry, the connection was interrupted. Please try again.",
          id: partId,
        });
        hasContent = true;
      }

      if (buffer.trim()) writeEvent(buffer);

      if (suspended) {
        writer.write({
          type: "text-delta",
          delta: `${hasContent ? "\n\n" : ""}${SUSPENDED_CHAT_RESPONSE}`,
          id: partId,
        });
      } else if (!hasContent) {
        writer.write({
          type: "text-delta",
          delta: "The agent did not return a response. Please try again.",
          id: partId,
        });
      }

      writer.write({ type: "text-end", id: partId });
      if (suspended) {
        writer.write({ type: "data-suspension", data: {} });
      }
    },
    onError: (error) => {
      console.error("[Stream error]", error);
      return "An error occurred while processing your request.";
    },
  });

  return createUIMessageStreamResponse({ stream });
}
