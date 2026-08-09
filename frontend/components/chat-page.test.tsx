import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatPage } from "./chat-page";

const mocks = vi.hoisted(() => ({
  createConversation: vi.fn(),
  getMemoryStats: vi.fn(),
  getModelCatalog: vi.fn(),
  getRuntimeSettings: vi.fn(),
  getConversationContext: vi.fn(),
  getTtsStatus: vi.fn(),
  listConversations: vi.fn(),
  listMessages: vi.fn(),
  stopSpeech: vi.fn(),
  streamChat: vi.fn(),
  searchParams: "",
  router: { push: vi.fn(), replace: vi.fn() },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => mocks.router,
  useSearchParams: () => new URLSearchParams(mocks.searchParams),
}));

vi.mock("@/lib/api", () => ({
  apiUrl: (path: string) => path,
  archiveConversation: vi.fn(),
  createConversation: mocks.createConversation,
  deleteConversation: vi.fn(),
  errorMessage: (_cause: unknown, fallback: string) => fallback,
  getMemoryStats: mocks.getMemoryStats,
  getModelCatalog: mocks.getModelCatalog,
  getRuntimeSettings: mocks.getRuntimeSettings,
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
  conversationsChangedEvent: "chat-memo:conversations-changed",
  MemoryMark: () => <span />,
  notifyWorkspaceConversationsChanged: vi.fn(),
  notifyWorkspaceSelectedConversationChanged: vi.fn(),
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
  model_profile_id: null,
};

const otherConversation = {
  ...conversation,
  id: 43,
  title: "另一段对话",
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
    mocks.searchParams = "";
    mocks.listConversations.mockResolvedValue([]);
    mocks.listMessages.mockResolvedValue([]);
    mocks.createConversation.mockResolvedValue(conversation);
    mocks.getMemoryStats.mockResolvedValue({ total_memories: 0 });
    mocks.getModelCatalog.mockResolvedValue({ purpose: "chat", default_profile_id: null, services: [], profiles: [] });
    mocks.getRuntimeSettings.mockResolvedValue({ web_search_enabled: true });
    mocks.getConversationContext.mockResolvedValue({ history_chars: 0, history_budget_chars: 120000, retained_messages: 0, retained_turns: 0, trimmed_messages: 0, prompt_tokens: 0, cached_tokens: 0 });
    mocks.getTtsStatus.mockResolvedValue({ mode: "off", enabled: false });
    mocks.stopSpeech.mockResolvedValue({ dropped: 0 });
  });

  afterEach(() => {
    cleanup();
  });

  it("keeps the redundant conversation title out of the toolbar", async () => {
    let finishStream!: () => void;
    mocks.streamChat.mockImplementation(async (_id, _content, _modelProfileId, onEvent) => {
      onEvent({ type: "conversation", conversation });
      onEvent({ type: "title", title: "服务端标题" });
      await new Promise<void>((resolve) => { finishStream = resolve; });
    });

    await startConversation();

    expect(screen.queryByText("服务端标题")).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "选择聊天模型" })).toBeInTheDocument();
    finishStream();
  });

  it("sends web search only after the composer tool is enabled", async () => {
    mocks.streamChat.mockResolvedValue(undefined);
    render(<ChatPage />);

    fireEvent.click(await screen.findByRole("button", { name: "添加工具" }));
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: /联网搜索/ }));
    const input = await screen.findByPlaceholderText("和我聊聊，或告诉我一件想记住的事……");
    fireEvent.change(input, { target: { value: "查一下今天的新闻" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(mocks.streamChat).toHaveBeenCalledWith(
      null,
      "查一下今天的新闻",
      null,
      expect.any(Function),
      expect.any(AbortSignal),
      true,
    ));
  });

  it("edits a user message in place before resending", async () => {
    mocks.searchParams = "conversation=42";
    mocks.listConversations.mockResolvedValue([conversation]);
    mocks.listMessages.mockResolvedValue([
      {
        id: 100,
        role: "user",
        content: [{ type: "text", text: "原来的问题" }],
        usage: null,
        created_at: "2026-08-07T00:00:00Z",
      },
      {
        id: 101,
        role: "assistant",
        content: [{ type: "text", text: "原来的回答" }],
        usage: null,
        created_at: "2026-08-07T00:00:01Z",
      },
    ]);
    mocks.streamChat.mockResolvedValue(undefined);

    render(<ChatPage />);
    expect(await screen.findByText("原来的问题")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "编辑重发" }));

    const editor = await screen.findByRole("textbox", { name: "编辑这条消息后重新发送…" });
    expect(editor).toHaveValue("原来的问题");
    fireEvent.change(editor, { target: { value: "修改后的问题" } });
    fireEvent.click(screen.getByRole("button", { name: "重发" }));

    await waitFor(() => expect(mocks.streamChat).toHaveBeenCalledWith(
      42,
      "修改后的问题",
      null,
      expect.any(Function),
      expect.any(AbortSignal),
      false,
    ));
  });

  it("aborts the active stream when the page unmounts", async () => {
    let streamSignal: AbortSignal | undefined;
    mocks.streamChat.mockImplementation(async (_id, _content, _modelProfileId, _onEvent, signal?: AbortSignal) => {
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

  it("keeps a live response and its final history scoped to the owning conversation", async () => {
    mocks.searchParams = "conversation=42";
    mocks.listConversations.mockResolvedValue([conversation, otherConversation]);
    mocks.listMessages.mockImplementation(async (id: number) => id === 43 ? [{
      id: 901,
      role: "assistant",
      content: [{ type: "text", text: "第二段会话的内容" }],
      usage: null,
      created_at: "2026-08-07T00:00:00Z",
    }] : []);

    let emit!: (event: { type: string; text?: string }) => void;
    let finishStream!: () => void;
    mocks.streamChat.mockImplementation(async (_id, _content, _modelProfileId, onEvent) => {
      emit = onEvent;
      await new Promise<void>((resolve) => { finishStream = resolve; });
    });

    const view = render(<ChatPage />);
    const input = await screen.findByPlaceholderText("写下你的问题或想让我记住的事…");
    fireEvent.change(input, { target: { value: "属于第一段的问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(mocks.streamChat).toHaveBeenCalledOnce());
    emit({ type: "text_delta", text: "第一段的流式回答" });
    expect(await screen.findByText("第一段的流式回答")).toBeInTheDocument();

    mocks.searchParams = "conversation=43";
    view.rerender(<ChatPage />);
    expect(await screen.findByText("第二段会话的内容")).toBeInTheDocument();

    emit({ type: "text_delta", text: "不应该出现在第二段" });
    await waitFor(() => expect(screen.queryByText("不应该出现在第二段")).not.toBeInTheDocument());

    finishStream();
    await waitFor(() => expect(mocks.listMessages).toHaveBeenCalledWith(42));
    expect(screen.getByText("第二段会话的内容")).toBeInTheDocument();
    expect(screen.queryByText("第一段的流式回答")).not.toBeInTheDocument();
  });
});
