import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
// agentic-create: llm-imports:start
import { ChatAnthropic } from "@langchain/anthropic";
// agentic-create: llm-imports:end

// agentic-create: llm-builder:start
const MODEL = "claude-sonnet-5";

export function buildLLM(opts: { temperature?: number } = {}): BaseChatModel {
  const apiKey = process.env.LLM_API_KEY ?? "";
  if (!apiKey) {
    throw new Error("LLM_API_KEY is missing; add it to .env or project secrets");
  }
  return new ChatAnthropic({
    model: MODEL,
    apiKey,
  });
}
// agentic-create: llm-builder:end
