/**
 * Hello-world Magenta template (TypeScript).
 *
 *  - LangGraph agent with a single agent ↔ tools loop
 *  - SystemMessage prefixed to every turn
 *  - Tools: get_current_date (no HITL), request_human_review (HITL)
 *  - Runtime LLM selection via agent.yaml hints + env API keys
 */

import "dotenv/config";

import { BaseMessage, SystemMessage } from "@langchain/core/messages";
import { BaseChatModel } from "@langchain/core/language_models/chat_models";
import { END, START, StateGraph } from "@langchain/langgraph";
import { ToolNode } from "@langchain/langgraph/prebuilt";

import { App } from "@magenta/magenta-sdklanggraph-ts";

import { buildLLM } from "./llm.js";
import { HelloWorldState, HelloWorldStateAnnotation } from "./state.js";
import { SYSTEM_PROMPT } from "./systemMessage.js";
import { registerTools } from "./tools.js";

function log(level: "INFO" | "WARN" | "ERROR", msg: string): void {
  const now = new Date();
  const hh = String(now.getHours()).padStart(2, "0");
  const mm = String(now.getMinutes()).padStart(2, "0");
  const ss = String(now.getSeconds()).padStart(2, "0");
  console.log(`${hh}:${mm}:${ss} | ${level.padEnd(8)} | ${msg}`);
}

export const app = new App({
  appName: "hello-world-agent",
});

registerTools(app);

export const buildAgent = app.entrypoint(() => {
  log("INFO", "Building hello-world-agent graph");
  const llmConfig = app.llmConfig;
  const runtimeLLM = app.llm(
    buildLLM({
      provider: llmConfig?.provider,
      model: llmConfig?.model,
      temperature: 0,
    }),
  );
  const tools = [...app.getTools()];
  const llmWithTools = (
    runtimeLLM as BaseChatModel & {
      bindTools: (tools: unknown[]) => BaseChatModel;
    }
  ).bindTools([...app.getToolSchemas()]);

  const agentNode = async (
    state: HelloWorldState,
  ): Promise<Partial<HelloWorldState>> => {
    const messages = state.messages ?? [];
    const head = messages[0];
    const tail = head instanceof SystemMessage ? messages.slice(1) : messages;
    const promptMessages: BaseMessage[] = [
      new SystemMessage(SYSTEM_PROMPT),
      ...tail,
    ];
    const response = await llmWithTools.invoke(promptMessages);
    return { messages: [response as BaseMessage] };
  };

  const shouldContinue = (state: HelloWorldState): "tools" | typeof END => {
    const last = state.messages[state.messages.length - 1] as
      | (BaseMessage & { tool_calls?: unknown[] })
      | undefined;
    if (last && Array.isArray(last.tool_calls) && last.tool_calls.length > 0) {
      return "tools";
    }
    return END;
  };

  const builder = new StateGraph(HelloWorldStateAnnotation)
    .addNode("agent", agentNode)
    .addNode("tools", new ToolNode(tools))
    .addEdge(START, "agent")
    .addConditionalEdges("agent", shouldContinue, {
      tools: "tools",
      [END]: END,
    })
    .addEdge("tools", "agent");

  return builder.compile({ checkpointer: app.checkpointer() as never });
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
