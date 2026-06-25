import {
  createUIMessageStream,
  createUIMessageStreamResponse,
} from "ai";
import { generateUUID } from "@/lib/utils";

export const maxDuration = 60;

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
  url: string,
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
    typeof body.session_id === "string" ? body.session_id : undefined;
  const threadId =
    typeof body.thread_id === "string" ? body.thread_id : undefined;

  let mongoResponse: Response;
  try {
    mongoResponse = await fetchWithRetry(process.env.MONGODB_AGENTIC_API_URL!, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${process.env.MONGODB_AGENTIC_API_KEY}`,
        "X-Project-ID": process.env.MONGODB_AGENTIC_PROJECT_ID!,
        "X-Workspace-ID": process.env.MONGODB_AGENTIC_WORKSPACE_ID!,
        "Content-Type": "application/json",
      },
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
    console.error(
      "MongoDB Agentic API error:",
      mongoResponse.status,
      await mongoResponse.text().catch(() => "")
    );
    return streamErrorMessage(
      "Unfortunately, something went wrong while processing your request. Please try again."
    );
  }

  const stream = createUIMessageStream({
    execute: async ({ writer }) => {
      const reader = mongoResponse.body!.getReader();
      const decoder = new TextDecoder();

      const partId = generateUUID();
      writer.write({ type: "text-start", id: partId });

      let hasContent = false;
      let buffer = "";

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

            try {
              const json = JSON.parse(trimmed.slice(6));
              if (json.chunk_type !== "text") continue;
              if (typeof json.content === "string" && json.content.length > 0) {
                writer.write({ type: "text-delta", delta: json.content, id: partId });
                hasContent = true;
              }
            } catch {
              // skip unparseable lines
            }
          }
        }
      } catch (error) {
        console.error("[Stream read error]", error);
        writer.write({
          type: "text-delta",
          delta: "\n\nSorry, the connection was interrupted. Please try again.",
          id: partId,
        });
      }

      if (buffer.trim()) {
        try {
          const payload = buffer.trim().startsWith("data: ")
            ? buffer.trim().slice(6)
            : buffer.trim();
          const json = JSON.parse(payload);
          if (json.chunk_type === "text" && typeof json.content === "string" && json.content.length > 0) {
            writer.write({ type: "text-delta", delta: json.content, id: partId });
            hasContent = true;
          }
        } catch {
          // skip
        }
      }

      if (!hasContent) {
        writer.write({
          type: "text-delta",
          delta: "The agent did not return a response. Please try again.",
          id: partId,
        });
      }

      writer.write({ type: "text-end", id: partId });
    },
    onError: (error) => {
      console.error("[Stream error]", error);
      return "An error occurred while processing your request.";
    },
  });

  return createUIMessageStreamResponse({ stream });
}
