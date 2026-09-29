"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { DefaultChatTransport, type UIMessage } from "ai";
import { useChat } from "@ai-sdk/react";
import { DicesIcon } from "lucide-react";
import Link from "next/link";
import { generateUUID } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  CHAT_SESSION_STORAGE_KEY,
  SUSPENDED_CHAT_RESPONSE,
  isSafeSessionId,
  type ChatHistory,
} from "@/lib/session";
import { Messages } from "./messages";
import { ChatInput } from "./chat-input";

const SESSION_POLL_INTERVAL_MS = 3_000;

type SessionProgress = "suspended" | "resuming" | null;

function persistSessionId(sessionId: string) {
  try {
    window.localStorage.setItem(CHAT_SESSION_STORAGE_KEY, sessionId);
  } catch {
    // Browsers may disable storage. The active tab still keeps the session.
  }
}

function readPersistedSessionId(): string | null {
  try {
    const sessionId = window.localStorage.getItem(CHAT_SESSION_STORAGE_KEY);
    return sessionId && isSafeSessionId(sessionId) ? sessionId : null;
  } catch {
    return null;
  }
}

function readRequestedSessionId(): string | null {
  const sessionId = new URLSearchParams(window.location.search)
    .get("session_id")
    ?.trim();
  return sessionId && isSafeSessionId(sessionId) ? sessionId : null;
}

function isChatHistory(value: unknown): value is ChatHistory {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const history = value as Partial<ChatHistory>;
  return (
    Array.isArray(history.messages) &&
    (history.historyStatus === "ready" ||
      history.historyStatus === "warming" ||
      history.historyStatus === "unsupported")
  );
}

function toUIMessages(history: ChatHistory, sessionId: string): UIMessage[] {
  const messages = history.messages.map(
    (message): UIMessage => ({
      id: message.id,
      role: message.role,
      parts: [{ type: "text", text: message.content }],
    })
  );

  if (history.latestStatus === "suspended") {
    messages.push({
      id: `suspension-${sessionId}`,
      role: "assistant",
      parts: [
        { type: "text", text: SUSPENDED_CHAT_RESPONSE },
        { type: "data-suspension", data: {} },
      ],
    } as UIMessage);
  }

  return messages;
}

function sessionProgress(history: ChatHistory): SessionProgress {
  if (history.latestStatus === "suspended") return "suspended";
  if (
    history.historyStatus === "warming" ||
    history.latestStatus === "pending" ||
    history.latestStatus === "running" ||
    history.latestStatus === "resuming" ||
    (history.latestStatus === "completed" &&
      history.messages.at(-1)?.role !== "assistant")
  ) {
    return "resuming";
  }
  return null;
}

function composerPlaceholder(
  isRestoring: boolean,
  progress: SessionProgress,
  sessionChanged: boolean
): string {
  if (isRestoring) return "Loading conversation...";
  if (progress === "resuming") return "Waiting for the agent's response...";
  if (progress === "suspended") return "Complete the human review to continue";
  if (sessionChanged) return "Apply the session ID to continue";
  return "Ask anything...";
}

async function fetchChatHistory(
  sessionId: string,
  signal: AbortSignal
): Promise<ChatHistory> {
  const response = await fetch(
    `/api/chat/history?session_id=${encodeURIComponent(sessionId)}`,
    { cache: "no-store", signal }
  );
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const message =
      body &&
      typeof body === "object" &&
      !Array.isArray(body) &&
      "error" in body &&
      typeof body.error === "string"
        ? body.error
        : "Unable to load conversation history.";
    throw new Error(message);
  }
  if (!isChatHistory(body)) {
    throw new Error("Conversation history returned an invalid response.");
  }
  if (body.historyStatus === "unsupported") {
    throw new Error("Conversation history is not available for this agent.");
  }
  return body;
}

