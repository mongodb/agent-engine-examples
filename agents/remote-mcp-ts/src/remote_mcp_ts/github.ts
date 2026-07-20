import "dotenv/config";

import { App } from "@magenta/magenta-sdklanggraph-ts";

import { buildRemoteMcpAgent } from "./common.js";

export const app = new App({ appName: "GitHub Remote MCP Agent" });

const SYSTEM_PROMPT = `You are a GitHub repository triage assistant.

Use the configured GitHub remote MCP tools before answering questions about
repositories, files, issues, pull requests, or GitHub Actions runs. Prefer
read-only investigation: search first, then fetch the specific issue, pull
request, file, or workflow run that supports the answer.

Always cite the strongest GitHub identifiers or links returned by the tools,
such as repository names, issue numbers, pull request numbers, workflow run IDs,
file paths, and commit SHAs. Do not create, update, comment on, close, merge, or
rerun anything unless the user explicitly asks for that write action and the
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
  (process.argv[1].endsWith("github.ts") || process.argv[1].endsWith("github.js"));

if (isDirectInvocation) {
  try {
    main();
  } catch (err) {
    const e = err as Error;
    console.error(e.stack ?? e.message);
    process.exit(1);
  }
}
