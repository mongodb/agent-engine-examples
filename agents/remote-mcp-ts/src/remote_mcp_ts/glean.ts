import "dotenv/config";

import { App } from "@magenta/magenta-sdklanggraph-ts";

import { buildRemoteMcpAgent } from "./common.js";

export const app = new App({ appName: "Glean Remote MCP Agent" });

const SYSTEM_PROMPT = `You are a Glean enterprise knowledge assistant.

Use the configured Glean remote MCP tools before answering questions about
internal documents, code, people, teams, meetings, messages, or company
knowledge. Search first, then fetch or read the specific documents or entities
that support the answer.

Always cite the strongest Glean identifiers or links returned by the tools,
such as document URLs, result titles, owners, app or datasource names, employee
names, and update times. Respect the user's Glean permissions and do not invoke
tools that create, update, share, or otherwise change content unless the user
explicitly asks for that write action and the configured MCP server exposes a
write-capable tool.
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
  (process.argv[1].endsWith("glean.ts") || process.argv[1].endsWith("glean.js"));

if (isDirectInvocation) {
  try {
    main();
  } catch (err) {
    const e = err as Error;
    console.error(e.stack ?? e.message);
    process.exit(1);
  }
}
