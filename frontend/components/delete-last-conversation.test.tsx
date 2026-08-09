import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatPage } from "./chat-page";
import { WorkspaceTopbar } from "./workspace-topbar";
import { ToastProvider } from "./toast";

/**
 * 真实应用里侧栏和聊天页是**同时挂载**的（WorkspaceFrame），两边都监听
 * conversationsChangedEvent、都各自拉会话列表。只测其中一个测不出它们的相互作用。
 */
const mocks = vi.hoisted(() => ({
  listConversations: vi.fn(),
  deleteConversation: vi.fn(),
  listMessages: vi.fn(),
  router: { push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() },
  searchParams: "conversation=94",
}));

vi.mock("next/navigation", () => ({
  useRouter: () => mocks.router,
  useSearchParams: () => new URLSearchParams(mocks.searchParams),
}));
vi.mock("next/image", () => ({ default: () => <span /> }));

vi.mock("@/lib/api", () => ({
  apiUrl: (path: string) => path,
  archiveConversation: vi.fn(),
  attachmentObjectUrl: vi.fn(),
  createConversation: vi.fn(),
  deleteConversation: mocks.deleteConversation,
  errorMessage: (cause: unknown, fallback: string) => (cause instanceof Error ? cause.message : fallback),
  getContextPreview: vi.fn().mockResolvedValue(null),
  getConversationContext: vi.fn().mockResolvedValue(null),
  getMemoryStats: vi.fn().mockResolvedValue({ total_memories: 0 }),
  getModelCatalog: vi.fn().mockResolvedValue({ purpose: "chat", default_profile_id: null, services: [], profiles: [] }),
  getNextSpeech: vi.fn(),
  getRuntimeSettings: vi.fn().mockResolvedValue({}),
  getTtsStatus: vi.fn().mockResolvedValue({ mode: "off", enabled: false }),
  listConversations: mocks.listConversations,
  listMessages: mocks.listMessages,
  prepareSpeech: vi.fn(),
  stopSpeech: vi.fn().mockResolvedValue({ dropped: 0 }),
  streamChat: vi.fn(),
  truncateMessages: vi.fn(),
  updateConversation: vi.fn(),
  uploadAttachment: vi.fn(),
}));

vi.mock("@/components/global-search", () => ({ SearchTrigger: () => <button type="button">搜索</button> }));
vi.mock("@/components/theme-control", () => ({ ThemeControl: () => <button type="button">主题</button> }));
vi.mock("@/components/language-control", () => ({ LanguageControl: () => <button type="button">语言</button> }));
vi.mock("@/components/voice-input-button", () => ({ VoiceInputButton: () => <button type="button">语音输入</button> }));
vi.mock("@/lib/media-playback", () => ({ resetMediaElement: vi.fn() }));

const only = { id: 94, title: "你好", created_at: "2026-08-09T07:56:21Z", updated_at: "2026-08-09T07:56:22Z", thinking: null, model_profile_id: null };

describe("deleting the only conversation, with the sidebar and chat page both mounted", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.searchParams = "conversation=94";
    window.history.replaceState({}, "", "/?conversation=94");
    mocks.listConversations.mockResolvedValue([only]);
    mocks.listMessages.mockResolvedValue([]);
    mocks.deleteConversation.mockResolvedValue(undefined);
  });

  afterEach(() => {
    cleanup();
    window.history.replaceState({}, "", "/");
  });

  it("removes it from the sidebar so it cannot be deleted a second time", async () => {
    render(<ToastProvider><WorkspaceTopbar active="chat" /><ChatPage /></ToastProvider>);

    fireEvent.click(await screen.findByRole("button", { name: /你好/ }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "删除" }));

    // 删掉之后服务端就空了。
    mocks.listConversations.mockResolvedValue([]);
    fireEvent.click(await screen.findByRole("button", { name: "永久删除会话" }));

    await waitFor(() => expect(mocks.deleteConversation).toHaveBeenCalledWith(94));
    // 它必须从列表里消失 —— 留在那儿用户就会再点一次，第二次是 404「会话不存在」。
    await waitFor(() => expect(screen.queryByText("你好")).not.toBeInTheDocument());
    expect(screen.queryByText("会话不存在")).not.toBeInTheDocument();
  });
});
