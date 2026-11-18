const SAFE_SESSION_ID = /^[A-Za-z0-9_-]{1,128}$/;

export const CHAT_SESSION_STORAGE_KEY = "mongodb-agent-engine-chat-session-id";
export const SUSPENDED_CHAT_RESPONSE = "This request is waiting for human review.";

export type ChatHistoryMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

export type ChatHistory = {
  messages: ChatHistoryMessage[];
  latestStatus: string | null;
  historyStatus: "ready" | "warming" | "unsupported";
};

export function isSafeSessionId(sessionId: string): boolean {
  return SAFE_SESSION_ID.test(sessionId);
}
