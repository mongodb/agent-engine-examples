import { z } from "zod";
import type { App } from "@mongodb-js/agent-engine-sdk-langgraph";

function isoDateInTimezone(timezoneName: string): string {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezoneName,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  return formatter.format(new Date());
}

/** Coerce a `build_context` result's `formatted_context` (string | array) to text. */
function contextToText(formatted: unknown): string {
  if (typeof formatted === "string") return formatted;
  if (Array.isArray(formatted) && formatted.length > 0) {
    return JSON.stringify(formatted);
  }
  return "";
}

/**
 * Log the full error server-side and return a generic, user-safe message.
 * Memory failures must not surface raw driver or transport details, and an
 * enabled-memory outage must not look like disabled or empty memory.
 */
function safeErrorMessage(context: string, err: unknown): string {
  console.error(`[hello-world-agent] ${context}:`, err);
  return context;
}

/**
 * Register the hello-world tools on the app.
 *
 * All tools are remote-capable: they resolve the current user from the ambient
 * execution context and run in the tool sandbox (see `sandboxes` in agent.yaml).
 */
export function registerTools(app: App): void {
  app.tool({
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

  app.tool({
    description:
      "Save a piece of information about the user to long-term memory.",
    schema: z.object({
      info_key: z
        .string()
        .describe(
          "What the information represents, such as name, location, or interests.",
        ),
      info_value: z.string().describe("The value to store."),
    }),
  })(async function save_user_info({
    info_key,
    info_value,
  }: {
    info_key: string;
    info_value: string;
  }): Promise<string> {
    const userId = app.getCurrentUserId() ?? "";
    try {
      const result = await app.memory.saveSemantic({
        text: `User ${info_key}: ${info_value}`,
        label: `${userId}_${info_key}`,
        source: "hello_world_agent",
        metadata: { type: "user_info", info_key },
      });
      return result.acknowledged
        ? JSON.stringify({ status: "saved", info_key })
        : JSON.stringify({ status: "error", error: "Memory is not enabled" });
    } catch (err) {
      return JSON.stringify({
        status: "error",
        error: safeErrorMessage("Failed to save user information", err),
      });
    }
  });

  app.tool({
    description:
      "Recall stored information about the current user from long-term memory.",
    schema: z.object({}),
  })(async function recall_user_info(): Promise<string> {
    const userId = app.getCurrentUserId() ?? "";
    try {
      const context = await app.memory.buildContext({
        query: `user profile and information for user ${userId}`,
      });
      const profile = contextToText(context.formatted_context);
      if (!profile) {
        return JSON.stringify({
          found: false,
          message: "No stored information found",
        });
      }
      return JSON.stringify({ found: true, profile });
    } catch (err) {
      return JSON.stringify({
        found: false,
        message: safeErrorMessage("Failed to recall user information", err),
      });
    }
  });
}
