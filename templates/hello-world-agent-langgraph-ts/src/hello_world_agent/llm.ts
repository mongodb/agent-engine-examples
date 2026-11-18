import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
// agentic-create: llm-imports:start
import { ChatOpenAI } from "@langchain/openai";
// agentic-create: llm-imports:end

// agentic-create: llm-builder:start
const MODEL = "gpt-5.4-mini";

export function buildLLM(opts: { temperature?: number } = {}): BaseChatModel {
  const apiKey = process.env.LLM_API_KEY ?? "";
  if (!apiKey) {
    throw new Error("LLM_API_KEY is missing; add it to .env or project secrets");
  }
  return new ChatOpenAI({
    model: MODEL,
    apiKey,
    temperature: opts.temperature ?? 0,
  });
}
// agentic-create: llm-builder:end