export function Chat() {
  const [input, setInput] = useState("");
  const [userId, setUserId] = useState("default-user-id");
  const [sessionId, setSessionId] = useState("");
  const [sessionInput, setSessionInput] = useState("");
  const [isRestoring, setIsRestoring] = useState(true);
  const [progress, setProgress] = useState<SessionProgress>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const historyRequest = useRef<AbortController | null>(null);
  const initialSession = useRef<{ id: string; restore: boolean } | null>(null);

  const [transport] = useState(
    () =>
      new DefaultChatTransport({
        api: "/api/chat",
      })
  );

  const {
    messages,
    sendMessage,
    setMessages,
    status,
    stop,
  } = useChat({
    generateId: generateUUID,
    transport,
    onData: (dataPart) => {
      if (
        dataPart.type === "data-session" &&
        typeof dataPart.data === "object" &&
        dataPart.data !== null &&
        "session_id" in dataPart.data &&
        typeof dataPart.data.session_id === "string"
      ) {
        const authoritativeSessionId = dataPart.data.session_id;
        setSessionId(authoritativeSessionId);
        setSessionInput(authoritativeSessionId);
        persistSessionId(authoritativeSessionId);
      } else if (dataPart.type === "data-suspension") {
        setProgress("suspended");
      }
    },
  });

  const restoreSession = useCallback(
    async (nextSessionId: string, foreground = true) => {
      historyRequest.current?.abort();
      const controller = new AbortController();
      historyRequest.current = controller;
      if (foreground) {
        setIsRestoring(true);
        setHistoryError(null);
      }

      try {
        const history = await fetchChatHistory(
          nextSessionId,
          controller.signal
        );
        if (!controller.signal.aborted) {
          setMessages(toUIMessages(history, nextSessionId));
          setProgress(sessionProgress(history));
          setHistoryError(null);
        }
      } catch (error) {
        if (!controller.signal.aborted && foreground) {
          setMessages([]);
          const message =
            error instanceof Error
              ? error.message
              : "Unable to load conversation history.";
          setHistoryError(`${message} You can continue with this session.`);
        }
      } finally {
        if (historyRequest.current === controller) {
          historyRequest.current = null;
          if (foreground) setIsRestoring(false);
        }
      }
    },
    [setMessages]
  );

  useEffect(() => {
    if (!initialSession.current) {
      const existingSessionId =
        readRequestedSessionId() ?? readPersistedSessionId();
      initialSession.current = existingSessionId
        ? { id: existingSessionId, restore: true }
        : { id: generateUUID(), restore: false };
    }

    const session = initialSession.current;
    setSessionId(session.id);
    setSessionInput(session.id);
    persistSessionId(session.id);
    if (session.restore) {
      void restoreSession(session.id);
    } else {
      setIsRestoring(false);
    }

    return () => historyRequest.current?.abort();
  }, [restoreSession]);

  useEffect(() => {
    if (!progress || !sessionId || isRestoring) return;

    const timer = window.setInterval(
      () => void restoreSession(sessionId, false),
      SESSION_POLL_INTERVAL_MS
    );
    return () => window.clearInterval(timer);
  }, [isRestoring, progress, restoreSession, sessionId]);

  const applySessionInput = useCallback(() => {
    const nextSessionId = sessionInput.trim();
    if (!isSafeSessionId(nextSessionId)) {
      setHistoryError(
        "Session IDs may contain only letters, numbers, hyphens, and underscores."
      );
      return;
    }
    if (nextSessionId === sessionId) return;

    setSessionId(nextSessionId);
    setSessionInput(nextSessionId);
    persistSessionId(nextSessionId);
    void restoreSession(nextSessionId);
  }, [restoreSession, sessionId, sessionInput]);

  const startNewSession = useCallback(() => {
    historyRequest.current?.abort();
    historyRequest.current = null;
    const nextSessionId = generateUUID();
    setSessionId(nextSessionId);
    setSessionInput(nextSessionId);
    persistSessionId(nextSessionId);
    setMessages([]);
    setProgress(null);
    setHistoryError(null);
    setIsRestoring(false);
  }, [setMessages]);

  const handleSendMessage = useCallback(
    (text: string) => {
      void sendMessage(
        { text },
        {
          body: {
            user_id: userId,
            session_id: sessionId,
            thread_id: sessionId,
          },
        }
      );
    },
    [sendMessage, sessionId, userId]
  );

  const sessionChanged = sessionInput.trim() !== sessionId;
  const composerIsDisabled =
    isRestoring || progress !== null || sessionChanged;

  return (
    <div className="flex h-dvh w-full flex-col bg-background">
      <header className="sticky top-0 z-10 border-b border-border/40 bg-background/80 backdrop-blur-md">
        <div className="mx-auto flex w-full max-w-4xl flex-col gap-2 px-4 py-3">
          <div className="flex items-center justify-between gap-4">
            <h1 className="text-sm font-semibold tracking-tight text-foreground">
              Atlas Agent Engine – Chatbot Starter App
            </h1>
            <Link className="text-sm text-muted-foreground hover:text-foreground" href="/reviews">
              Reviews
            </Link>
          </div>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <label className="text-xs text-muted-foreground whitespace-nowrap" htmlFor="user-id">
                User ID
              </label>
              <Input
                className="h-7 w-40 text-xs"
                id="user-id"
                onChange={(e) => setUserId(e.target.value)}
                value={userId}
              />
              <Button
                aria-label="Randomize user ID"
                onClick={() => setUserId(`user-${generateUUID().slice(0, 8)}`)}
                size="icon-xs"
                variant="outline"
              >
                <DicesIcon className="size-3.5" />
              </Button>
            </div>
            <div className="flex items-center gap-2">
              <label className="text-xs text-muted-foreground whitespace-nowrap" htmlFor="session-id">
                Session ID
              </label>
              <Input
                className="h-7 w-56 text-xs font-mono"
                id="session-id"
                onBlur={applySessionInput}
                onChange={(event) => {
                  setSessionInput(event.target.value);
                  setHistoryError(null);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter") event.currentTarget.blur();
                }}
                value={sessionInput}
              />
              <Button
                aria-label="Randomize session ID"
                onClick={startNewSession}
                size="icon-xs"
                variant="outline"
              >
                <DicesIcon className="size-3.5" />
              </Button>
            </div>
          </div>
        </div>
      </header>
      <Messages
        emptyState={
          isRestoring
            ? "Loading conversation..."
            : progress === "resuming"
              ? "Waiting for the agent's response..."
              : "Ask anything..."
        }
        messages={messages}
        status={status}
      />
      <div className="sticky bottom-0 z-1 mx-auto flex w-full max-w-4xl gap-2 border-t-0 bg-background px-2 pb-3 md:px-4 md:pb-4">
        <div className="w-full">
          {historyError ? (
            <p className="mb-2 text-sm text-destructive" role="alert">
              {historyError}
            </p>
          ) : null}
          <ChatInput
            disabled={composerIsDisabled}
            input={input}
            onSendMessage={handleSendMessage}
            placeholder={composerPlaceholder(
              isRestoring,
              progress,
              sessionChanged
            )}
            setInput={setInput}
            status={status}
            stop={stop}
          />
        </div>
      </div>
    </div>
  );
}
