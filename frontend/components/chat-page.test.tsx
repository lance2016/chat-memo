import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatPage } from "./chat-page";

const mocks = vi.hoisted(() => ({
  createConversation: vi.fn(),
  getMemoryStats: vi.fn(),
  getModelCatalog: vi.fn(),
  getContextPreview: vi.fn(),
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
  getContextPreview: mocks.getContextPreview,
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
    window.localStorage.clear();
    mocks.searchParams = "";
    mocks.listConversations.mockResolvedValue([]);
    mocks.listMessages.mockResolvedValue([]);
    mocks.createConversation.mockResolvedValue(conversation);
    mocks.getMemoryStats.mockResolvedValue({ total_memories: 0 });
    mocks.getModelCatalog.mockResolvedValue({ purpose: "chat", default_profile_id: null, services: [], profiles: [] });
    mocks.getContextPreview.mockResolvedValue({ history_chars: 0, history_budget_chars: 120000, retained_messages: 0, retained_turns: 0, trimmed_messages: 0, prompt_tokens: 0, cached_tokens: 0 });
    mocks.getRuntimeSettings.mockResolvedValue({ web_search_enabled: true });
    mocks.getConversationContext.mockResolvedValue({ history_chars: 0, history_budget_chars: 120000, retained_messages: 0, retained_turns: 0, trimmed_messages: 0, prompt_tokens: 0, cached_tokens: 0 });
    mocks.getTtsStatus.mockResolvedValue({ mode: "off", enabled: false });
    mocks.stopSpeech.mockResolvedValue({ dropped: 0 });
  });

  afterEach(() => {
    cleanup();
  });

  it("keeps the active conversation title visible in the navigation bar", async () => {
    let finishStream!: () => void;
    mocks.streamChat.mockImplementation(async (_id, _content, _modelProfileId, onEvent) => {
      onEvent({ type: "conversation", conversation });
      onEvent({ type: "title", title: "服务端标题" });
      await new Promise<void>((resolve) => { finishStream = resolve; });
    });

    await startConversation();

    expect(screen.getByText("服务端标题")).toBeInTheDocument();
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
      [],
      undefined,
      null,
    ));
  });

  it("shows DeepSeek thinking depth and sends the selected effort", async () => {
    mocks.streamChat.mockResolvedValue(undefined);
    mocks.getModelCatalog.mockResolvedValue({
      purpose: "chat",
      default_profile_id: 1,
      services: [{ id: 7, slug: "deepseek", name: "DeepSeek" }],
      profiles: [{
        id: 1,
        slug: "builtin:deepseek",
        service_id: 7,
        model_id: "deepseek-v4-flash",
        is_default: true,
        available: true,
        capabilities: { thinking: true },
        thinking_default: true,
        thinking_efforts: ["low", "high", "max"],
        thinking_effort_default: "high",
      }],
    });

    render(<ChatPage />);
    const thinking = await screen.findByRole("button", { name: "思考设置" });
    expect(thinking).toHaveTextContent("思考");
    expect(thinking).not.toHaveAttribute("title");
    fireEvent.click(thinking);
    expect(screen.queryByText("当前模型自动决定，未提供可调档位")).not.toBeInTheDocument();
    expect(screen.getByRole("menuitemradio", { name: "轻量" })).toBeInTheDocument();
    expect(screen.getByRole("menuitemradio", { name: "深入" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitemradio", { name: "最深入" }));
    expect(thinking).toHaveTextContent("思考");

    const input = screen.getByPlaceholderText("和我聊聊，或告诉我一件想记住的事……");
    fireEvent.change(input, { target: { value: "认真分析一下" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(mocks.streamChat).toHaveBeenCalledWith(
      null,
      "认真分析一下",
      1,
      expect.any(Function),
      expect.any(AbortSignal),
      false,
      [],
      true,
      "max",
    ));
  });

  it("keeps one popover shape and explains automatic depth for boolean-only models", async () => {
    mocks.getModelCatalog.mockResolvedValue({
      purpose: "chat",
      default_profile_id: 2,
      services: [{ id: 8, slug: "compatible", name: "Compatible" }],
      profiles: [{
        id: 2,
        slug: "compatible:model",
        service_id: 8,
        model_id: "reasoner",
        is_default: true,
        available: true,
        capabilities: { thinking: true },
        thinking_default: false,
        thinking_efforts: [],
        thinking_effort_default: null,
      }],
    });

    render(<ChatPage />);
    const trigger = await screen.findByRole("button", { name: "思考设置" });
    fireEvent.click(trigger);
    const toggle = screen.getByRole("menuitemcheckbox", { name: /思考/ });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("当前模型自动决定，未提供可调档位")).toBeInTheDocument();
    expect(trigger).toHaveTextContent("思考");
  });

  it("shows returned thinking without a separate settings-page preference", async () => {
    mocks.searchParams = "conversation=42";
    mocks.listConversations.mockResolvedValue([conversation]);
    mocks.listMessages.mockResolvedValue([{
      id: 301,
      role: "assistant",
      content: [
        { type: "thinking", thinking: "先分析问题结构" },
        { type: "text", text: "最终回答" },
      ],
      usage: null,
      created_at: "2026-08-07T00:00:00Z",
    }]);

    render(<ChatPage />);

    expect(await screen.findByText("思考过程")).toBeInTheDocument();
    expect(screen.getByText("最终回答")).toBeInTheDocument();
  });

  it("keeps attachment upload inside the tool menu", async () => {
    mocks.getRuntimeSettings.mockResolvedValue({ web_search_enabled: true, vision_enabled: true });

    render(<ChatPage />);

    fireEvent.click(await screen.findByRole("button", { name: "添加工具" }));
    expect(await screen.findByRole("menuitem", { name: /添加附件/ })).toBeInTheDocument();
  });

  it("closes floating menus and context usage when clicking blank space", async () => {
    render(<ChatPage />);

    fireEvent.click(await screen.findByRole("button", { name: "添加工具" }));
    expect(screen.getByRole("menu")).toBeInTheDocument();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();

    const contextSummary = await waitFor(() => {
      const summary = document.querySelector(".chat-context-indicator > summary");
      if (!summary) throw new Error("context summary not ready");
      return summary;
    });
    fireEvent.click(contextSummary);
    expect(screen.getByText("上下文用量")).toBeInTheDocument();
    fireEvent.pointerDown(document.body);
    expect(contextSummary.closest("details")).not.toHaveAttribute("open");
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
      [],
      undefined,
      null,
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

describe("ChatPage keyboard and screen-reader affordances", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    mocks.searchParams = "";
    mocks.listConversations.mockResolvedValue([]);
    mocks.listMessages.mockResolvedValue([]);
    mocks.createConversation.mockResolvedValue(conversation);
    mocks.getMemoryStats.mockResolvedValue({ total_memories: 0 });
    mocks.getModelCatalog.mockResolvedValue({ purpose: "chat", default_profile_id: null, services: [], profiles: [] });
    mocks.getContextPreview.mockResolvedValue({ history_chars: 0, history_budget_chars: 120000, retained_messages: 0, retained_turns: 0, trimmed_messages: 0, prompt_tokens: 0, cached_tokens: 0 });
    mocks.getRuntimeSettings.mockResolvedValue({ web_search_enabled: true });
    mocks.getConversationContext.mockResolvedValue({ history_chars: 0, history_budget_chars: 120000, retained_messages: 0, retained_turns: 0, trimmed_messages: 0, prompt_tokens: 0, cached_tokens: 0 });
    mocks.getTtsStatus.mockResolvedValue({ mode: "off", enabled: false });
    mocks.stopSpeech.mockResolvedValue({ dropped: 0 });
  });

  afterEach(() => {
    cleanup();
  });

  it("walks the model list with arrow keys and commits with Enter", async () => {
    mocks.getModelCatalog.mockResolvedValue({
      purpose: "chat",
      default_profile_id: 1,
      services: [{ id: 7, name: "Anthropic" }],
      profiles: [
        { id: 1, service_id: 7, model_id: "claude-opus-5", is_default: true, available: true },
        { id: 2, service_id: 7, model_id: "claude-sonnet-5", is_default: false, available: true },
      ],
    });

    render(<ChatPage />);
    const trigger = await screen.findByRole("combobox", { name: "选择聊天模型" });
    expect(trigger).toHaveTextContent("claude-opus-5");

    // 「把目录默认模型落成当前选中项」是 chat-page 的一个 effect 干的（见 useEffect
    // [conversations, default_profile_id, selectedId]）。findBy* 只等到 DOM 出现，
    // 它的 MutationObserver 回调是微任务，会抢在 React 的 passive effect 前面 ——
    // 不 flush 的话，菜单打开时的高亮起点会在「跟随默认」和 claude-opus-5 之间抖，
    // 这一条用例就变成大约每 20 次挂 1 次。
    await act(async () => {});

    // 方向键要能把菜单打开，而不是只有鼠标点得开。
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    expect(await screen.findByRole("listbox")).toBeInTheDocument();

    // 打开时高亮停在当前选中项上，再往下走一格就是第二个模型。
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    const active = trigger.getAttribute("aria-activedescendant");
    expect(document.getElementById(active ?? "")).toHaveTextContent("claude-sonnet-5");

    fireEvent.keyDown(trigger, { key: "Enter" });
    await waitFor(() => expect(screen.queryByRole("listbox")).not.toBeInTheDocument());
    expect(trigger).toHaveTextContent("claude-sonnet-5");
  });

  it("closes the model list with Escape without changing the selection", async () => {
    mocks.getModelCatalog.mockResolvedValue({
      purpose: "chat",
      default_profile_id: 1,
      services: [{ id: 7, name: "Anthropic" }],
      profiles: [{ id: 1, service_id: 7, model_id: "claude-opus-5", is_default: true, available: true }],
    });

    render(<ChatPage />);
    const trigger = await screen.findByRole("combobox", { name: "选择聊天模型" });
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    expect(await screen.findByRole("listbox")).toBeInTheDocument();

    fireEvent.keyDown(trigger, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("listbox")).not.toBeInTheDocument());
    expect(trigger).toHaveTextContent("claude-opus-5");
  });

  it("aborts the running response when Escape is pressed", async () => {
    let signal!: AbortSignal;
    let finishStream!: () => void;
    mocks.streamChat.mockImplementation(async (_id: unknown, _content: unknown, _profile: unknown, onEvent: (event: unknown) => void, abortSignal: AbortSignal) => {
      signal = abortSignal;
      onEvent({ type: "conversation", conversation });
      await new Promise<void>((resolve) => { finishStream = resolve; });
    });

    await startConversation();
    expect(signal.aborted).toBe(false);

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(signal.aborted).toBe(true));
    finishStream();
  });

  it("announces the start and the end of a response to screen readers", async () => {
    let finishStream!: () => void;
    mocks.streamChat.mockImplementation(async (_id: unknown, _content: unknown, _profile: unknown, onEvent: (event: unknown) => void) => {
      onEvent({ type: "conversation", conversation });
      await new Promise<void>((resolve) => { finishStream = resolve; });
    });

    await startConversation();
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("正在生成回答"));

    finishStream();
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("回答已完成"));
  });
});
