import type { ApiMessage, ContentBlock, ToolActivity, Turn } from "./types";

function isToolResult(block: ContentBlock): block is Extract<ContentBlock, { type: "tool_result" }> {
  return block.type === "tool_result";
}

function isToolUse(block: ContentBlock): block is { type: "tool_use"; id: string; name: string; input: Record<string, unknown> } {
  return block.type === "tool_use" && typeof block.id === "string" && typeof block.name === "string" && typeof block.input === "object" && block.input !== null;
}

export function toTurns(messages: ApiMessage[]): Turn[] {
  const turns: Turn[] = [];
  const toolInputs = new Map<string, { name: string; input: Record<string, unknown> }>();

  for (const message of messages) {
    const blocks = message.content ?? [];
    const isToolResultOnly = blocks.length > 0 && blocks.every(isToolResult);

    if (message.role === "user" && isToolResultOnly) {
      const last = turns.at(-1);
      if (last?.kind === "assistant") {
        for (const block of blocks) {
          const call = toolInputs.get(block.tool_use_id);
          last.tools.push({
            name: call?.name ?? "memory",
            input: call?.input ?? {},
            ok: !block.is_error,
            summary: typeof block.content === "string" ? block.content : "",
          });
        }
      }
      continue;
    }

    if (message.role === "user") {
      // 附件引用必须在这里留住：这个函数是有损的（只保下面认识的块），
      // 漏掉它的症状是刷新一次页面，贴过的图就从气泡里消失了。
      const attachments = blocks
        .filter((block): block is Extract<ContentBlock, { type: "attachment_ref" }> => block.type === "attachment_ref")
        .map((block) => ({
          id: block.id,
          filename: block.filename,
          ...(block.mime ? { mime: block.mime } : {}),
          ...(typeof block.bytes === "number" ? { bytes: block.bytes } : {}),
          ...(typeof block.width === "number" ? { width: block.width } : {}),
          ...(typeof block.height === "number" ? { height: block.height } : {}),
        }));
      turns.push({
        kind: "user",
        text: blocks.filter((block): block is Extract<ContentBlock, { type: "text" }> => block.type === "text").map((block) => block.text).join("\n"),
        ...(attachments.length > 0 ? { attachments } : {}),
        messageId: message.id,
      });
      continue;
    }

    const text = blocks.filter((block): block is Extract<ContentBlock, { type: "text" }> => block.type === "text").map((block) => block.text).join("");
    const thinking = blocks.filter((block): block is Extract<ContentBlock, { type: "thinking" }> => block.type === "thinking").map((block) => block.thinking).join("");
    const toolUses = blocks.filter(isToolUse);
    for (const block of toolUses) toolInputs.set(block.id, { name: block.name, input: block.input });
    if (!text && !thinking && toolUses.length === 0 && !message.usage?.interrupted) continue;

    const last = turns.at(-1);
    if (last?.kind === "assistant") {
      last.text += text;
      last.thinking += thinking;
      if (message.usage) last.usage = message.usage;
      last.messageId = message.id;
    } else {
      turns.push({ kind: "assistant", text, thinking, tools: [], usage: message.usage ?? undefined, messageId: message.id });
    }
  }

  return turns;
}

export function toolLabel(tool: Pick<ToolActivity, "name" | "input">) {
  const inputValue = (key: string, fallback: string) => typeof tool.input[key] === "string" && tool.input[key] ? String(tool.input[key]) : fallback;
  if (tool.name === "kb_search") return `搜索知识库「${inputValue("query", "相关内容")}」`;
  if (tool.name === "kb_read") return `查阅笔记 ${inputValue("path", "知识库笔记")}`;
  if (tool.name === "kb_list") return `浏览知识库 ${inputValue("path", "/")}`;
  if (tool.name === "kb_backlinks") return `查找 ${inputValue("path", "笔记")} 的反向链接`;
  if (tool.name === "timeline_list") return "查看近期时间事项";
  if (tool.name === "timeline_create") return `记录时间事项「${inputValue("title", "新事项")}」`;
  if (tool.name === "timeline_update") return `更新时间事项「${inputValue("title", "已有事项")}」`;
  if (tool.name === "web_search") return `联网搜索「${inputValue("query", "相关内容")}」`;

  const command = typeof tool.input.command === "string" ? tool.input.command : "";
  const path = typeof tool.input.path === "string" ? tool.input.path : "记忆";
  if (command === "view") return `查阅记忆 ${path}`;
  if (["create", "str_replace", "insert"].includes(command)) return `更新记忆 ${path}`;
  if (command === "delete") return `删除记忆 ${path}`;
  return `${tool.name} ${path}`;
}
