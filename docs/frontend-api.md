# 前端对接文档

给写前端的人/agent 看的。后端已经跑通并实测过，本文档里的所有 JSON 都是**真实响应**，不是示意。

- Base URL：`http://localhost:8000`
- 技术栈建议：Next.js 15 (App Router) + TypeScript + Tailwind + shadcn/ui
- 前端**不直接调任何模型 API**，一律走后端

## 更新日志

按批次记录，**新的在最上面**。每批只列「相对上一批的变化」，方便对照着做增量。
详细说明都在下面对应章节里。

### 第 3 批 · 待前端实现 🆕

| 变化 | 位置 | 前端要做什么 |
|---|---|---|
| `GET /api/settings` | [运行时设置](#运行时设置) | 读当前 provider/模型，决定思考开关的初始态和可用性 |
| `PATCH /api/conversations/{id}` | [修改会话](#修改会话) | 会话级思考开关；也能改标题 |
| `POST /api/memories/restore` | [恢复历史版本](#恢复历史版本) | 版本历史加「恢复」按钮；聊天里工具条加「撤销」 |
| 消息多了中断标记 | [中断的回答](#中断的回答) | `usage.interrupted === true` 时显示「回答被中断」 |

**行为变化（无需改代码，但要知道）**：中断生成后，用户已看到的正文现在会被保存，
刷新页面不再消失。以前是全部丢失。

**自动每日整理已默认关闭**，只能手动触发（`POST /api/jobs/consolidate`）。
每日回顾页的「重新整理这一天」按钮现在是唯一入口，比之前更重要。

### 第 2 批 · 已实现 ✅

[归档](#归档)、[截断（重新生成/编辑重发）](#截断重新生成编辑重发)、
[搜索](#搜索)、[记忆使用率](#记忆使用率)、[会话摘要](#会话摘要)、
[全局记忆变更](#全局记忆变更)、[用量统计](#用量统计)，
以及消息响应里的 [`usage` 字段](#历史消息必须先规整再渲染)。

### 第 1 批 · 已实现 ✅

会话 CRUD、[聊天 SSE](#聊天sse-流)、[历史消息规整](#历史消息必须先规整再渲染)、
记忆读写与版本历史、手动触发整理。

---

## 目录

1. [认证](#认证)
2. [运行时设置](#运行时设置) 🆕
3. [会话](#会话)
4. [聊天：SSE 流](#聊天sse-流)
5. [历史消息：必须先规整再渲染](#历史消息必须先规整再渲染) ← **最容易写错的地方**
6. [记忆](#记忆)
7. [任务](#任务)
8. [要做的三个页面](#要做的三个页面)
9. [TypeScript 类型](#typescript-类型)

---

## 认证

后端 `.env` 里 `API_KEY` 为空时不校验（本地开发默认如此）。若设置了，所有 `/api/*` 请求加头：

```
X-API-Key: <key>
```

放进 `.env.local` 的 `NEXT_PUBLIC_API_KEY`，封装一个 fetch wrapper 统一加上即可。

---

## 运行时设置

> 🆕 第 3 批新增

```http
GET /api/settings
```

```json
{
  "provider": "deepseek",
  "model": "deepseek-v4-flash",
  "thinking_default": true,
  "thinking_toggle": true
}
```

渲染思考开关前先读它。**不要在前端硬编码模型名或能力**——以后换模型、加模型，
后端改这个响应就够了，前端一行不用动。

- `thinking_default`：新会话默认思不思考
- `thinking_toggle`：当前模型支不支持关掉思考。为 `false` 时开关应置灰

---

## 会话

### 新建

```http
POST /api/conversations
```

```json
{
  "id": 3,
  "title": "新对话",
  "created_at": "2026-08-05T16:09:21.481523Z",
  "updated_at": "2026-08-05T16:09:43.126902Z"
}
```

标题初始是 `"新对话"`，首轮对话后由后端自动生成并通过 SSE 的 `title` 事件推回来。

### 列表

```http
GET /api/conversations?limit=50
```

返回上面那种对象的数组，**按 `updated_at` 倒序**。发消息会更新 `updated_at`，所以聊过的会话会自动冒到顶部——收到 SSE `done` 后重新拉一次列表即可。

### 删除

```http
DELETE /api/conversations/{id}   → 204
```

级联删除该会话的所有消息。**不可撤销**，前端请加二次确认。

### 修改会话

> 🆕 第 3 批新增

```http
PATCH /api/conversations/{id}
{"thinking": false}    # 这个会话不思考
{"thinking": null}     # 恢复跟随全局默认
{"title": "新标题"}    # 也能改标题
```

返回更新后的会话对象。

**思考开关是两层的**：

```
会话覆盖  conversations.thinking   ← 这个接口设置
   ↓ 为 null 时落到
全局默认  GET /api/settings 的 thinking_default
```

新建的会话 `thinking` 是 `null`（跟随全局）。UI 上建议做成三态或「跟随默认 / 开 / 关」，
不要做成二态——否则用户没法退回「跟随全局」。

**只传出现的字段**：只发 `{"title": ...}` 不会把 `thinking` 冲掉，反之亦然。

关掉思考的实际效果（实测同一个问题）：

| | 思考字符 | 输出 token |
|---|---|---|
| 开 | 83 | 48 |
| 关 | 0 | 2 |

适合简单问答提速；复杂问题建议留着。**DeepSeek 关掉思考后工具调用仍然正常**
（记忆读写不受影响），这点已实测确认。

### 归档

比删除温和的收纳方式，可逆。

```http
POST /api/conversations/{id}/archive                  # 归档
POST /api/conversations/{id}/archive?archived=false   # 取消归档
GET  /api/conversations?archived=true                 # 看归档列表
```

都返回会话对象。默认的 `GET /api/conversations` 只返回**未归档**的。

建议：会话右键菜单里「归档」放在「删除」上面，删除保留但加二次确认。

### 截断（重新生成/编辑重发）

```http
DELETE /api/conversations/{id}/messages?after={message_id}
→ {"deleted": 3}
```

删掉 **id 大于 `after`** 的所有消息。`after=0` 清空整个会话。

**这一个接口同时支撑「重新生成」和「编辑重发」**，用法是先截断再重发：

```ts
// 重新生成某条用户消息的回复
async function regenerate(conversationId: number, messages: ApiMessage[], target: ApiMessage) {
  const index = messages.findIndex((m) => m.id === target.id);
  // 截到目标消息「之前」那条 —— 目标消息本身也要删掉，因为 /api/chat 会重新追加它
  const after = index > 0 ? messages[index - 1].id : 0;

  await fetch(`/api/conversations/${conversationId}/messages?after=${after}`, { method: "DELETE" });
  await streamChat(conversationId, target.content.find((b) => b.type === "text")!.text, onEvent);
}

// 编辑重发：一模一样，只是把最后那个参数换成用户改过的新文本
```

两个注意点：

- **id 不连续**，不能用 `target.id - 1`，必须从列表里取前一条的 id
- 截断后历史可能留下没有配对结果的 `tool_use`，**后端加载时会自动补齐**，不会把会话截坏
- 不支持「只删中间某一条消息」——截断一定会删掉它后面的所有内容，这是对话模型的固有约束，UI 上要说清楚

---

## 聊天：SSE 流

```http
POST /api/chat
Content-Type: application/json

{"conversation_id": 3, "content": "记住：我用 uv 管理 Python 依赖，从不用 pip"}
```

响应是 `text/event-stream`，每个事件一行 `data: {...}`，事件之间空一行。

### 事件类型

| type | 字段 | 怎么处理 |
|---|---|---|
| `thinking_delta` | `text` | 追加到「思考中」折叠区。分片很碎，每个字都可能是一个事件 |
| `text_delta` | `text` | 追加到正文，逐字渲染 |
| `tool_use` | `name`, `input` | 显示状态条：「正在读取/更新记忆…」，`input.path` 是具体文件 |
| `tool_result` | `name`, `ok`, `summary` | 更新上面那条状态条；`ok=false` 显示为警告色但**不是错误**，模型通常会自行重试 |
| `title` | `title` | 更新侧边栏该会话的标题（仅首轮出现） |
| `done` | `usage` | 本轮结束 |
| `message_id` | `message_id` | 本轮最后一条消息的 id |
| `error` | `message` | 展示错误，结束本轮 |

### 顺序保证（可以依赖）

```
[thinking_delta ...] [tool_use → tool_result]* [text_delta ...] [title] done message_id
```

- `title` 一定在 `done` **之前**
- `done` 之后只可能再来一个 `message_id`
- `done` 整个流里只出现一次，可以当作终止信号
- 出错时只有 `error`，**不会有 `done`** —— 加载状态要在 `done` 或 `error` 任一到达时清除
- `tool_use`/`tool_result` 可以出现多组（模型能连续调多轮工具）

### 真实的一轮完整流（节选）

```
data: {"text": "The user is telling me a stable preference", "type": "thinking_delta"}
data: {"name": "memory", "input": {"command": "view", "path": "/memories/MEMORY.md"}, "type": "tool_use"}
data: {"name": "memory", "ok": false, "summary": "/memories/MEMORY.md 不存在", "type": "tool_result"}
data: {"name": "memory", "input": {"command": "create", "path": "/memories/profile/preferences.md", "file_text": "# 工具偏好\n\n- 用 uv 管理 Python 依赖，从不用 pip。\n"}, "type": "tool_use"}
data: {"name": "memory", "ok": true, "summary": "已创建 /memories/profile/preferences.md", "type": "tool_result"}
data: {"text": "记", "type": "text_delta"}
data: {"text": "下了", "type": "text_delta"}
data: {"type": "title", "title": "记住用uv管理Python依赖"}
data: {"usage": {"completion_tokens": 588, "prompt_tokens": 4688, "prompt_cache_hit_tokens": 3328}, "type": "done"}
data: {"type": "message_id", "message_id": 11}
```

注意第一次 `tool_result` 是 `ok: false` —— 模型查了个不存在的文件，然后**自己纠正**继续干活。这是正常流程，别当成失败。

### 消费 SSE

**不能用 `EventSource`**（它只支持 GET，这里是 POST）。用 `fetch` + `ReadableStream`：

```ts
export async function streamChat(
  conversationId: number,
  content: string,
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
) {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conversation_id: conversationId, content }),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`chat failed: ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    // 关键：网络分片会把一行从中间切开，必须缓冲到换行为止再解析。
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      onEvent(JSON.parse(line.slice(6)) as ChatEvent);
    }
  }
}
```

**踩坑提醒**：上面 `buffer` 那段不能省。中文字符和长的 `file_text` 极易被切开，直接 `JSON.parse` 每个 chunk 会随机抛异常。

用 `AbortController` 支持「停止生成」；abort 之后后端仍会把已完成的部分落库。

---

## 历史消息：必须先规整再渲染

```http
GET /api/conversations/{id}/messages
```

```json
[
  {"id": 6, "role": "user", "content": [...], "usage": null, "created_at": "..."},
  {"id": 7, "role": "assistant", "content": [...],
   "usage": {"prompt_tokens": 4688, "completion_tokens": 588,
             "prompt_cache_hit_tokens": 3328}, "created_at": "..."}
]
```

`usage` 只有 assistant 消息有，**字段名随 provider 不同**（DeepSeek 是 `prompt_tokens`，
Anthropic 是 `input_tokens`）。要展示的话用 [`/api/usage`](#用量统计) 的归一化结果更省事。

### 中断的回答

> 🆕 第 3 批新增

用户点停止、关标签页或断网时，**已经流出来的正文现在会被保存**（之前会全部丢失）。
这类消息的 `usage` 是一个固定标记：

```json
{"id": 42, "role": "assistant",
 "content": [{"type": "text", "text": "写到一半的内容…"}],
 "usage": {"interrupted": true}, "created_at": "..."}
```

判断方式：`message.usage?.interrupted === true`。建议在气泡末尾加一行淡色的
「回答被中断」，并给个「继续」按钮（重发一条「继续」即可）。

两个已知行为：

- **不保存半截的思考**，只保存正文。thinking 在 Anthropic 上必须带签名才能回传，
  半截的没有签名会让下一轮 400
- 一个字都没流出来就中断的话，**不会留下空的助手消息**

### ⚠️ 这里是最容易写错的地方

`content` 是模型 API 的原始 content block 数组。**直接按 `role` 渲染气泡是错的。**

上面那次真实对话，存下来是 6 条消息：

| id | role | content 里的块 | 实际是什么 |
|---|---|---|---|
| 6 | `user` | `[text]` | ✅ 真的用户消息 |
| 7 | `assistant` | `[thinking, tool_use]` | ⚠️ 没有正文，只是在调工具 |
| 8 | `user` | `[tool_result]` | ❌ **不是用户说的话**，是工具返回结果 |
| 9 | `assistant` | `[thinking, tool_use, tool_use]` | ⚠️ 同上 |
| 10 | `user` | `[tool_result, tool_result]` | ❌ 同上 |
| 11 | `assistant` | `[text]` | ✅ 真正的回复 |

照 `role` 直接渲染的话，用户会看到两条自己从没发过的消息，内容是「已创建 /memories/...」。

### 规整规则

```ts
export type Turn =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string; thinking: string; tools: ToolActivity[] };

export function toTurns(messages: ApiMessage[]): Turn[] {
  const turns: Turn[] = [];
  // 工具调用的入参和结果分散在两条消息里，用 id 配对。
  const toolInputs = new Map<string, { name: string; input: Record<string, unknown> }>();

  for (const m of messages) {
    const blocks = m.content;
    const isToolResultOnly =
      blocks.length > 0 && blocks.every((b) => b.type === "tool_result");

    // 1) 只含 tool_result 的 user 消息 → 不是用户发言，合并进上一条 assistant
    if (m.role === "user" && isToolResultOnly) {
      const last = turns.at(-1);
      if (last?.kind === "assistant") {
        for (const b of blocks) {
          if (b.type !== "tool_result") continue;
          const call = toolInputs.get(b.tool_use_id);
          last.tools.push({
            name: call?.name ?? "memory",
            input: call?.input ?? {},
            ok: !b.is_error,
            summary: typeof b.content === "string" ? b.content : "",
          });
        }
      }
      continue;
    }

    if (m.role === "user") {
      turns.push({
        kind: "user",
        text: blocks.filter((b) => b.type === "text").map((b) => b.text).join("\n"),
      });
      continue;
    }

    // 2) assistant：把连续的几条合并成一个回合，避免出现多个空气泡
    const text = blocks.filter((b) => b.type === "text").map((b) => b.text).join("");
    const thinking = blocks.filter((b) => b.type === "thinking").map((b) => b.thinking).join("");
    for (const b of blocks) {
      if (b.type === "tool_use") toolInputs.set(b.id, { name: b.name, input: b.input });
    }

    const last = turns.at(-1);
    if (last?.kind === "assistant") {
      last.text += text;
      last.thinking += thinking;
    } else {
      turns.push({ kind: "assistant", text, thinking, tools: [] });
    }
  }

  return turns;
}
```

### 验收样例

上面这段代码已经在真实数据上跑过，那 6 条消息的规整结果是：

```
6 条原始消息 → 2 个回合

[用户] 记住：我用 uv 管理 Python 依赖，从不用 pip
[助手] 正文: 记下了。以后涉及 Python 依赖一律用 uv，不会给你塞 pip 命令。
       思考: 1330 字符
       工具: 3 次
         ✗ view   /memories/MEMORY.md              → /memories/MEMORY.md 不存在
         ✓ create /memories/profile/preferences.md → 已创建 /memories/profile/preferences.md
         ✓ create /memories/MEMORY.md              → 已创建 /memories/MEMORY.md
```

你的实现应当得到相同结果。两个判据：**回合数是 2 不是 6**，**工具活动的 input 和 result 正确配对**（配对靠 `tool_use.id` ↔ `tool_result.tool_use_id`，它们在两条不同的消息里）。

这和流式时看到的应当**完全一致**——刷新页面不能让界面变样。

### content block 的形状

```jsonc
// text
{"type": "text", "text": "记下了。以后涉及 Python 依赖一律用 uv。"}

// thinking —— DeepSeek 无 signature 字段，Claude 有（前端都不用管）
{"type": "thinking", "thinking": "The user is telling me a stable preference..."}

// tool_use
{"type": "tool_use", "id": "call_00_X5Mz...", "name": "memory",
 "input": {"command": "create", "path": "/memories/profile/preferences.md", "file_text": "..."}}

// tool_result —— 出现在 role="user" 的消息里；is_error 仅在失败时存在
{"type": "tool_result", "tool_use_id": "call_00_X5Mz...",
 "content": "/memories/MEMORY.md 不存在", "is_error": true}
```

遇到未知的 `type` 直接跳过，不要报错——以后可能加新块类型。

---

## 记忆

路径参数是**不带 `/memories` 前缀**的相对路径：文件真实路径是 `/memories/profile/preferences.md`，请求 URL 写 `/api/memories/profile/preferences.md`。

### 树

```http
GET /api/memories
```

```json
[
  {"path": "/memories/MEMORY.md", "is_dir": false, "size": 70},
  {"path": "/memories/profile", "is_dir": true, "size": 0},
  {"path": "/memories/profile/preferences.md", "is_dir": false, "size": 37}
]
```

返回的是**扁平数组**，`path` 是全路径（带 `/memories` 前缀），目录节点由后端推导。前端自己按 `/` 切分建树。`MEMORY.md` 是索引文件，建议在树里置顶并特殊标记——它是模型每轮都会看到的那份摘要。

### 读 / 写 / 删

```http
GET    /api/memories/{path}                   → {path, content, created_at, updated_at}
PUT    /api/memories/{path}  {"content": "…"} → 覆盖写，返回同上
DELETE /api/memories/{path}                   → 204
```

- `PUT` 是整体覆盖，不是增量
- `DELETE` 作用在目录上会**递归删除**，务必确认
- 路径非法（如包含 `..`）返回 400，文件不存在返回 404

### 全局记忆变更

```http
GET /api/memories/versions?day=2026-08-06&actor=chat&limit=100
```

不带 path，返回**所有记忆文件**的变更流，按时间倒序。参数都可选。

```json
[
  {"id": 22, "path": "/memories/MEMORY.md", "content": "...",
   "operation": "modified", "actor": "chat", "created_at": "..."},
  {"id": 21, "path": "/memories/people/xiaoye.md", "content": "...",
   "operation": "created", "actor": "chat", "created_at": "..."},
  {"id": 20, "path": "/memories/scratch.md", "content": "临时",
   "operation": "deleted", "actor": "manual", "created_at": "..."}
]
```

两个用途：

1. **每日回顾页的主体**——「今天模型自己记了什么、改了什么」。按 `actor` 分组展示效果最好
2. **查看已删除记忆的唯一入口**。文件删掉后路径就不在树里了，
   下面那个按路径查的接口根本构造不出 URL。想做「恢复」按钮，
   就从这里拿 `content` 再 `PUT` 回去

### 恢复历史版本

> 🆕 第 3 批新增

```http
POST /api/memories/restore
{"version_id": 42}
```

返回恢复后的记忆对象。

**用 `version_id` 而不是路径定位**，这是有意的：删掉的记忆路径已经不在树里，
按路径根本构造不出请求。所以这个接口同时支撑两件事：

1. **回滚**：版本历史里每条加「恢复到此版本」
2. **找回误删**：从[全局记忆变更](#全局记忆变更)里挑 `operation: "deleted"` 的记录恢复

恢复本身也会记一条新版本（`actor: "manual"`），所以**回滚可以再回滚**，不会丢历史。

聊天页也建议接上：模型刚写完记忆的那条工具状态条，加个「撤销」——
从 `/api/memories/versions?limit=1` 拿到刚写的那条，取它前一个版本恢复即可。
模型记错东西时，这能把一次挫败变成一次点击。

### 版本历史（单个文件）

```http
GET /api/memories/{path}/versions?limit=50
```

```json
[
  {"id": 3, "path": "/memories/profile/preferences.md",
   "content": "…删除前的完整内容…",
   "operation": "deleted", "actor": "manual",
   "created_at": "2026-08-05T15:33:08.519822Z"}
]
```

**按时间倒序**。每次变更都留全量快照，所以：

- 取相邻两条做 diff（推荐 `diff` 或 `jsdiff`，按行渲染）
- `operation`：`created` | `modified` | `deleted`
- `actor`：`chat`（聊天中模型实时写的）| `consolidation`（每日整理写的）| `manual`（你在这个页面手动改的）—— **用不同颜色的 badge 区分，这是这个页面最有价值的信息**
- 已删除的记忆内容仍能从版本记录里取回，可以做「恢复」按钮（读旧版本 content → PUT 回去）

---

## 搜索

同时搜对话历史和记忆，一个接口。

```http
GET /api/search?q=杭州&limit=20
```

```json
{
  "query": "杭州",
  "conversations": [
    {"conversation_id": 12, "title": "杭州测试", "message_id": 41,
     "role": "user", "snippet": "我搬到杭州西湖区了", "matches": 2,
     "created_at": "..."}
  ],
  "memories": [
    {"path": "/memories/profile/location.md", "snippet": "# 居住地 住在杭州。"}
  ]
}
```

**按会话聚合**：一个会话里命中多条只返回一个结果，`matches` 是命中次数。
点进去用 `message_id` 定位到那条消息（滚动 + 高亮）。

几个行为要知道：

- **子串匹配，不分词**。搜「杭州」命中「我搬到杭州西湖区」✅；
  搜「运行」不会命中「跑」——没有同义词和词干还原
- **大小写不敏感**，中英文一视同仁
- **只搜正文**。模型的 thinking 和工具参数不进搜索——搜「杭州」不该命中模型的内部推理
- **查询短于 2 个字符直接返回空**。单字会命中几乎所有内容，没有意义
- `%` `_` 这些 LIKE 通配符**已在后端转义**，可以放心把用户输入直接传进来。
  搜「76%」能正常命中含百分号的内容

`snippet` 是命中位置前后各 40 字符的片段，多余空白已折叠，超出部分用 `…` 标记。
**后端不标记高亮位置**——前端在片段里自己找 `query` 做高亮即可（大小写不敏感）。

### UI 建议

顶部一个搜索框（`Cmd+K` 唤起最好），下拉分两组显示「对话」和「记忆」。
输入防抖 300ms，少于 2 个字符不发请求。

## 记忆使用率

```http
GET /api/memories/stats?days=30&top=10
```

一次返回仪表盘要的全部数据，前端一个 loading 态就够。

```json
{
  "total_memories": 5, "total_reads": 12, "total_writes": 14,
  "never_read": 2, "missed_reads": 1,
  "daily":    [{"day": "2026-08-01", "reads": 0, "writes": 0}, ...],
  "top":      [{"path": "/memories/projects/chat.md", "reads": 8, "writes": 3,
                "idle_days": 0, "content_chars": 1240, "last_read_at": "...",
                "created_at": "..."}],
  "unused":   [{"path": "/memories/notes/old.md", "reads": 0, "writes": 1,
                "idle_days": null, "content_chars": 2100, ...}],
  "by_actor": [{"actor": "chat", "reads": 10, "writes": 12},
               {"actor": "consolidation", "reads": 2, "writes": 0},
               {"actor": "manual", "reads": 0, "writes": 2}]
}
```

### ⚠️ 怎么正确解读这些数字

**`reads` 统计的是模型主动 `view` 打开文件的次数，不是「这条记忆被用上的次数」。**

因为索引（`MEMORY.md`）每轮都会全量注入 system prompt，里面每条记忆都有一行摘要。
短事实靠摘要就答完了——实测中模型回答「我住在哪个城市」时，
索引里「住在杭州」这一行就够了，**根本没有打开 `location.md`**。

所以：

| 现象 | 含义 |
|---|---|
| `reads` 高 | 这条内容有深度，模型经常需要细节 ✅ |
| `reads = 0` + 内容短 | **正常**，索引摘要已经够用，不是浪费 |
| `reads = 0` + 内容长 | ⚠️ **噪音候选**：写了一大堆细节却从没用到 |
| `missed_reads` 高 | ⚠️ 索引和实际内容对不上，模型在找不存在的文件 |

`unused` 数组**已经按 `content_chars` 倒序**排好了，越靠前越可疑。
UI 上别写「未使用」，那是误导——写「未展开细节」，长的那几条才标黄。

### 图表建议

| 图 | 数据 | 说明 |
|---|---|---|
| 折线图 | `daily`（读/写双线） | 已按日期升序、**无空洞**（没活动的天返回 0），直接喂给图表库 |
| 横向条形图 | `top` | 最常被展开的记忆 Top N |
| 堆叠条 / 饼图 | `by_actor` | `chat` 实时写的 / `consolidation` 整理的 / `manual` 你手改的 |
| 列表 + 警示色 | `unused` | 按长度倒序，长的标黄并给「删除」快捷入口 |
| 数字卡片 | 顶部四个总数 | `missed_reads > 0` 时标红，提示去修索引 |

放在记忆管理页顶部，或者单开一个 `/memories/stats` 标签页。

### 埋点范围（避免误解）

- 只有**模型**调 `view` 才记数（`actor` 为 `chat` / `consolidation`）
- **你在记忆页翻看不计入**——否则你自己点几下就把统计刷满了
- 写操作（create/str_replace/insert）不算读

## 会话摘要

每日整理任务生成的「这个会话聊了什么」。

```http
GET /api/summaries?day=2026-08-06     # 按天
GET /api/summaries?conversation_id=3  # 按会话
GET /api/summaries?limit=20           # 最近的
```

```json
[
  {"id": 2, "conversation_id": 11, "conversation_title": "记住用户名字",
   "summary": "用户的名字是 lance（此前误写为 lan'ce，已更正）。",
   "created_at": "2026-08-06T01:12:03.884Z"}
]
```

`conversation_title` 已经 join 好了，不用再逐个去查会话。

**摘要不是实时的**——只有跑过整理任务（凌晨 4 点自动，或手动触发）的那天才有。
当天还没整理过时返回空数组，前端要给个「今天还没整理」的空状态 + 触发按钮。

## 用量统计

```http
GET /api/usage?days=7
```

```json
[
  {"day": "2026-08-06", "messages": 16, "input_tokens": 28668,
   "output_tokens": 1694, "cached_tokens": 21888},
  {"day": "2026-08-05", "messages": 0, "input_tokens": 0,
   "output_tokens": 0, "cached_tokens": 0}
]
```

**字段已经跨 provider 归一化过了**，不用自己处理 `prompt_tokens` / `input_tokens` 的差异。
按天倒序，没有数据的日子也会返回一行 0（方便直接画图，不用补空洞）。

`cached_tokens` 占 `input_tokens` 的比例就是缓存命中率——上面这天是 76%，
这个数掉下去通常意味着 system prompt 里混进了变动内容。

## 任务

```http
POST /api/jobs/consolidate            # 整理今天
POST /api/jobs/consolidate?day=2026-08-05
```

```json
{
  "date": "2026-08-06",
  "summarized_conversations": 1,
  "tool_calls": 2,
  "memory_writes": 0,
  "skipped": false,
  "failed_summaries": 0,
  "detail": ""
}
```

**这个接口很慢（10 秒起，可能几十秒），要有 loading 状态。**

> 🆕 第 3 批：**自动每日整理已默认关闭**（定时器在进程重启或机器休眠后很容易整天不触发，
> 不如手动可靠）。所以这个接口现在是整理记忆的**唯一入口**，
> 每日回顾页的触发按钮比之前更重要，建议放显眼位置。

- `memory_writes: 0` **不是失败**——表示模型看过之后认为现有记忆已经准确，无需改动
- `skipped: true` 表示当天没有值得沉淀的对话，此时看 `detail`
- `failed_summaries > 0` 表示部分摘要生成失败，详情在后端日志

---

## 要做的三个页面

### 1. 聊天页 `/`

左侧会话列表 + 右侧消息流的经典布局。

- 消息流用上面的 `toTurns()` 规整后渲染
- **思考区默认折叠**，标题写「思考中…」/ 完成后「思考过程」，点开显示全文。思考内容可能很长（实测几百字）且常是英文，别默认展开
- 工具活动渲染成内联的窄状态条，不要做成气泡。文案按 `input.command` 区分：`view` → 「查阅记忆 <path>」，`create`/`str_replace`/`insert` → 「更新记忆 <path>」，`delete` → 「删除记忆 <path>」
- 正文用 `react-markdown` + `remark-gfm`，代码块用 `shiki` 高亮
- 流式时自动滚到底，但**用户手动上滚后要停止自动滚动**
- 发送中禁用输入框，提供「停止」按钮（AbortController）

### 2. 记忆管理页 `/memories`

**这页是这个项目区别于普通聊天 UI 的地方，值得多花时间。**

左树右编辑器：

- 左侧文件树，`MEMORY.md` 置顶
- 右侧 Markdown 编辑器（`@uiw/react-md-editor` 或纯 textarea + 预览切换均可），保存走 `PUT`
- 底部/侧边 tab 显示版本历史，选两个版本做 diff
- 版本条目上用 badge 标出 `actor`，一眼能看出「这条是模型自己记的还是我改的」
- 删除要二次确认，目录删除要额外警告会递归

### 3. 每日回顾页 `/review`

**这页现在才真正能做**——之前后端没有任何接口能读出摘要。一个日期选择器，下面三块：

| 区块 | 数据源 | 说明 |
|---|---|---|
| 今天聊了什么 | `GET /api/summaries?day=` | 每个会话一张卡片，点进去跳到聊天页 |
| 记忆变更了什么 | `GET /api/memories/versions?day=` | 按 `actor` 分组：模型实时记的 / 整理任务改的 / 你手动改的 |
| 用量 | `GET /api/usage?days=7` | 一条七日走势线，标注缓存命中率 |

顶部放「重新整理这一天」按钮（`POST /api/jobs/consolidate?day=`）。**这个请求很慢（10 秒起）**，
要有 loading，完成后刷新上面三块。

摘要为空是常态（当天还没整理过），别当错误处理——给个「今天还没整理」+ 触发按钮的空状态。

记忆变更那块建议做成时间线，每条显示 `operation` 图标 + 路径 + `actor` badge，
点开展开内容 diff（和记忆管理页复用同一个 diff 组件）。已删除的记忆也在这里，
可以给个「恢复」按钮：拿该版本的 `content` 去 `PUT` 回原路径。

### 聊天页要补的交互

新接口解锁的：

- **重新生成** / **编辑重发**：见[截断](#截断重新生成编辑重发)，一个接口两用
- **归档**：会话右键菜单，比删除温和且可逆
- **本轮用量**：`done` 事件里就有 `usage`，可以在消息末尾显示一个淡淡的 token 数

---

## TypeScript 类型

```ts
export interface Conversation {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
  /** 🆕 第 3 批：null = 跟随 RuntimeSettings.thinking_default */
  thinking: boolean | null;
}

/** 🆕 第 3 批 */
export interface RuntimeSettings {
  provider: string;
  model: string;
  thinking_default: boolean;
  /** false 时思考开关应置灰 */
  thinking_toggle: boolean;
}

export type ContentBlock =
  | { type: "text"; text: string }
  | { type: "thinking"; thinking: string; signature?: string }
  | { type: "tool_use"; id: string; name: string; input: Record<string, unknown> }
  | { type: "tool_result"; tool_use_id: string; content: string; is_error?: boolean };

export interface ApiMessage {
  id: number;
  role: "user" | "assistant";
  content: ContentBlock[];
  /** 仅 assistant 消息有；字段名随 provider 不同，展示优先用 /api/usage。
   *  🆕 第 3 批：中断的回答这里是 {interrupted: true} */
  usage: (Record<string, number> & { interrupted?: boolean }) | null;
  created_at: string;
}

export interface ConversationSummary {
  id: number;
  conversation_id: number;
  conversation_title: string;
  summary: string;
  created_at: string;
}

export interface SearchResults {
  query: string;
  conversations: {
    conversation_id: number;
    title: string;
    /** 用它定位并高亮到具体那条消息 */
    message_id: number;
    role: "user" | "assistant";
    snippet: string;
    /** 该会话里的命中次数 */
    matches: number;
    created_at: string;
  }[];
  memories: { path: string; snippet: string }[];
}

export interface MemoryUsage {
  path: string;
  reads: number;
  writes: number;
  last_read_at: string | null;
  created_at: string;
  /** 距上次被展开的天数；null = 从未展开 */
  idle_days: number | null;
  /** 判断噪音要结合它看：短记忆没被展开是正常的 */
  content_chars: number;
}

export interface MemoryStats {
  total_memories: number;
  total_reads: number;
  total_writes: number;
  never_read: number;
  missed_reads: number;
  daily: { day: string; reads: number; writes: number }[];
  top: MemoryUsage[];
  unused: MemoryUsage[];
  by_actor: { actor: string; reads: number; writes: number }[];
}

export interface DailyUsage {
  day: string;
  messages: number;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
}

export type ChatEvent =
  | { type: "thinking_delta"; text: string }
  | { type: "text_delta"; text: string }
  | { type: "tool_use"; name: string; input: Record<string, unknown> }
  | { type: "tool_result"; name: string; ok: boolean; summary: string }
  | { type: "title"; title: string }
  | { type: "done"; usage: Record<string, number> }
  | { type: "message_id"; message_id: number }
  | { type: "error"; message: string };

export interface ToolActivity {
  name: string;
  input: Record<string, unknown>;
  ok: boolean;
  summary: string;
}

export interface MemoryNode {
  path: string;
  is_dir: boolean;
  size: number;
}

export interface Memory {
  path: string;
  content: string;
  created_at: string;
  updated_at: string;
}

export interface MemoryVersion {
  id: number;
  path: string;
  content: string;
  operation: "created" | "modified" | "deleted";
  actor: "chat" | "consolidation" | "manual";
  created_at: string;
}
```

---

## 本地起后端

后端跑在 Docker 里，一条命令：

```bash
docker compose up -d --build     # db + api，迁移自动执行
curl localhost:8000/health       # {"status":"ok","provider":"deepseek",...}
```

已经在跑的话不用管。看日志 `docker compose logs -f api`。

后端 CORS 默认放行 `http://localhost:3000`，改端口的话同步改后端 `.env` 的 `CORS_ORIGINS`。

`GET /health` 可以用来做前端启动时的连通性检查。完整接口列表见 `http://localhost:8000/docs`（FastAPI 自动生成）。
