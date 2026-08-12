import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WorkspaceTopbar } from "./workspace-topbar";
import { ToastProvider } from "./toast";

const mocks = vi.hoisted(() => ({
  listConversations: vi.fn(),
  deleteConversation: vi.fn(),
  archiveConversation: vi.fn(),
  updateConversation: vi.fn(),
  router: { push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() },
}));

vi.mock("next/navigation", () => ({ useRouter: () => mocks.router }));
vi.mock("next/image", () => ({ default: () => <span /> }));

vi.mock("@/lib/api", () => ({
  archiveConversation: mocks.archiveConversation,
  deleteConversation: mocks.deleteConversation,
  errorMessage: (cause: unknown, fallback: string) => (cause instanceof Error ? cause.message : fallback),
  listConversations: mocks.listConversations,
  updateConversation: mocks.updateConversation,
}));

vi.mock("@/components/global-search", () => ({ SearchTrigger: () => <button type="button">搜索</button> }));
vi.mock("@/components/theme-control", () => ({ ThemeControl: () => <button type="button">主题</button> }));
vi.mock("@/components/language-control", () => ({ LanguageControl: () => <button type="button">语言</button> }));

const only = { id: 7, title: "你好", created_at: "2026-08-09T00:00:00Z", updated_at: "2026-08-09T00:00:00Z", thinking: null, model_profile_id: null };

function renderTopbar() {
  return render(<ToastProvider><WorkspaceTopbar active="chat" /></ToastProvider>);
}

async function openDeleteDialog() {
  fireEvent.click(await screen.findByRole("button", { name: /你好/ }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "删除" }));
}

describe("WorkspaceTopbar deleting the last conversation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, "", "/?conversation=7");
    mocks.listConversations.mockResolvedValue([only]);
    mocks.deleteConversation.mockResolvedValue(undefined);
  });

  afterEach(() => {
    cleanup();
    window.history.replaceState({}, "", "/");
  });

  it("clears the list after the only conversation is deleted", async () => {
    renderTopbar();
    await openDeleteDialog();

    // 删掉之后服务端就空了，列表刷新必须拿到空列表。
    mocks.listConversations.mockResolvedValue([]);
    fireEvent.click(await screen.findByRole("button", { name: "永久删除会话" }));

    await waitFor(() => expect(mocks.deleteConversation).toHaveBeenCalledWith(7));
    await waitFor(() => expect(screen.queryByText("你好")).not.toBeInTheDocument());
    expect(screen.queryByText("会话不存在")).not.toBeInTheDocument();
  });

  it("does not fire a second delete when the confirm button is double-clicked", async () => {
    renderTopbar();
    await openDeleteDialog();
    mocks.listConversations.mockResolvedValue([]);

    const confirm = await screen.findByRole("button", { name: "永久删除会话" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);

    // 第二次会打到已经删掉的会话上，后端返回 404「会话不存在」。
    await waitFor(() => expect(mocks.deleteConversation).toHaveBeenCalled());
    expect(mocks.deleteConversation).toHaveBeenCalledTimes(1);
  });
});

