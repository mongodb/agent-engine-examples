"use client";

import { useRef, useState } from "react";
import { DefaultChatTransport } from "ai";
import { useChat } from "@ai-sdk/react";
import { DicesIcon } from "lucide-react";
import { generateUUID } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Messages } from "./messages";
import { ChatInput } from "./chat-input";

export function Chat() {
  const [input, setInput] = useState("");
  const [userId, setUserId] = useState("default-user-id");
  const [sessionId, setSessionId] = useState(() => generateUUID());
  const userIdRef = useRef(userId);
  userIdRef.current = userId;
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;

  const [transport] = useState(
    () =>
      new DefaultChatTransport({
        api: "/api/chat",
        body: () => ({
          user_id: userIdRef.current,
          session_id: sessionIdRef.current,
          thread_id: sessionIdRef.current,
        }),
      })
  );

  const {
    messages,
    sendMessage,
    status,
    stop,
  } = useChat({
    generateId: generateUUID,
    transport,
  });

  return (
    <div className="flex h-dvh w-full flex-col bg-background">
      <header className="sticky top-0 z-10 border-b border-border/40 bg-background/80 backdrop-blur-md">
        <div className="mx-auto flex w-full max-w-4xl flex-col gap-2 px-4 py-3">
          <h1 className="text-sm font-semibold tracking-tight text-foreground">
            MongoDB Agentic Platform – Chatbot Starter App
          </h1>
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
                onChange={(e) => setSessionId(e.target.value)}
                value={sessionId}
              />
              <Button
                aria-label="Randomize session ID"
                onClick={() => setSessionId(generateUUID())}
                size="icon-xs"
                variant="outline"
              >
                <DicesIcon className="size-3.5" />
              </Button>
            </div>
          </div>
        </div>
      </header>
      <Messages messages={messages} status={status} />
      <div className="sticky bottom-0 z-1 mx-auto flex w-full max-w-4xl gap-2 border-t-0 bg-background px-2 pb-3 md:px-4 md:pb-4">
        <ChatInput
          input={input}
          setInput={setInput}
          sendMessage={sendMessage}
          status={status}
          stop={stop}
        />
      </div>
    </div>
  );
}
