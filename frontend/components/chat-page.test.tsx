import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatPage } from "./chat-page";

const mocks = vi.hoisted(() => ({
  createConversation: vi.fn(),
  getMemoryStats: vi.fn(),
  getConversationContext: vi.fn(),
  getTtsStatus: vi.fn(),
  listConversations: vi.fn(),
  listMessages: vi.fn(),
  stopSpeech: vi.fn(),
  streamChat: vi.fn(),
  router: { push: vi.fn(), replace: vi.fn() },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => mocks.router,
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/api", () => ({
  apiUrl: (path: string) => path,
  archiveConversation: vi.fn(),
  createConversation: mocks.createConversation,
  deleteConversation: vi.fn(),
  errorMessage: (_cause: unknown, fallback: string) => fallback,
  getMemoryStats: mocks.getMemoryStats,
  getConversationContext: mocks.getConversationContext,
  getNextSpeech: vi.fn(),
  getTtsStatus: mocks.getTtsStatus,
  listConversations: mocks.listConversations,
  listMessages: mocks.listMessages,
  prepareSpeech: vi.fn(),
  stopSpeech: mocks.stopSpeech,
  streamChat: mocks.streamChat,
  truncateMessages: vi.fn(),
  updateConversation: vi.fn(),
  transcribeAudio: vi.fn(),
}));

vi.mock("@/components/workspace-topbar", () => ({
  MemoryMark: () => <span />,
  notifyWorkspaceConversationsChanged: vi.fn(),
  WorkspacePageFallback: ({ message }: { message: string }) => <div>{message}</div>,
}));

vi.mock("@/components/voice-input-button", () => ({
  VoiceInputButton: () => <button type="button">语音输入</button>,
}));

vi.mock("@/lib/media-playback", () => ({
  resetMediaElement: vi.fn(),
}));

const conversation = {
  id: 42,
  title: "新对话",
  created_at: "2026-08-07T00:00:00Z",
  updated_at: "2026-08-07T00:00:00Z",
  thinking: null,
};

async function startConversation() {
  render(<ChatPage />);
  const input = await screen.findByPlaceholderText("和我聊聊，或告诉我一件想记住的事……");
  fireEvent.change(input, { target: { value: "测试问题" } });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(mocks.streamChat).toHaveBeenCalledOnce());
}

describe("ChatPage streaming lifecycle", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listConversations.mockResolvedValue([]);
    mocks.listMessages.mockResolvedValue([]);
    mocks.createConversation.mockResolvedValue(conversation);
    mocks.getMemoryStats.mockResolvedValue({ total_memories: 0 });
    mocks.getConversationContext.mockResolvedValue({ history_chars: 0, history_budget_chars: 120000, retained_messages: 0, retained_turns: 0, trimmed_messages: 0, prompt_tokens: 0, cached_tokens: 0 });
    mocks.getTtsStatus.mockResolvedValue({ mode: "off", enabled: false });
    mocks.stopSpeech.mockResolvedValue({ dropped: 0 });
  });

  afterEach(() => {
    cleanup();
  });

  it("applies a title event to the conversation that owns the stream", async () => {
    let finishStream!: () => void;
    mocks.streamChat.mockImplementation(async (_id, _content, onEvent) => {
      onEvent({ type: "title", title: "服务端标题" });
      await new Promise<void>((resolve) => { finishStream = resolve; });
    });

    await startConversation();

    expect(await screen.findByText("服务端标题")).toBeInTheDocument();
    finishStream();
  });

  it("aborts the active stream when the page unmounts", async () => {
    let streamSignal: AbortSignal | undefined;
    mocks.streamChat.mockImplementation(async (_id, _content, _onEvent, signal?: AbortSignal) => {
      streamSignal = signal;
      await new Promise<void>((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
      });
    });

    const view = render(<ChatPage />);
    const input = await screen.findByPlaceholderText("和我聊聊，或告诉我一件想记住的事……");
    fireEvent.change(input, { target: { value: "测试问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(mocks.streamChat).toHaveBeenCalledOnce());

    view.unmount();

    expect(streamSignal?.aborted).toBe(true);
  });
});
