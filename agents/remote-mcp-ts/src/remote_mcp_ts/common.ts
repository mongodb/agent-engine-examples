/**
 * Shared agent-building logic for the remote-mcp-ts example agents.
 *
 * Port of remote-mcp's common.py. The agents do not define custom tools —
 * their tool surface comes entirely from the `mcp.servers` block in
 * agent.yaml, discovered by the TypeScript SDK at startup.
 */

import { BaseChatModel } from "@langchain/core/language_models/chat_models";
import { BaseMessage, SystemMessage } from "@langchain/core/messages";
import { Annotation, END, START, StateGraph } from "@langchain/langgraph";
import { ToolNode } from "@langchain/langgraph/prebuilt";
import { ChatAnthropic } from "@langchain/anthropic";
import { ChatGoogleGenerativeAI } from "@langchain/google-genai";
import { ChatOpenAI } from "@langchain/openai";

import type { App } from "@magenta/magenta-sdklanggraph-ts";

export const AgentStateAnnotation = Annotation.Root({
  messages: Annotation<BaseMessage[]>({
    reducer: (left, right) => left.concat(right),
    default: () => [],
  }),
});
export type AgentState = typeof AgentStateAnnotation.State;

/** Build a small agent whose tool surface comes from remote MCP config. */
export function buildRemoteMcpAgent(
  app: App,
  systemPrompt: string,
  llm?: BaseChatModel,
) {
  const model = llm ?? createChatModel(app);
  // Local SDK dev can install a second @langchain/core copy under /opt/sdk-local;
  // cast at the SDK boundary so tsc does not mix incompatible type graphs.
  const wrappedLlm = app.llm(model as never);
  const llmWithTools = (
    wrappedLlm as unknown as BaseChatModel & {
      bindTools: (tools: unknown[]) => BaseChatModel;
    }
  ).bindTools([...app.getToolSchemas()]);
  const tools = [...app.getTools()];

  const agentNode = async (
    state: AgentState,
  ): Promise<Partial<AgentState>> => {
    const messages = state.messages ?? [];
    const head = messages[0];
    const promptMessages: BaseMessage[] =
      head instanceof SystemMessage
        ? messages
        : [new SystemMessage(systemPrompt), ...messages];
    const response = await llmWithTools.invoke(promptMessages);
    return { messages: [response as BaseMessage] };
  };

  const shouldContinue = (state: AgentState): "tools" | typeof END => {
    const last = state.messages[state.messages.length - 1] as
      | (BaseMessage & { tool_calls?: unknown[] })
      | undefined;
    if (last && Array.isArray(last.tool_calls) && last.tool_calls.length > 0) {
      return "tools";
    }
    return END;
  };

  const builder = new StateGraph(AgentStateAnnotation)
    .addNode("agent", agentNode)
    .addNode("tools", new ToolNode(tools as never))
    .addEdge(START, "agent")
    .addConditionalEdges("agent", shouldContinue, {
      tools: "tools",
      [END]: END,
    })
    .addEdge("tools", "agent");

  return builder.compile({ checkpointer: app.checkpointer() as never });
}

export function createChatModel(app: App): BaseChatModel {
  const llmConfig = app.llmConfig;
  const provider = (llmConfig?.provider ?? "").trim().toLowerCase();
  const configuredModel = (llmConfig?.model ?? "").trim();
  const configuredBaseUrl = (llmConfig?.baseUrl ?? "").trim();

  if (provider === "openai") {
    const openaiKey = process.env.OPENAI_API_KEY ?? "";
    const openaiBaseUrl =
      configuredBaseUrl || (process.env.OPENAI_BASE_URL ?? "").trim();
    const config: ConstructorParameters<typeof ChatOpenAI>[0] = {
      model: configuredModel || process.env.OPENAI_MODEL || "gpt-5.4-mini",
      temperature: 0,
    };
    if (openaiKey) config.apiKey = openaiKey;
    if (openaiBaseUrl) {
      if (openaiBaseUrl.includes("grove-foundry")) {
        const baseURL = `${openaiBaseUrl.split("/v1")[0]}/v1`;
        config.configuration = {
          baseURL,
          defaultHeaders: openaiKey ? { "api-key": openaiKey } : undefined,
        };
      } else {
        config.configuration = { baseURL: openaiBaseUrl.replace(/\/$/, "") };
      }
    }
    return new ChatOpenAI(config);
  }

  if (provider === "anthropic") {
    const anthropicKey = process.env.ANTHROPIC_API_KEY ?? "";
    const anthropicBaseUrl =
      configuredBaseUrl || (process.env.ANTHROPIC_BASE_URL ?? "").trim();
    const config: ConstructorParameters<typeof ChatAnthropic>[0] = {
      model:
        configuredModel ||
        process.env.ANTHROPIC_MODEL ||
        "claude-sonnet-4-6",
      temperature: 0,
    };
    if (anthropicKey) config.apiKey = anthropicKey;
    if (anthropicBaseUrl) {
      if (anthropicBaseUrl.includes("grove-foundry")) {
        config.anthropicApiUrl = anthropicBaseUrl
          .split("/v1")[0]!
          .replace(/\/$/, "");
        if (anthropicKey) {
          config.clientOptions = {
            defaultHeaders: { "api-key": anthropicKey },
          };
        }
      } else {
        config.anthropicApiUrl = anthropicBaseUrl.replace(/\/$/, "");
      }
    }
    return new ChatAnthropic(config);
  }

  if (provider === "gemini") {
    const geminiKey =
      process.env.GEMINI_API_KEY || process.env.GOOGLE_API_KEY || "";
    return new ChatGoogleGenerativeAI({
      apiKey: geminiKey,
      model: configuredModel || process.env.GEMINI_MODEL || "gemini-2.0-flash",
      temperature: 0,
    });
  }

  throw new Error(
    "Set agent.yaml config.provider to one of: openai, anthropic, gemini.",
  );
}
