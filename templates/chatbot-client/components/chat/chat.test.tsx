import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Chat } from "@/components/chat/chat";
import { CHAT_SESSION_STORAGE_KEY } from "@/lib/session";

let chatOptions: { onData?: (part: { type: string; data: unknown }) => void } = {};
const sendMessage = vi.fn();
const setMessages = vi.fn();
let storedValues: Map<string, string>;

vi.mock("@ai-sdk/react", () => ({
  useChat: (options: typeof chatOptions) => {
    chatOptions = options;
    return {
      messages: [],
      sendMessage,
      setMessages,
      status: "ready",
      stop: vi.fn(),
    };
  },
}));

vi.mock("ai", async (importOriginal) => {
  const actual = await importOriginal<typeof import("ai")>();
  return { ...actual, DefaultChatTransport: class {} };
});

describe("Chat", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/");
    storedValues = new Map();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storedValues.get(key) ?? null,
      setItem: (key: string, value: string) => storedValues.set(key, value),
    });
    chatOptions = {};
    sendMessage.mockReset();
    setMessages.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("adopts the authoritative session for later turns", async () => {
    const user = userEvent.setup();
    render(<Chat />);

    act(() => {
      chatOptions.onData?.({
        type: "data-session",
        data: { session_id: "authoritative-session" },
      });
    });

    expect(screen.getByLabelText("Session ID")).toHaveValue("authoritative-session");
    expect(window.localStorage.getItem(CHAT_SESSION_STORAGE_KEY)).toBe(
      "authoritative-session"
    );
    expect(screen.getByRole("link", { name: "Reviews" })).toHaveAttribute(
      "href",
      "/reviews"
    );

    await user.type(screen.getByPlaceholderText("Ask anything..."), "Next turn");
    await user.keyboard("{Enter}");

    expect(sendMessage).toHaveBeenCalledWith(
      { text: "Next turn" },
      {
        body: {
          user_id: "default-user-id",
          session_id: "authoritative-session",
          thread_id: "authoritative-session",
        },
      }
    );
  });

  it("restores the persisted session and its messages after a refresh", async () => {
    window.localStorage.setItem(CHAT_SESSION_STORAGE_KEY, "persisted-session");
    const fetchMock = vi.fn().mockResolvedValue(
      Response.json({
        messages: [
          { id: "message-1", role: "user", content: "Review my claim" },
          { id: "message-2", role: "assistant", content: "I can help." },
        ],
        latestStatus: "completed",
        historyStatus: "ready",
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<Chat />);

    expect(screen.getByRole("textbox", { name: "Session ID" })).toHaveValue(
      "persisted-session"
    );
    expect(screen.getByPlaceholderText("Loading conversation...")).toBeDisabled();

    await waitFor(() => {
      expect(setMessages).toHaveBeenCalledWith([
        {
          id: "message-1",
          role: "user",
          parts: [{ type: "text", text: "Review my claim" }],
        },
        {
          id: "message-2",
          role: "assistant",
          parts: [{ type: "text", text: "I can help." }],
        },
      ]);
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/chat/history?session_id=persisted-session",
      expect.objectContaining({ cache: "no-store" })
    );
    expect(screen.getByPlaceholderText("Ask anything...")).toBeEnabled();
  });

  it("polls a suspended session until the resumed result appears", async () => {
    window.localStorage.setItem(CHAT_SESSION_STORAGE_KEY, "suspended-session");
    let poll: (() => void) | undefined;
    vi.spyOn(window, "setInterval").mockImplementation((handler) => {
      poll = handler as () => void;
      return 1 as unknown as ReturnType<typeof window.setInterval>;
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({
          messages: [{ id: "message-1", role: "user", content: "Approve it" }],
          latestStatus: "suspended",
          historyStatus: "ready",
        })
      )
      .mockResolvedValueOnce(
        Response.json({
          messages: [
            { id: "message-1", role: "user", content: "Approve it" },
            {
              id: "message-2",
              role: "assistant",
              content: "The claim was approved.",
            },
          ],
          latestStatus: "completed",
          historyStatus: "ready",
        })
      );
    vi.stubGlobal("fetch", fetchMock);

    render(<Chat />);

    await waitFor(() =>
      expect(screen.getByPlaceholderText("Complete the human review to continue"))
        .toBeDisabled()
    );
    expect(poll).toBeDefined();

    act(() => poll?.());

    await waitFor(() => {
      expect(setMessages).toHaveBeenLastCalledWith([
        {
          id: "message-1",
          role: "user",
          parts: [{ type: "text", text: "Approve it" }],
        },
        {
          id: "message-2",
          role: "assistant",
          parts: [{ type: "text", text: "The claim was approved." }],
        },
      ]);
    });
    expect(screen.getByPlaceholderText("Ask anything...")).toBeEnabled();
  });

  it("opens a reviewed session from the URL", async () => {
    window.history.replaceState({}, "", "/?session_id=reviewed-session");
    window.localStorage.setItem(CHAT_SESSION_STORAGE_KEY, "previous-session");
    const fetchMock = vi.fn().mockResolvedValue(
      Response.json({
        messages: [],
        latestStatus: null,
        historyStatus: "ready",
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<Chat />);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/chat/history?session_id=reviewed-session",
        expect.objectContaining({ cache: "no-store" })
      );
    });
    expect(screen.getByLabelText("Session ID")).toHaveValue("reviewed-session");
    expect(window.localStorage.getItem(CHAT_SESSION_STORAGE_KEY)).toBe(
      "reviewed-session"
    );
  });
});
