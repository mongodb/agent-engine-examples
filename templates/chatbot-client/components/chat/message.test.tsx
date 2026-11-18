import { render, screen } from "@testing-library/react";
import type { UIMessage } from "ai";
import { describe, expect, it } from "vitest";
import { PreviewMessage } from "@/components/chat/message";

describe("PreviewMessage", () => {
  it("renders a suspension action as an internal link", () => {
    const message = {
      id: "assistant-1",
      role: "assistant",
      parts: [
        { type: "text", text: "This request is waiting for human review." },
        { type: "data-suspension", data: {} },
      ],
    } as UIMessage;

    render(<PreviewMessage isLoading={false} message={message} />);

    const link = screen.getByRole("link", { name: "Open reviews to continue" });
    expect(link).toHaveAttribute("href", "/reviews");
    expect(link).not.toHaveAttribute("target", "_blank");
    expect(screen.queryByText(/external website/i)).not.toBeInTheDocument();
  });
});
