import { describe, expect, it } from "vitest";
import { toTurns, toolLabel } from "./turns";
import type { ApiMessage } from "./types";

describe("toTurns", () => {
  it("turns six raw messages into two visible turns and pairs tools", () => {
    const messages: ApiMessage[] = [
      { id: 6, role: "user", content: [{ type: "text", text: "记住我的偏好" }], usage: null, created_at: "" },
      { id: 7, role: "assistant", content: [{ type: "thinking", thinking: "想一下" }, { type: "tool_use", id: "a", name: "memory", input: { command: "view", path: "/memories/MEMORY.md" } }], usage: null, created_at: "" },
      { id: 8, role: "user", content: [{ type: "tool_result", tool_use_id: "a", content: "不存在", is_error: true }], usage: null, created_at: "" },
      { id: 9, role: "assistant", content: [{ type: "tool_use", id: "b", name: "memory", input: { command: "create", path: "/memories/profile.md" } }], usage: null, created_at: "" },
      { id: 10, role: "user", content: [{ type: "tool_result", tool_use_id: "b", content: "已创建" }], usage: null, created_at: "" },
      { id: 11, role: "assistant", content: [{ type: "text", text: "记下了。" }], usage: null, created_at: "" },
    ];
    const turns = toTurns(messages);
    expect(turns).toHaveLength(2);
    expect(turns[0]).toEqual({ kind: "user", text: "记住我的偏好", messageId: 6 });
    expect(turns[1]).toMatchObject({ kind: "assistant", text: "记下了。", thinking: "想一下", messageId: 11 });
    expect(turns[1].kind === "assistant" && turns[1].tools).toEqual([
      { name: "memory", input: { command: "view", path: "/memories/MEMORY.md" }, ok: false, summary: "不存在" },
      { name: "memory", input: { command: "create", path: "/memories/profile.md" }, ok: true, summary: "已创建" },
    ]);
  });

  it("ignores unknown blocks", () => {
    const messages: ApiMessage[] = [{ id: 1, role: "assistant", content: [{ type: "future_block", value: "ignored" }], usage: null, created_at: "" }];
    expect(toTurns(messages)).toEqual([]);
  });

  it("keeps an interrupted assistant response visible", () => {
    const messages: ApiMessage[] = [{ id: 4, role: "assistant", content: [{ type: "text", text: "已经生成的部分" }], usage: { interrupted: true }, created_at: "" }];
    expect(toTurns(messages)).toMatchObject([{ kind: "assistant", text: "已经生成的部分", usage: { interrupted: true } }]);
  });
});

describe("toolLabel", () => {
  it("uses command-specific memory copy", () => {
    expect(toolLabel({ name: "memory", input: { command: "view", path: "/memories/MEMORY.md" } })).toBe("查阅记忆 /memories/MEMORY.md");
    expect(toolLabel({ name: "memory", input: { command: "delete", path: "/memories/x.md" } })).toBe("删除记忆 /memories/x.md");
  });

  it("uses knowledge-base tool copy without a command field", () => {
    expect(toolLabel({ name: "kb_search", input: { query: "手冲咖啡" } })).toBe("搜索知识库「手冲咖啡」");
    expect(toolLabel({ name: "kb_read", input: { path: "咖啡笔记.md" } })).toBe("查阅笔记 咖啡笔记.md");
    expect(toolLabel({ name: "kb_list", input: {} })).toBe("浏览知识库 /");
    expect(toolLabel({ name: "kb_backlinks", input: { path: "项目/chat-memo.md" } })).toBe("查找 项目/chat-memo.md 的反向链接");
  });

  it("uses timeline-specific copy", () => {
    expect(toolLabel({ name: "timeline_create", input: { title: "周五开会" } })).toBe("记录时间事项「周五开会」");
    expect(toolLabel({ name: "timeline_update", input: { id: 12 } })).toBe("更新时间事项「已有事项」");
  });
});
