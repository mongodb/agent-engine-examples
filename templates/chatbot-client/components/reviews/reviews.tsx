"use client";

import Link from "next/link";
import { RefreshCwIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  REVIEW_POLL_INTERVAL_MS,
  getPendingInterrupts,
  initialResumeMap,
  isRecord,
  parseResumeMap,
  type PendingReview,
  type ResumeReviewRequest,
} from "@/lib/reviews";
import { Button } from "@/components/ui/button";

function allowedDecisions(review: PendingReview): string[] {
  const decisions = review.suspend_context?.allowed_decisions;
  if (!Array.isArray(decisions)) return ["approve", "reject"];
  const valid = decisions.filter(
    (decision): decision is string => typeof decision === "string" && Boolean(decision.trim())
  );
  return valid.length > 0 ? valid : ["approve", "reject"];
}

function contextEntries(review: PendingReview): [string, unknown][] {
  return Object.entries(review.suspend_context ?? {}).filter(
    ([key]) => !["interrupts", "allowed_decisions", "resume_schema"].includes(key)
  );
}

function displayValue(value: unknown): string {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function reviewStateKey(review: PendingReview): string {
  return JSON.stringify([
    review.execution_id,
    review.session_id,
    review.updated_at,
    getPendingInterrupts(review)?.map((interrupt) => interrupt.id),
  ]);
}

async function submitReview(
  executionId: string,
  request: ResumeReviewRequest
): Promise<void> {
  const response = await fetch(
    `/api/reviews/${encodeURIComponent(executionId)}/resume`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    }
  );
  if (response.ok) return;

  const result = await response.json().catch(() => null);
  throw new Error(
    isRecord(result) && typeof result.error === "string"
      ? result.error
      : `Review submission failed with HTTP ${response.status}.`
  );
}

