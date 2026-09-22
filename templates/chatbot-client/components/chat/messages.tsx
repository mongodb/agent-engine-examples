"use client";

import type { ChatStatus, UIMessage } from "ai";
import { ArrowDownIcon } from "lucide-react";
import { useScrollToBottom } from "@/hooks/use-scroll-to-bottom";
import { cn } from "@/lib/utils";
import { PreviewMessage, ThinkingMessage } from "./message";

type MessagesProps = {
  emptyState?: string;
  messages: UIMessage[];
  status: ChatStatus;
};

export function Messages({ emptyState = "Ask anything...", messages, status }: MessagesProps) {
  const {
    containerRef,
    endRef,
    isAtBottom,
    scrollToBottom,
  } = useScrollToBottom();

  return (
    <div className="relative flex-1 bg-background">
      {messages.length === 0 && (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
          <p className="text-lg text-muted-foreground">{emptyState}</p>
        </div>
      )}
      <div
        className={cn(
          "absolute inset-0 touch-pan-y overflow-y-auto",
          messages.length > 0 ? "bg-background" : "bg-transparent"
        )}
        ref={containerRef}
      >
        <div className="mx-auto flex min-h-full min-w-0 max-w-4xl flex-col gap-5 px-2 py-6 md:gap-7 md:px-4">
          {messages.map((message, index) => (
            <PreviewMessage
              isLoading={
                status === "streaming" && messages.length - 1 === index
              }
              key={message.id}
              message={message}
            />
          ))}

          {status === "submitted" && messages.at(-1)?.role !== "assistant" && (
            <ThinkingMessage />
          )}

          <div
            className="min-h-[24px] min-w-[24px] shrink-0"
            ref={endRef}
          />
        </div>
      </div>

      <button
        aria-label="Scroll to bottom"
        className={`absolute bottom-4 left-1/2 z-10 flex -translate-x-1/2 items-center rounded-full border border-border/50 bg-card/90 px-3.5 shadow-[var(--shadow-float)] backdrop-blur-lg transition-all duration-200 h-7 text-[10px] ${
          isAtBottom
            ? "pointer-events-none scale-90 opacity-0"
            : "pointer-events-auto scale-100 opacity-100"
        }`}
        onClick={() => scrollToBottom("smooth")}
        type="button"
      >
        <ArrowDownIcon className="size-3 text-muted-foreground" />
      </button>
    </div>
  );
}
