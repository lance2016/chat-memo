import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { I18nProvider } from "./i18n-provider";
import { IndexAuditCard } from "./memories-page";
import type { MemoryIndexAudit } from "@/lib/types";

afterEach(cleanup);

function audit(overrides: Partial<MemoryIndexAudit> = {}): MemoryIndexAudit {
  return {
    ok: true,
    issue_count: 0,
    summary: "",
    total_files: 3,
    index_missing: false,
    missing: [],
    orphaned: [],
    overlong: [],
    malformed: [],
    ...overrides,
  };
}

function renderCard(value: MemoryIndexAudit | null, onOpenFile = vi.fn(), error = "") {
  render(<I18nProvider><IndexAuditCard audit={value} error={error} onOpenFile={onOpenFile} /></I18nProvider>);
  return onOpenFile;
}

it("索引对得上时只给一行确认，不制造噪音", () => {
  renderCard(audit());

  expect(screen.getByText(/索引和 3 个记忆文件完全对得上/)).toBeInTheDocument();
  expect(screen.queryByText("没进索引")).not.toBeInTheDocument();
});

it("漏进索引的记忆要点得开——这是这张卡片存在的全部理由", () => {
  // 文件还在、左侧目录里点得开，但模型永远不知道它存在。只有这里会说出来。
  const onOpenFile = renderCard(audit({
    ok: false,
    issue_count: 1,
    missing: ["/memories/projects/chat.md"],
  }));

  expect(screen.getByText("没进索引")).toBeInTheDocument();
  fireEvent.click(screen.getByTitle("/memories/projects/chat.md"));

  expect(onOpenFile).toHaveBeenCalledWith("/memories/projects/chat.md");
});

it("孤儿条目不给跳转按钮——那个文件根本不存在", () => {
  renderCard(audit({ ok: false, issue_count: 1, orphaned: ["/memories/projects/gone.md"] }));

  expect(screen.getByText("projects/gone.md")).toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

it("超长条目带上实际字数，好判断要删多少", () => {
  renderCard(audit({ ok: false, issue_count: 1, overlong: [["/memories/profile/preferences.md", 48]] }));

  expect(screen.getByText("条目描述超长")).toBeInTheDocument();
  expect(screen.getByText("48 字")).toBeInTheDocument();
});

it("问题数量显示在标题上，一眼能看出严重程度", () => {
  renderCard(audit({ ok: false, issue_count: 4, missing: ["/memories/a.md"], orphaned: ["/memories/b.md"] }));

  expect(screen.getByText("4")).toBeInTheDocument();
  expect(screen.getByText(/下次每日整理会带着这些问题/)).toBeInTheDocument();
});

it("校验接口挂了只影响这张卡片，不冒充「索引没问题」", () => {
  renderCard(null, vi.fn(), "连接失败");

  expect(screen.getByText(/无法读取索引校验/)).toBeInTheDocument();
});