function ReviewCard({
  review,
  onResolved,
}: {
  review: PendingReview;
  onResolved: (review: PendingReview) => void;
}) {
  const interrupts = getPendingInterrupts(review);
  const [decision, setDecision] = useState(allowedDecisions(review)[0]);
  const [notes, setNotes] = useState("");
  const [resumeMap, setResumeMap] = useState(() =>
    interrupts ? initialResumeMap(interrupts) : "{}"
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async () => {
    let request: ResumeReviewRequest;
    if (interrupts) {
      const parsed = parseResumeMap(resumeMap, interrupts);
      if ("error" in parsed) {
        setError(parsed.error);
        return;
      }
      if (!review.session_id) {
        setError("This review has no session ID and cannot be resumed.");
        return;
      }
      request = {
        type: "interrupt",
        session_id: review.session_id,
        resume_map: parsed.resumeMap,
      };
    } else {
      request = {
        type: "decision",
        decision,
        ...(notes ? { reviewer_notes: notes } : {}),
      };
    }

    setSubmitting(true);
    setError(null);
    try {
      await submitReview(review.execution_id, request);
      onResolved(review);
    } catch (submissionError) {
      setError(
        submissionError instanceof Error
          ? submissionError.message
          : "Unable to resume this review."
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <article className="rounded-xl border bg-card p-5 shadow-[var(--shadow-card)]">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold">
            {review.suspend_reason?.replaceAll("_", " ") || "Human review"}
          </h2>
          <p className="mt-1 font-mono text-xs text-muted-foreground">
            {review.execution_id}
          </p>
        </div>
        <span className="rounded-full bg-muted px-2.5 py-1 text-xs font-medium">
          Suspended
        </span>
      </div>

      <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
        {review.session_id ? (
          <div>
            <dt className="text-xs text-muted-foreground">Session</dt>
            <dd className="mt-1 break-all font-mono">{review.session_id}</dd>
          </div>
        ) : null}
        {review.created_at ? (
          <div>
            <dt className="text-xs text-muted-foreground">Submitted</dt>
            <dd className="mt-1">{new Date(review.created_at).toLocaleString()}</dd>
          </div>
        ) : null}
      </dl>

      {contextEntries(review).length > 0 ? (
        <div className="mt-4 rounded-lg bg-muted/50 p-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Context
          </h3>
          <dl className="mt-2 space-y-2 text-sm">
            {contextEntries(review).map(([key, value]) => (
              <div key={key}>
                <dt className="font-medium">{key.replaceAll("_", " ")}</dt>
                <dd className="mt-0.5 whitespace-pre-wrap break-words text-muted-foreground">
                  {displayValue(value)}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}

      <div className="mt-5 space-y-3">
        {interrupts ? (
          <>
            <div className="space-y-1">
              <h3 className="text-sm font-medium">Pending interrupts</h3>
              {interrupts.map((interrupt) => (
                <p className="text-sm text-muted-foreground" key={interrupt.id}>
                  <code>{interrupt.id}</code>: {displayValue(interrupt.value)}
                </p>
              ))}
            </div>
            <label className="block text-sm font-medium" htmlFor={`resume-${review.execution_id}`}>
              Resume map
            </label>
            <textarea
              className="min-h-36 w-full rounded-lg border bg-background p-3 font-mono text-sm"
              id={`resume-${review.execution_id}`}
              onChange={(event) => setResumeMap(event.target.value)}
              spellCheck={false}
              value={resumeMap}
            />
            <p className="text-xs text-muted-foreground">
              Replace each null with an answer matching the response schema
              above. Keep every interrupt ID as a top-level key.
            </p>
          </>
        ) : (
          <>
            <label className="block text-sm font-medium" htmlFor={`decision-${review.execution_id}`}>
              Decision
            </label>
            <select
              className="h-9 w-full rounded-lg border bg-background px-3 text-sm"
              id={`decision-${review.execution_id}`}
              onChange={(event) => setDecision(event.target.value)}
              value={decision}
            >
              {allowedDecisions(review).map((option) => (
                <option key={option} value={option}>
                  {option.replaceAll("_", " ")}
                </option>
              ))}
            </select>
            <label className="block text-sm font-medium" htmlFor={`notes-${review.execution_id}`}>
              Reviewer notes <span className="font-normal text-muted-foreground">(optional)</span>
            </label>
            <textarea
              className="min-h-24 w-full rounded-lg border bg-background p-3 text-sm"
              id={`notes-${review.execution_id}`}
              onChange={(event) => setNotes(event.target.value)}
              value={notes}
            />
          </>
        )}

        {error ? <p className="text-sm text-destructive" role="alert">{error}</p> : null}
        <Button disabled={submitting} onClick={() => void handleSubmit()}>
          {submitting ? "Submitting…" : interrupts ? "Continue execution" : "Submit decision"}
        </Button>
      </div>
    </article>
  );
}

export function Reviews() {
  const [reviews, setReviews] = useState<PendingReview[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [resolvedReview, setResolvedReview] = useState<PendingReview | null>(null);
  const activeRequest = useRef<AbortController | null>(null);

  const loadReviews = useCallback(async (showLoading = false) => {
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    if (showLoading) setLoading(true);
    try {
      const response = await fetch("/api/reviews", {
        cache: "no-store",
        signal: controller.signal,
      });
      const result = await response.json().catch(() => null);
      if (activeRequest.current !== controller) return;
      if (!response.ok) {
        throw new Error(
          isRecord(result) && typeof result.error === "string"
            ? result.error
            : `Unable to load reviews (HTTP ${response.status}).`
        );
      }
      const nextReviews =
        isRecord(result) && Array.isArray(result.reviews)
          ? (result.reviews as PendingReview[])
          : [];
      setReviews(nextReviews);
      setError(null);
    } catch (loadError) {
      if (controller.signal.aborted) return;
      setError(loadError instanceof Error ? loadError.message : "Unable to load reviews.");
    } finally {
      if (activeRequest.current === controller) {
        activeRequest.current = null;
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    const poll = async () => {
      await loadReviews();
      if (!stopped) {
        timer = window.setTimeout(() => void poll(), REVIEW_POLL_INTERVAL_MS);
      }
    };
    void poll();

    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
      const request = activeRequest.current;
      activeRequest.current = null;
      request?.abort();
    };
  }, [loadReviews]);

  const handleResolved = useCallback((review: PendingReview) => {
    const request = activeRequest.current;
    activeRequest.current = null;
    request?.abort();
    setResolvedReview(review);
    setReviews((current) =>
      current.filter((item) => item.execution_id !== review.execution_id)
    );
  }, []);

  return (
    <main className="min-h-dvh bg-background px-4 py-8">
      <div className="mx-auto max-w-4xl">
        <header className="flex flex-wrap items-start justify-between gap-4 border-b pb-5">
          <div>
            <Link className="text-sm text-muted-foreground hover:text-foreground" href="/">
              ← Back to chat
            </Link>
            <h1 className="mt-3 text-2xl font-semibold">Human reviews</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              {reviews.length} suspended execution{reviews.length === 1 ? "" : "s"} awaiting review.
            </p>
          </div>
          <Button
            aria-label="Refresh reviews"
            disabled={loading}
            onClick={() => void loadReviews(true)}
            variant="outline"
          >
            <RefreshCwIcon className={loading ? "animate-spin" : ""} />
            Refresh
          </Button>
        </header>

        {error ? (
          <div className="mt-6 rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive" role="alert">
            {error}
          </div>
        ) : null}

        {resolvedReview ? (
          <div className="mt-6 rounded-lg border bg-muted/40 p-4 text-sm" role="status">
            Review submitted.
            {resolvedReview.session_id ? (
              <>
                {" "}
                <Link
                  className="font-medium text-primary underline"
                  href={`/?session_id=${encodeURIComponent(resolvedReview.session_id)}`}
                >
                  View the resumed conversation
                </Link>
              </>
            ) : null}
          </div>
        ) : null}

        {loading && reviews.length === 0 ? (
          <p className="mt-8 text-sm text-muted-foreground" role="status">Loading reviews…</p>
        ) : null}

        {!loading && !error && reviews.length === 0 ? (
          <div className="mt-8 rounded-xl border border-dashed p-8 text-center">
            <h2 className="font-medium">No pending reviews</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Suspended executions for this workspace will appear here.
            </p>
          </div>
        ) : null}

        <div className="mt-6 space-y-4">
          {reviews.map((review) => (
            <ReviewCard
              key={reviewStateKey(review)}
              onResolved={handleResolved}
              review={review}
            />
          ))}
        </div>
      </div>
    </main>
  );
}
