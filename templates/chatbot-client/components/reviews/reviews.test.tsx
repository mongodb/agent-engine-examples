import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Reviews } from "@/components/reviews/reviews";

const decisionReview = {
  execution_id: "execution-decision",
  status: "suspended",
  session_id: "session-1",
  suspend_reason: "awaiting_human_review",
  suspend_context: {
    summary: "Approve the refund",
    allowed_decisions: ["approve", "reject"],
  },
  created_at: "2026-09-15T12:00:00Z",
};

const interruptReview = {
  execution_id: "execution-interrupt",
  status: "suspended",
  session_id: "session-2",
  suspend_reason: "interrupt",
  suspend_context: {
    interrupts: [{ id: "region", value: "Which region?" }],
  },
};

const claimInterruptReview = {
  execution_id: "execution-claim",
  status: "suspended",
  session_id: "session-claim",
  suspend_reason: "agent_interrupt",
  suspend_context: {
    interrupts: [
      {
        id: "claim-review-1",
        value: {
          message: "Please review the claim and approve or deny it.",
          response_schema: {
            type: "object",
            required: ["decision"],
            properties: {
              decision: {
                type: "string",
                title: "Decision",
                enum: ["approved", "denied"],
              },
              reviewer_notes: { type: "string", title: "Reviewer notes" },
            },
            additionalProperties: false,
          },
        },
      },
    ],
  },
};

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Reviews", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ reviews: [], count: 0 })));
  });

  afterEach(() => vi.unstubAllGlobals());

  it("shows the empty state after loading", async () => {
    render(<Reviews />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading reviews");
    expect(await screen.findByRole("heading", { name: "No pending reviews" })).toBeVisible();
  });

  it("shows a load error and supports manual refresh", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ error: "Not authorized" }, 403))
      .mockResolvedValueOnce(response({ reviews: [], count: 0 }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<Reviews />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Not authorized");
    await user.click(screen.getByRole("button", { name: "Refresh reviews" }));
    expect(await screen.findByRole("heading", { name: "No pending reviews" })).toBeVisible();
  });

  it("submits a decision and removes the resolved review", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ reviews: [decisionReview], count: 1 }))
      .mockResolvedValueOnce(response({ success: true }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<Reviews />);
    const card = (await screen.findByText("Approve the refund")).closest("article")!;
    await user.selectOptions(within(card).getByLabelText("Decision"), "reject");
    await user.type(within(card).getByLabelText(/Reviewer notes/), "Needs correction");
    await user.click(within(card).getByRole("button", { name: "Submit decision" }));

    await waitFor(() => expect(screen.queryByText("Approve the refund")).not.toBeInTheDocument());
    expect(
      screen.getByRole("link", { name: "View the resumed conversation" })
    ).toHaveAttribute("href", "/?session_id=session-1");
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      type: "decision",
      decision: "reject",
      reviewer_notes: "Needs correction",
    });
  });

  it("validates and submits a native interrupt resume map", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ reviews: [interruptReview], count: 1 }))
      .mockResolvedValueOnce(response({ success: true }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<Reviews />);
    const editor = await screen.findByLabelText("Resume map");
    await user.clear(editor);
    await user.type(editor, "not json");
    await user.click(screen.getByRole("button", { name: "Continue execution" }));
    expect(screen.getByRole("alert")).toHaveTextContent("valid JSON");

    fireEvent.change(editor, { target: { value: JSON.stringify({ region: "us-east-1" }) } });
    await user.click(screen.getByRole("button", { name: "Continue execution" }));

    await waitFor(() => expect(screen.queryByText("Which region?")).not.toBeInTheDocument());
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      type: "interrupt",
      session_id: "session-2",
      resume_map: { region: "us-east-1" },
    });
  });

  it("seeds and validates a structured claim review answer", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ reviews: [claimInterruptReview], count: 1 }))
      .mockResolvedValueOnce(response({ success: true }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<Reviews />);
    const editor = await screen.findByLabelText("Resume map");
    expect(editor).toHaveValue(
      JSON.stringify(
        { "claim-review-1": { decision: null } },
        null,
        2
      )
    );

    fireEvent.change(editor, {
      target: {
        value: JSON.stringify({ "claim-review-1": { decision: "approve" } }),
      },
    });
    await user.click(screen.getByRole("button", { name: "Continue execution" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      'Decision must be one of: "approved", "denied".'
    );
    expect(fetchMock).toHaveBeenCalledOnce();

    fireEvent.change(editor, {
      target: {
        value: JSON.stringify({ "claim-review-1": { decision: "approved" } }),
      },
    });
    await user.click(screen.getByRole("button", { name: "Continue execution" }));

    await waitFor(() =>
      expect(screen.queryByText("execution-claim")).not.toBeInTheDocument()
    );
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      type: "interrupt",
      session_id: "session-claim",
      resume_map: { "claim-review-1": { decision: "approved" } },
    });
  });

  it("replaces a stale resume-map draft after the suspension changes", async () => {
    const firstReview = {
      ...interruptReview,
      updated_at: "2026-09-15T12:00:00Z",
      suspend_context: {
        interrupts: [{ id: "first-question", value: "First question" }],
      },
    };
    const updatedReview = {
      ...interruptReview,
      updated_at: "2026-09-15T12:01:00Z",
      suspend_context: {
        interrupts: [{ id: "second-question", value: "Second question" }],
      },
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ reviews: [firstReview], count: 1 }))
      .mockResolvedValueOnce(response({ reviews: [updatedReview], count: 1 }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<Reviews />);
    const editor = await screen.findByLabelText("Resume map");
    fireEvent.change(editor, { target: { value: '{"first-question":"draft"}' } });
    await user.click(screen.getByRole("button", { name: "Refresh reviews" }));

    await waitFor(() =>
      expect(screen.getByLabelText("Resume map")).toHaveValue(
        JSON.stringify({ "second-question": null }, null, 2)
      )
    );
  });

  it("shows the submission state and keeps a review when submission fails", async () => {
    let resolveSubmission!: (response: Response) => void;
    const submission = new Promise<Response>((resolve) => {
      resolveSubmission = resolve;
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ reviews: [decisionReview], count: 1 }))
      .mockReturnValueOnce(submission);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<Reviews />);
    const card = (await screen.findByText("Approve the refund")).closest("article")!;
    await user.click(within(card).getByRole("button", { name: "Submit decision" }));

    expect(within(card).getByRole("button", { name: "Submitting…" })).toBeDisabled();
    resolveSubmission(response({ error: "Resume rejected" }, 409));

    expect(await within(card).findByRole("alert")).toHaveTextContent("Resume rejected");
    expect(screen.getByText("Approve the refund")).toBeVisible();
  });
});
