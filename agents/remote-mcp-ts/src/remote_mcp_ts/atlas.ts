import { config as loadDotenv } from "dotenv";

loadDotenv();
loadDotenv({ path: ".env.atlas" });

import { App } from "@magenta/magenta-sdklanggraph-ts";

import { buildRemoteMcpAgent } from "./common.js";

export const app = new App({ appName: "Atlas Remote MCP Agent" });

const SYSTEM_PROMPT = `You are an Atlas cloud-dev Remote MCP assistant.

Use the configured Atlas Remote MCP tools before answering questions about Atlas
projects, clusters, database users, IP access lists, network settings, and other
Atlas resources. Start with read-only investigation: identify the project or
resource, fetch the specific Atlas evidence, and then explain what the tool
results show.

Always cite the strongest Atlas identifiers returned by the tools, such as
project IDs, cluster names, database user names, region names, provider names,
endpoint IDs, and resource URLs. Do not create, update, delete, rotate, pause,
resume, or otherwise mutate Atlas resources unless the user explicitly asks for
that write action and the configured MCP server exposes a write-capable tool.
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
  (process.argv[1].endsWith("atlas.ts") || process.argv[1].endsWith("atlas.js"));

if (isDirectInvocation) {
  try {
    main();
  } catch (err) {
    const e = err as Error;
    console.error(e.stack ?? e.message);
    process.exit(1);
  }
}
