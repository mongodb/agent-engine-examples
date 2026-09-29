import { getAgentEngineHeaders } from "@/lib/agent-engine-auth";
import {
  getAgentEngineTarget,
  isLocalReviewApiMissing,
  isSafeExecutionId,
  isSafeSessionId,
  readUpstreamErrorDetails,
  LOCAL_REVIEW_API_UNAVAILABLE_MESSAGE,
} from "@/lib/agent-engine-api";
import { isRecord, type ResumeReviewRequest } from "@/lib/reviews";

const MAX_REVIEWER_NOTES_LENGTH = 4_000;
const RESUME_INPUT_INVALID_CODE = "RESUME_INPUT_INVALID";

function actionableResumeError(message: string, code?: string): string {
  return code === RESUME_INPUT_INVALID_CODE
    ? "The platform rejected this continuation. Refresh reviews to confirm the execution is still suspended, then verify the interrupt IDs and response shape."
    : message;
}

function parseResumeRequest(value: unknown): ResumeReviewRequest | null {
  if (!isRecord(value)) return null;

  if (value.type === "decision") {
    const notes = value.reviewer_notes;
    if (
      typeof value.decision !== "string" ||
      !value.decision.trim() ||
      (notes !== undefined && typeof notes !== "string") ||
      (typeof notes === "string" && notes.length > MAX_REVIEWER_NOTES_LENGTH)
    ) {
      return null;
    }
    return {
      type: "decision",
      decision: value.decision.trim(),
      ...(typeof notes === "string" && notes ? { reviewer_notes: notes } : {}),
    };
  }

  if (
    value.type === "interrupt" &&
    typeof value.session_id === "string" &&
    isSafeSessionId(value.session_id) &&
    isRecord(value.resume_map) &&
    Object.keys(value.resume_map).length > 0
  ) {
    return {
      type: "interrupt",
      session_id: value.session_id,
      resume_map: value.resume_map,
    };
  }

  return null;
}

export async function POST(
  request: Request,
  { params }: { params: Promise<{ executionId: string }> }
) {
  const { executionId } = await params;
  if (!isSafeExecutionId(executionId)) {
    return Response.json({ error: "Invalid execution ID." }, { status: 400 });
  }

  const body = parseResumeRequest(await request.json().catch(() => null));
  if (!body) {
    return Response.json({ error: "Invalid resume request." }, { status: 400 });
  }

  try {
    const target = getAgentEngineTarget(process.env.MONGODB_AGENTIC_API_URL);
    const upstreamUrl =
      body.type === "decision"
        ? target.decisionResumeUrl(executionId)
        : target.invokeUrl;
    const upstreamBody =
      body.type === "decision"
        ? {
            decision: body.decision,
            ...(body.reviewer_notes ? { reviewer_notes: body.reviewer_notes } : {}),
          }
        : {
            session_id: body.session_id,
            resume_map: body.resume_map,
          };
    const response = await fetch(upstreamUrl, {
      method: "POST",
      headers: await getAgentEngineHeaders(
        target,
        body.type === "interrupt" ? body.session_id : undefined
      ),
      body: JSON.stringify(upstreamBody),
    });

    if (!response.ok) {
      // Only the decision branch calls a review endpoint; interrupts go to invoke,
      // which the orchestration engine serves either way.
      if (body.type === "decision" && isLocalReviewApiMissing(target, response)) {
        return Response.json(
          { error: LOCAL_REVIEW_API_UNAVAILABLE_MESSAGE },
          { status: response.status }
        );
      }
      const upstreamError = await readUpstreamErrorDetails(response);
      return Response.json(
        { error: actionableResumeError(upstreamError.message, upstreamError.code) },
        { status: response.status }
      );
    }

    const result = await response.json().catch(() => ({}));
    return Response.json({ success: true, result });
  } catch (error) {
    return Response.json(
      { error: error instanceof Error ? error.message : "Unable to resume review." },
      { status: 500 }
    );
  }
}
