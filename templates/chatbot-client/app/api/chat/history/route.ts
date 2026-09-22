import { getAgenticHeaders } from "@/lib/agentic-auth";
import {
  getAgenticTarget,
  isLocalReviewApiMissing,
  isSafeSessionId,
  normalizeChatHistory,
  readUpstreamError,
  LOCAL_REVIEW_API_UNAVAILABLE_MESSAGE,
} from "@/lib/agentic-api";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const sessionId = new URL(request.url).searchParams.get("session_id")?.trim();
  if (!sessionId || !isSafeSessionId(sessionId)) {
    return Response.json({ error: "A valid session_id is required." }, { status: 400 });
  }

  try {
    const target = getAgenticTarget(process.env.MONGODB_AGENTIC_API_URL);
    const response = await fetch(target.sessionMessagesUrl(sessionId), {
      headers: await getAgenticHeaders(target),
      cache: "no-store",
    });

    if (!response.ok) {
      return Response.json(
        {
          error: isLocalReviewApiMissing(target, response)
            ? LOCAL_REVIEW_API_UNAVAILABLE_MESSAGE
            : await readUpstreamError(response),
        },
        { status: response.status }
      );
    }

    return Response.json(normalizeChatHistory(await response.json(), sessionId));
  } catch (error) {
    return Response.json(
      {
        error:
          error instanceof Error ? error.message : "Unable to load conversation history.",
      },
      { status: 500 }
    );
  }
}
