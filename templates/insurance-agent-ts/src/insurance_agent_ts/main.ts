/**
 * Insurance agent (TypeScript) built on @mongodb-js/agent-engine-sdk-langgraph.
 *
 * Demonstrates two platform capabilities together:
 *
 *  - **Deep agent** — the graph is a deepagents orchestrator (`app.deepAgent`),
 *    giving built-in planning (todos), a sandboxed filesystem/shell,
 *    and subagent delegation. Requires `features.deep_agent: true`. A
 *    `claims-risk-analyst` subagent handles claim risk assessment.
 *
 *  - **Long-term memory** — tools read/write the Memory Server through
 *    `app.memory` (semantic customer facts, episodic conversation summaries,
 *    taxonomic knowledge base). Requires `features.memory: true`. Identity is
 *    resolved from the ambient execution context, so memory persists across
 *    turns and sessions for the same user.
 */

import "dotenv/config";

import { App } from "@mongodb-js/agent-engine-sdk-langgraph";

import { buildLLM } from "./llm.js";
import { createStores } from "./policyStore.js";
import { SYSTEM_PROMPT } from "./systemMessage.js";
import { registerTools } from "./tools.js";

function log(level: "INFO" | "WARN" | "ERROR", msg: string): void {
  const t = new Date().toISOString().slice(11, 19);
  console.log(`${t} | ${level.padEnd(8)} | ${msg}`);
}

const MONGODB_URI = process.env.MONGODB_URI ?? "";
const MONGODB_DATABASE = process.env.MONGODB_DATABASE ?? "insurance_agent_ts";

export const app = new App({ appName: "insurance-agent-ts" });

const { policyStore, claimStore } = createStores(MONGODB_URI, MONGODB_DATABASE);
registerTools(app, policyStore, claimStore);
log("INFO", "App created and tools registered");

const RISK_ANALYST_TOOLS = new Set([
  "analyze_claim_risk",
  "check_claim_status",
  "lookup_policy",
]);

const RISK_ANALYST_PROMPT = `You are a claims risk analyst. Given a filed claim and its policy, use
analyze_claim_risk to produce a risk_assessment (low/medium/high) with a
recommendation, looking up the policy or claim status when you need more detail.
Return a concise structured summary of the assessment and the recommended
approval path. Do not resolve or notify — that is the main agent's job.`;

export const buildAgent = app.entrypoint(() => {
  log("INFO", "Building insurance-agent-ts deep agent");

  // `app.getTools()` returns tools typed against the SDK's copy of
  // @langchain/core. We only need `.name` here; keep them loosely typed and
  // cross the SDK boundary with `as never` so a second @langchain/core in the
  // agent's own node_modules (common with --local-sdk mounts) can't trigger a
  // nominal type clash. The SDK uses the same escape hatch internally.
  const allTools = app.getTools() as readonly { name: string }[];
  const riskAnalystTools = allTools.filter((t) => RISK_ANALYST_TOOLS.has(t.name));

  // Subagent model MUST be a wrapped instance (app.llm) — a string model would
  // bypass OE routing and fail subagent validation. Registered under a distinct
  // llm id before app.deepAgent wraps the orchestrator's own model.
  const riskAnalyst = {
    name: "claims-risk-analyst",
    description:
      "Assesses a filed insurance claim for risk and returns a structured " +
      "recommendation (low/medium/high, auto_approve/review_recommended/manual_review_required).",
    systemPrompt: RISK_ANALYST_PROMPT,
    model: app.llm(
      buildLLM({ temperature: 0 }) as never,
      "claims-risk-analyst",
    ),
    tools: riskAnalystTools as never,
  };

  // app.checkpointer() returns a saver already guarded against empty write
  // batches (the upstream @langchain/langgraph-checkpoint-mongodb `bulkWrite([])`
  // rejection) by the SDK itself (AP-1970); `null` in tool mode.
  const saver = app.checkpointer();

  // Orchestrator model is passed raw — app.deepAgent wraps it internally.
  // Returned as `unknown`: deepAgent's inferred type references deepagents/
  // langchain internal module paths that TypeScript can't name portably
  // (TS2742). The runner only invokes this entrypoint; it doesn't consume the
  // compiled-graph type, so erasing it here is safe.
  return app.deepAgent(
    buildLLM({ temperature: 0 }) as never,
    {
      tools: allTools as never,
      systemPrompt: SYSTEM_PROMPT,
      subagents: [riskAnalyst] as never,
      checkpointer: (saver ?? false) as never,
    },
  ) as unknown;
});

export function main(): void {
  app.run();
}

const isDirectInvocation =
  typeof process !== "undefined" &&
  process.argv[1] &&
  (process.argv[1].endsWith("main.ts") || process.argv[1].endsWith("main.js"));

if (isDirectInvocation) {
  try {
    main();
  } catch (err) {
    const e = err as Error;
    log("ERROR", e.stack ?? e.message);
    process.exit(1);
  }
}
