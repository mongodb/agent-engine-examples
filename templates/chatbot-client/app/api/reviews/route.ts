import { getAgenticHeaders } from "@/lib/agentic-auth";
import {
  getAgenticTarget,
  isLocalReviewApiMissing,
  normalizeReviews,
  readUpstreamError,
  LOCAL_REVIEW_API_UNAVAILABLE_MESSAGE,
} from "@/lib/agentic-api";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const target = getAgenticTarget(process.env.MONGODB_AGENTIC_API_URL);
    const response = await fetch(target.reviewsUrl, {
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

    const reviews = normalizeReviews(await response.json());
    return Response.json({ reviews, count: reviews.length });
  } catch (error) {
    return Response.json(
      { error: error instanceof Error ? error.message : "Unable to load reviews." },
      { status: 500 }
    );
  }
}
