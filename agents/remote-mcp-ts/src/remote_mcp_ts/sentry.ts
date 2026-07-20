import "dotenv/config";

import { App } from "@magenta/magenta-sdklanggraph-ts";

import { buildRemoteMcpAgent } from "./common.js";

export const app = new App({ appName: "Sentry Remote MCP Agent" });

const SYSTEM_PROMPT = `You are a Sentry incident and debugging assistant.

Use the configured Sentry remote MCP tools before answering questions about
organizations, projects, issues, events, traces, releases, or alerts. Start with
read-only investigation: identify the relevant organization and project, search
or fetch the specific issue or event, and then explain what the evidence shows.

Always cite the strongest Sentry identifiers or links returned by the tools,
such as organization slugs, project slugs, issue IDs, event IDs, release names,
trace IDs, and Sentry URLs. Do not assign, update, resolve, ignore, create, or
delete anything unless the user explicitly asks for that write action and the
configured MCP server exposes a write-capable tool.
`;

export const buildAgent = app.entrypoint(() =>
  buildRemoteMcpAgent(app, SYSTEM_PROMPT),
);

export function main(): void {
  app.run();
}

const isDirectInvocation =
  typeof process !== "undefined" &&
  process.argv[1] &&
  (process.argv[1].endsWith("sentry.ts") || process.argv[1].endsWith("sentry.js"));

if (isDirectInvocation) {
  try {
    main();
  } catch (err) {
    const e = err as Error;
    console.error(e.stack ?? e.message);
    process.exit(1);
  }
}
