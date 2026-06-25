"use client";

import type { ChatStatus, UIMessage } from "ai";
import type { UseChatHelpers } from "@ai-sdk/react";
import { ArrowUpIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { StopIcon } from "./icons";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

type ChatInputProps = {
  input: string;
  setInput: (value: string) => void;
  sendMessage: UseChatHelpers<UIMessage>["sendMessage"];
  status: ChatStatus;
  stop: () => void;
};

export function ChatInput({
  input,
  setInput,
  sendMessage,
  status,
  stop,
}: ChatInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [isComposing, setIsComposing] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => textareaRef.current?.focus(), 100);
    return () => clearTimeout(timer);
  }, []);

  const submitForm = useCallback(() => {
    if (!input.trim()) return;
    sendMessage({
      role: "user",
      parts: [{ type: "text", text: input }],
    });
    setInput("");
  }, [input, setInput, sendMessage]);

  return (
    <form
      className="w-full"
      onSubmit={(e) => {
        e.preventDefault();
        if (input.trim() && (status === "ready" || status === "error")) {
          submitForm();
        }
      }}
    >
      <div className="overflow-hidden rounded-2xl border border-border/30 bg-card/70 shadow-[var(--shadow-composer)] transition-shadow duration-300 focus-within:shadow-[var(--shadow-composer-focus)]">
        <textarea
          className="field-sizing-content max-h-48 min-h-24 w-full resize-none border-0 bg-transparent px-4 pt-3.5 pb-1.5 text-[13px] leading-relaxed outline-none placeholder:text-muted-foreground/35"
          onChange={(e) => setInput(e.target.value)}
          onCompositionEnd={() => setIsComposing(false)}
          onCompositionStart={() => setIsComposing(true)}
          onKeyDown={(e) => {
            if (e.key !== "Enter" || e.shiftKey || isComposing || e.nativeEvent.isComposing) return;
            e.preventDefault();
            if (input.trim() && (status === "ready" || status === "error")) {
              submitForm();
            }
          }}
          placeholder="Ask anything..."
          ref={textareaRef}
          value={input}
        />
        <div className="flex items-center justify-end px-3 pb-3">
          {status === "submitted" || status === "streaming" ? (
            <Button
              className="h-7 w-7 rounded-xl bg-foreground p-1 text-background transition-all duration-200 hover:opacity-85 active:scale-95"
              onClick={(e) => {
                e.preventDefault();
                stop();
              }}
              type="button"
            >
              <StopIcon size={14} />
            </Button>
          ) : (
            <button
              className={cn(
                "inline-flex h-7 w-7 items-center justify-center rounded-xl transition-all duration-200",
                input.trim()
                  ? "bg-foreground text-background hover:opacity-85 active:scale-95"
                  : "bg-muted text-muted-foreground/25 cursor-not-allowed"
              )}
              disabled={!input.trim()}
              type="submit"
            >
              <ArrowUpIcon className="size-4" />
            </button>
          )}
        </div>
      </div>
    </form>
  );
}
