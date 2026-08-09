import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoriesPage } from "./memories-page";
import { ToastProvider } from "./toast";

const mocks = vi.hoisted(() => ({
  deleteMemory: vi.fn(),
  getMemory: vi.fn(),
  listMemoryNodes: vi.fn(),
  listMemoryVersions: vi.fn(),
  listAllMemoryVersions: vi.fn(),
  restoreMemoryVersion: vi.fn(),
  router: { push: vi.fn(), replace: vi.fn() },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => mocks.router,
  useSearchParams: () => new URLSearchParams(""),
}));

vi.mock("@/lib/api", () => ({
  deleteMemory: mocks.deleteMemory,
  errorMessage: (_cause: unknown, fallback: string) => fallback,
  getMemory: mocks.getMemory,
  getMemoryAudit: vi.fn().mockResolvedValue(null),
  getMemoryStats: vi.fn().mockResolvedValue(null),
  importMemories: vi.fn(),
  listAllMemoryVersions: mocks.listAllMemoryVersions,
  listMemoryNodes: mocks.listMemoryNodes,
  listMemoryVersions: mocks.listMemoryVersions,
  restoreMemoryVersion: mocks.restoreMemoryVersion,
  saveMemory: vi.fn(),
}));

vi.mock("@/components/eval-panel", () => ({ EvalPanel: () => <div /> }));
vi.mock("@/components/markdown", () => ({ Markdown: ({ children }: { children: string }) => <div>{children}</div> }));

const nodes = [
  { path: "/memories", is_dir: true, size: 0 },
  { path: "/memories/MEMORY.md", is_dir: false, size: 10 },
  { path: "/memories/profile", is_dir: true, size: 0 },
  { path: "/memories/profile/reading-notes.md", is_dir: false, size: 20 },
];

const remaining = nodes.filter((node) => node.path !== "/memories/profile/reading-notes.md");

function renderPage() {
  return render(<ToastProvider><MemoriesPage /></ToastProvider>);
}

describe("MemoriesPage delete and undo", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listMemoryNodes.mockResolvedValue(nodes);
    mocks.getMemory.mockResolvedValue({ path: "/memories/MEMORY.md", content: "索引正文", created_at: "2026-08-01T00:00:00Z", updated_at: "2026-08-01T00:00:00Z" });
    mocks.listMemoryVersions.mockResolvedValue([]);
    mocks.deleteMemory.mockResolvedValue(undefined);
    mocks.listAllMemoryVersions.mockResolvedValue([]);
  });

  afterEach(cleanup);

  it("deletes a single file without a confirmation dialog and offers undo", async () => {
    mocks.listAllMemoryVersions.mockResolvedValue([
      { id: 91, path: "/memories/MEMORY.md", content: "索引正文", operation: "deleted", actor: "manual", created_at: "2026-08-09T10:00:00Z" },
    ]);
    mocks.listMemoryNodes.mockResolvedValueOnce(nodes).mockResolvedValue(remaining);

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "删除" }));

    // 确认弹窗不该出现 —— 后悔权在吐司上。
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    await waitFor(() => expect(mocks.deleteMemory).toHaveBeenCalledWith("/memories/MEMORY.md"));
    expect(await screen.findByRole("button", { name: "撤销" })).toBeInTheDocument();
  });

  it("restores the snapshot that the delete itself produced", async () => {
    mocks.listAllMemoryVersions.mockResolvedValue([
      { id: 91, path: "/memories/MEMORY.md", content: "索引正文", operation: "deleted", actor: "manual", created_at: "2026-08-09T10:00:00Z" },
      { id: 44, path: "/memories/MEMORY.md", content: "更早的正文", operation: "modified", actor: "chat", created_at: "2026-08-08T10:00:00Z" },
    ]);
    mocks.restoreMemoryVersion.mockResolvedValue({ path: "/memories/MEMORY.md", content: "索引正文", created_at: "x", updated_at: "y" });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "删除" }));
    fireEvent.click(await screen.findByRole("button", { name: "撤销" }));

    // 必须挑 operation=deleted 的那条，而不是历史上任意一条快照。
    await waitFor(() => expect(mocks.restoreMemoryVersion).toHaveBeenCalledWith(91));
    expect(await screen.findByText("已恢复 1 份记忆")).toBeInTheDocument();
  });

  it("does not offer undo when no snapshot can be found", async () => {
    mocks.listAllMemoryVersions.mockResolvedValue([]);

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "删除" }));

    await waitFor(() => expect(mocks.deleteMemory).toHaveBeenCalledOnce());
    // 给一个点了不管用的撤销按钮，比不给还糟。
    expect(screen.queryByRole("button", { name: "撤销" })).not.toBeInTheDocument();
  });

  it("still confirms before discarding unsaved edits", async () => {
    renderPage();
    const editor = await screen.findByDisplayValue("索引正文");
    fireEvent.change(editor, { target: { value: "改了但还没保存" } });

    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    // 未保存的字从没进过版本历史，撤销救不回来，所以这一种仍然要问。
    expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
    expect(mocks.deleteMemory).not.toHaveBeenCalled();
  });

  it("keeps the confirmation for recursive directory deletes", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "递归删除目录 /memories/profile" }));

    expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
    expect(mocks.deleteMemory).not.toHaveBeenCalled();
  });
});
