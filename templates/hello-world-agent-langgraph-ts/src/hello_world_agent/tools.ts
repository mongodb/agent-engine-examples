import { z } from "zod";
import type { App } from "@magenta/magenta-sdklanggraph-ts";

function isoDateInTimezone(timezoneName: string): string {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezoneName,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  return formatter.format(new Date());
}

export function registerTools(app: App): void {
  // Tool 1: no human-in-the-loop — runs directly in the AER container.
  app.tool({
    isLocal: true,
    description:
      "Return today's date in ISO 8601 format for an IANA timezone.",
    schema: z.object({
      timezone_name: z
        .string()
        .default("UTC")
        .describe("IANA timezone name, e.g. America/Los_Angeles"),
    }),
  })(function get_current_date({
    timezone_name,
  }: {
    timezone_name: string;
  }): string {
    try {
      return isoDateInTimezone(timezone_name);
    } catch (err) {
      if (err instanceof RangeError) {
        return JSON.stringify({
          error: `Unknown timezone: ${timezone_name}`,
          hint: "Use an IANA timezone like America/Los_Angeles.",
        });
      }
      throw err;
    }
  });

  // Tool 2: human-in-the-loop — suspends the graph until a human resumes it.
  app.tool({
    isLocal: true,
    description:
      "Suspend execution and request a human to review a decision before proceeding. " +
      "Use this when the task requires human judgement.",
    schema: z.object({
      reason: z.string().describe("Why human review is needed"),
      context: z
        .string()
        .describe("Summary of the current situation for the reviewer"),
      preliminary_decision: z
        .string()
        .describe("The agent's preliminary recommendation"),
    }),
  })(function request_human_review({
    reason,
    context: ctx,
    preliminary_decision,
  }: {
    reason: string;
    context: string;
    preliminary_decision: string;
  }): string {
    return app.suspend("awaiting_human_review", {
      reason,
      context: ctx,
      preliminary_decision,
      requested_at: new Date().toISOString(),
    });
  });
}