describe("WorkspaceTopbar conversation actions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, "", "/?conversation=7");
    mocks.listConversations.mockResolvedValue([only]);
    mocks.deleteConversation.mockResolvedValue(undefined);
    mocks.archiveConversation.mockImplementation((id: number, archived: boolean) => Promise.resolve({ ...only, id, archived }));
  });

  afterEach(() => {
    cleanup();
    window.history.replaceState({}, "", "/");
  });

  it("reports the delete through a toast instead of inline red text", async () => {
    renderTopbar();
    await openDeleteDialog();
    mocks.listConversations.mockResolvedValue([]);
    fireEvent.click(await screen.findByRole("button", { name: "永久删除会话" }));

    expect(await screen.findByText("已删除会话「你好」")).toBeInTheDocument();
  });

  it("keeps the conversation row and chat surface intact while preparing a delete", async () => {
    renderTopbar();

    fireEvent.click(await screen.findByRole("button", { name: /你好/ }));
    const menu = await screen.findByRole("menu");
    expect(menu.parentElement).toBe(document.body);
    expect(screen.getByRole("link", { name: "你好" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("menuitem", { name: "删除" }));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "你好" })).toBeInTheDocument();
    expect(mocks.deleteConversation).not.toHaveBeenCalled();
  });

  it("offers a real undo for archiving, which needs no soft delete", async () => {
    renderTopbar();
    fireEvent.click(await screen.findByRole("button", { name: /你好/ }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "归档" }));

    await waitFor(() => expect(mocks.archiveConversation).toHaveBeenCalledWith(7, true));
    expect(await screen.findByText("已归档「你好」")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    await waitFor(() => expect(mocks.archiveConversation).toHaveBeenCalledWith(7, false));
  });

  it("groups the icon-only search and collapse controls beside the logo", () => {
    const onCollapsedChange = vi.fn();
    render(<ToastProvider><WorkspaceTopbar active="chat" onSidebarCollapsedChange={onCollapsedChange} /></ToastProvider>);

    const sidebar = screen.getByRole("complementary");
    const header = sidebar.querySelector<HTMLElement>(".workspace-sidebar-header");
    const primaryActions = sidebar.querySelector<HTMLElement>(".workspace-sidebar-primary-actions");
    expect(within(sidebar).queryByText("朝花夕拾")).not.toBeInTheDocument();
    expect(header).not.toBeNull();
    expect(primaryActions).not.toBeNull();
    expect(within(header!).getByRole("button", { name: "搜索" })).toBeInTheDocument();
    expect(within(primaryActions!).queryByRole("button", { name: "搜索" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "折叠侧栏" }));
    expect(onCollapsedChange).toHaveBeenCalledWith(true);
  });

  it("keeps the collapsed rail to logo, new chat, search, and recent chats", () => {
    const onCollapsedChange = vi.fn();
    render(<ToastProvider><WorkspaceTopbar active="chat" sidebarCollapsed onSidebarCollapsedChange={onCollapsedChange} /></ToastProvider>);

    const sidebar = screen.getByRole("complementary");
    expect(within(sidebar).queryByRole("navigation")).not.toBeInTheDocument();
    expect(within(sidebar).getByRole("link", { name: "新建会话" })).toBeInTheDocument();
    expect(within(sidebar).getByRole("button", { name: "搜索" })).toBeInTheDocument();
    expect(within(sidebar).getByRole("button", { name: "最近对话" })).toBeInTheDocument();
    fireEvent.click(within(sidebar).getByRole("button", { name: "展开侧栏" }));
    expect(onCollapsedChange).toHaveBeenCalledWith(false);
  });

  it("keeps common destinations visible and reserves the profile menu for secondary controls", () => {
    renderTopbar();
    const sidebar = screen.getByRole("complementary");

    const navigation = within(sidebar).getByRole("navigation");
    expect(within(navigation).getByRole("link", { name: "记忆库" })).toBeInTheDocument();
    expect(within(navigation).getByRole("link", { name: "每日回顾" })).toBeInTheDocument();
    expect(within(navigation).getByRole("link", { name: "时间线" })).toBeInTheDocument();
    expect(within(navigation).queryByRole("link", { name: "设置" })).not.toBeInTheDocument();
    expect(within(sidebar).getByRole("button", { name: "已归档对话" })).toBeInTheDocument();
    fireEvent.click(within(sidebar).getByRole("button", { name: "打开个人菜单" }));

    const menu = within(sidebar).getByRole("menu");
    expect(within(menu).getByRole("menuitem", { name: "设置" })).toBeInTheDocument();
    expect(within(menu).queryByRole("menuitem", { name: "已归档对话" })).not.toBeInTheDocument();
  });

  it("opens archived chats directly from the recent-chat section", async () => {
    renderTopbar();
    const sidebar = screen.getByRole("complementary");

    fireEvent.click(within(sidebar).getByRole("button", { name: "已归档对话" }));

    expect(await within(sidebar).findByRole("button", { name: "返回最近对话" })).toBeInTheDocument();
    expect(mocks.listConversations).toHaveBeenCalledWith(20, true, 0);
  });
});
