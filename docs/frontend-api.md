# 前端对接文档

给写前端的人/agent 看的。后端已经跑通并实测过，本文档里的所有 JSON 都是**真实响应**，不是示意。

- Base URL：宿主机直连默认为 `http://localhost:18000`；Compose 前端使用同源 `/backend` 代理
- 技术栈建议：Next.js 15 (App Router) + TypeScript + Tailwind + shadcn/ui
- 前端**不直接调任何模型 API**，一律走后端

## 更新日志

按批次记录，**新的在最上面**。每批只列「相对上一批的变化」，方便对照着做增量。
详细说明都在下面对应章节里。

### 第 7 批 · 后端已就绪，前端待做 🚧

Obsidian 知识库（只读）接入：`.env` 配置 `VAULT_PATH` 后模型多了四个 `kb_*` 工具。
接口无破坏性变化 —— 不适配也能用，只是 kb 工具的状态条文案会难看。

| 变化 | 位置 | 前端要做什么 |
|---|---|---|
| `tool_use.name` 新增四种 `kb_*` | [知识库工具](#知识库工具kb_) | `frontend/lib/turns.ts` 的 `toolLabel()` 给 kb 工具配中文文案（kb 的 `input` **没有 `command` 字段**，按 `name` 分支） |
| 工具组标题写死「记忆操作」 | [知识库工具](#知识库工具kb_) | `frontend/components/chat-page.tsx` 的 `ToolActivityGroup`：组内可能混着记忆和知识库操作，标题改成按内容区分（全 kb → 「知识库查询」，混合 → 「工具操作」） |
| `GET /api/settings` 新增 `kb_enabled` | [运行时设置](#运行时设置) | 可选：设置页运行时卡片显示「知识库：已挂载/未启用」。**不是** `fields` 里的可写项，别渲染成表单 |

### 第 6 批 · 后端已就绪，前端待做 🚧

这一批全是**性能**，接口没有破坏性变化。核心是 `auto` 模式别再等整段合成完。

| 变化 | 位置 | 前端要做什么 |
|---|---|---|
| `POST /api/tts/next` | [边写边读](#边写边读句级流水线) | **`auto` 模式改走这条**：边流式边按句合成、排队播放。首声从「等全程」变成 1～2 秒 |
| `POST /api/tts/stop` | [边写边读](#边写边读句级流水线) | 用户按停止时调，丢掉队列里没播的句子 |
| `POST /api/tts/warmup` | [边写边读](#边写边读句级流水线) | 一般不用调（启动时自动预热）。状态灯由离线变在线时可以调一次 |
| `POST /api/tts/prepare` | [语音播放](#语音播放) | **`manual` 的播放按钮建议也从 `/speech` 换成它** —— 返回一个 URL 喂 `<audio src>`，边下边播，首字节 6.97s → 1.12s |
| 设置项新增 `tts_warmup` | [运行时设置](#运行时设置) | 按 `fields` 自动渲染，无需特殊处理 |

⚠️ **`POST /api/tts/speech` 没有废弃**，但它会等整段合成完才返回，
现在只推荐用于「重播一条已有消息」（要 blob 做缓存的场景）。

### 第 5 批 · 后端已就绪，前端待做 🚧

| 变化 | 位置 | 前端要做什么 |
|---|---|---|
| `POST /api/tts/speech` | [语音播放](#语音播放) | 拿到音频 blob 直接喂 `<audio>`，做消息旁的播放按钮 |
| `GET /api/tts/status` | [语音播放](#语音播放) | 设置页显示「语音服务在线/离线」，附试听 |
| 设置项新增 `tts_*` 九项 | [运行时设置](#运行时设置) | 按 `fields` 自动渲染即可，**不用硬编码** |
| `fields[].group` 新字段 | [运行时设置](#运行时设置) | `"tts"` 归「语音」分区，`"debug"` 归「调试」，`""` 维持现状 |
| `GET /api/debug/prompt` | [调试](#调试看清每次发了什么) | 显示当前 system prompt 原文 |
| `GET /api/debug/requests` | [调试](#调试看清每次发了什么) | 调试面板：最近发给模型的请求 |
| 设置项新增 `debug_prompts` | [调试](#调试看清每次发了什么) | 开关，控制上面那个列表记不记 |
| 设置项新增 `custom_instructions` | [自定义指令](#自定义指令) | 多行文本框，用户手写、直接进 system prompt |
| `fields[].kind` 新增 `"text"` | [运行时设置](#运行时设置) | 渲染成 `<textarea>`，校验规则和 `str` 完全一样 |
| `owner_name` 的 `group` 变成 `"prompt"` | [运行时设置](#运行时设置) | 和自定义指令归到同一个「人格与指令」分区 |

**核心开关是 `tts_mode`**，三档：`off` 只出文字（默认）/ `manual` 消息旁给播放按钮 /
`auto` 回答完自动朗读。这是「支持语音播放或者文字播放」的落点，全部逻辑挂在这一个值上。

### 第 4 批 · 已实现 ✅

| 变化 | 位置 | 前端要做什么 |
|---|---|---|
| `GET /api/settings` **响应扩展** | [运行时设置](#运行时设置) | 设置页从只读变成可编辑；按 `fields` 渲染表单，按 `sources` 标出「已修改」 |
| `PATCH /api/settings` | [修改设置](#修改设置) | 改完**立刻生效，不用重启**；传 `null` 恢复默认 |
| `POST /api/jobs/backup` | [备份](#备份) | 设置页加「立即备份」按钮 |
| 并发被拒的新错误 | [聊天 SSE](#聊天sse-流) | 同一会话正在生成时再发会收到 `error`，提示用户等待即可 |

**行为变化**：同一会话不再允许并发生成（之前会让历史错乱）。前端发送时禁用按钮即可，
真撞上了会收到 `error` 事件而不是坏数据。

**这批里还没做的**：`env_only` 字段的只读展示（后端已在响应里返回，设置页当前直接跳过了，
用户看不到「哪些配置只能改 .env」）。

### 第 3 批 · 已实现 ✅

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
2. [运行时设置](#运行时设置)
3. [会话](#会话)
4. [聊天：SSE 流](#聊天sse-流)
5. [历史消息：必须先规整再渲染](#历史消息必须先规整再渲染) ← **最容易写错的地方**
6. [记忆](#记忆)
7. [语音播放](#语音播放)
8. [自定义指令](#自定义指令)
9. [调试：看清每次发了什么](#调试看清每次发了什么)
10. [任务](#任务)
11. [四个页面](#四个页面)
12. [TypeScript 类型](#typescript-类型)

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

> 🆕 第 4 批：响应扩展成完整描述（`fields` / `sources` / `providers` / `env_only`），
> 设置页据此做成可编辑表单。

```json
{
  "values":  {"owner_name": "用户", "provider": "deepseek",
              "deepseek_model": "deepseek-v4-flash", "consolidate_hour": 4, "...": "..."},
  "sources": {"owner_name": "db", "consolidate_hour": "env"},
  "fields": [
    {"key": "owner_name", "label": "助手怎么称呼你", "kind": "str",
     "choices": [], "minimum": 1, "maximum": 32, "provider": "", "group": "prompt"},
    {"key": "custom_instructions", "label": "自定义指令", "kind": "text",
     "choices": [], "minimum": null, "maximum": 4000, "provider": "", "group": "prompt"},
    {"key": "effort", "label": "推理强度", "kind": "enum",
     "choices": ["low","medium","high","xhigh","max"], "provider": "anthropic",
     "group": ""},
    {"key": "tts_mode", "label": "语音播放", "kind": "enum",
     "choices": ["off","manual","auto"], "provider": "", "group": "tts"}
  ],
  "providers": [
    {"value": "deepseek",  "available": true,  "reason": ""},
    {"value": "anthropic", "available": false, "reason": "未配置 ANTHROPIC_API_KEY"}
  ],
  "env_only": ["database_url", "api_key", "cors_origins", "..."],

  "provider": "deepseek", "model": "deepseek-v4-flash",
  "thinking_default": true, "thinking_toggle": true,
  "kb_enabled": false
}
```

> 🆕 第 7 批：`kb_enabled` 表示 Obsidian 知识库是否挂载（`.env` 的 `VAULT_PATH`，
> 设置页**改不了**，纯只读状态位）。为 `true` 时聊天流里会出现 `kb_*` 工具事件，
> 见 [知识库工具](#知识库工具kb_)。

**照着 `fields` 渲染表单，不要硬编码字段清单**——以后后端加配置项，前端不用改：

| 字段 | 用途 |
|---|---|
| `values` | 当前**生效值**（数据库覆盖已叠加在 .env 之上） |
| `sources` | `db` = 你在界面上改过，`env` = 来自 .env 默认。据此显示「已修改」标记和「恢复默认」按钮 |
| `fields[].kind` | `str` / `text` / `int` / `bool` / `enum`，决定用输入框、多行文本框、数字框、开关还是下拉。🆕 `text` 的校验规则和 `str` 完全一样，只是提示前端用 `<textarea>` |
| `fields[].minimum/maximum` | 数字是范围，字符串是长度。前端先校验一次，后端仍会再验 |
| `fields[].provider` | 非空表示只在该 provider 下有意义（如 `effort` 只对 anthropic），可按当前 provider 过滤或折叠 |
| `fields[].group` | 🆕 第 5 批。界面分区：`""` = 模型与整理，`"prompt"` = 人格与指令，`"tts"` = 语音，`"debug"` = 调试。按它分组渲染，以后加分区前端不用动 |
| `providers[].available` | `false` 时该选项置灰并显示 `reason` |
| `env_only` | 这些只能改 `.env`，**界面上不要给入口**（密钥、数据库、CORS、日志） |

末尾四个平铺字段是给运行时卡片用的，保持不变。

### 修改设置

> 🆕 第 4 批新增

```http
PATCH /api/settings
{"owner_name": "阿明"}        # 改一项
{"owner_name": null}          # 恢复 .env 默认
{"consolidate_hour": 6, "deepseek_thinking": false}   # 一次改多项
```

返回和 `GET` 相同结构的完整描述，前端直接拿去刷新界面。

- **改完立刻生效，不需要重启容器**。每个请求都会重新解析配置
- **部分更新**：只传要改的字段，其余不受影响
- 非法值返回 400，`detail` 是可以直接展示给用户的中文说明，例如
  「自动整理时间（点）不能大于 23」「未配置 ANTHROPIC_API_KEY，无法切换到该 provider」
- 写 `env_only` 里的字段一律 400。这是有意的：改坏 `api_key` 或 `cors_origins`
  会把设置页自己锁在门外

### 备份

> 🆕 第 4 批新增

```http
POST /api/jobs/backup
```

```json
{"dump_file": "chat-20260806-075235.dump", "dump_bytes": 27012,
 "memory_files": 5, "memory_dir": "/backups/memories",
 "created_at": "20260806-075235", "detail": ""}
```

生成两份东西，落在宿主机的 `backups/`：

- **`.dump`** —— `pg_dump` 全量快照，能用 `pg_restore` 完整恢复（对话、记忆、版本历史、埋点）
- **`memories/`** —— 记忆导出成真实的 `.md` 文件树，可读、可 `grep`、可以 `git` 管理

`detail` 非空表示 dump 那部分出了问题（例如镜像里没装 `pg_dump`），此时记忆文件仍然导出成功。

设置页放一个「立即备份」按钮，成功后显示文件名和大小即可。**这个操作会阻塞几秒**，要有 loading。

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
| `tool_use` | `name`, `input` | 显示状态条。`name` 可能是 `memory`，也可能是（🆕 第 7 批）`kb_search` / `kb_read` / `kb_list` / `kb_backlinks`，各自的 `input` 结构见 [知识库工具](#知识库工具kb_) |
| `tool_result` | `name`, `ok`, `summary` | 更新上面那条状态条；`ok=false` 显示为警告色但**不是错误**，模型通常会自行重试 |
| `title` | `title` | 更新侧边栏该会话的标题（仅首轮出现） |
| `done` | `usage` | 本轮结束 |
| `message_id` | `message_id` | 本轮最后一条消息的 id |
| `error` | `message` | 展示错误，结束本轮 |

> 🆕 第 4 批：同一会话**正在生成时再发**，会立刻收到
> `error`「该会话正在生成中，请等当前回答结束」。这是有意的——
> 并发跑同一个会话会让历史错乱。前端发送时禁用按钮即可，这里是兜底。

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

### 知识库工具（kb_*）

> 🆕 第 7 批。仅当 `GET /api/settings` 的 `kb_enabled` 为 `true`（`.env` 配置了
> `VAULT_PATH`，Obsidian vault 只读挂载）时才会出现。

四个只读工具，`tool_use.input` 的结构：

| name | input 字段 | 建议的状态条文案 |
|---|---|---|
| `kb_search` | `query`（必有）, `path_prefix?`, `limit?` | 搜索知识库「{query}」 |
| `kb_read` | `path`（必有，vault 相对路径）, `view_range?` | 查阅笔记 {path} |
| `kb_list` | `path?`（空/缺省 = 根目录） | 浏览知识库 {path ?? "/"} |
| `kb_backlinks` | `path`（必有） | 查找 {path} 的反向链接 |

和 memory 工具的两个区别：**没有 `command` 字段**（操作语义在 `name` 里）；
`path` 是 vault 相对路径（`Projects/chat-memo.md`），不带 `/memories` 前缀。
`tool_result` 结构不变（`name` / `ok` / `summary`，`summary` 后端已截断到 200 字符）。

样例事件：

```
data: {"name": "kb_search", "input": {"query": "手冲咖啡"}, "type": "tool_use"}
data: {"name": "kb_search", "ok": true, "summary": "搜到 1 条：\n- 咖啡笔记.md  (2026-08-06)\n  手冲参数：15g 粉，1:15 粉水比，92 度。", "type": "tool_result"}
data: {"name": "kb_read", "input": {"path": "咖啡笔记.md"}, "type": "tool_use"}
data: {"name": "kb_read", "ok": true, "summary": "咖啡笔记.md:\n1\t---\n2\ttags: [hobby]…", "type": "tool_result"}
```

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
GET /api/search?q=依赖&limit=20
```

```json
{
  "query": "依赖",
  "conversations": [
    {"conversation_id": 12, "title": "依赖管理", "message_id": 41,
     "role": "user", "snippet": "我用 uv 管理 Python 依赖", "matches": 2,
     "created_at": "..."}
  ],
  "memories": [
    {"path": "/memories/profile/preferences.md", "snippet": "# 工具偏好 用 uv 管理依赖"}
  ]
}
```

**按会话聚合**：一个会话里命中多条只返回一个结果，`matches` 是命中次数。
点进去用 `message_id` 定位到那条消息（滚动 + 高亮）。

几个行为要知道：

- **子串匹配，不分词**。搜「依赖」命中「我用 uv 管理 Python 依赖」✅；
  搜「运行」不会命中「跑」——没有同义词和词干还原
- **大小写不敏感**，中英文一视同仁
- **只搜正文**。模型的 thinking 和工具参数不进搜索——搜「依赖」不该命中模型的内部推理
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
短事实靠摘要就答完了——实测中模型回答「我用什么管理依赖」时，
索引里「用 uv 管理依赖」这一行就够了，**根本没有打开 `preferences.md`**。

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

---

## 语音播放

> 🆕 第 5 批新增，**后端已就绪，前端待做**

回答可以只出文字，也可以念出来。语音由跑在宿主机上的本地 TTS 服务（mlx-audio）合成，
**前端不直连它，统一走后端代理** —— 配置只有一份、不用给 TTS 服务额外配 CORS、
"要不要开、念多长"是服务端策略。

### 一个开关决定全部行为

`tts_mode`（在 `GET /api/settings` 的 `values` 里）：

| 值 | 前端行为 |
|---|---|
| `off`（默认） | 纯文字。**不要显示任何播放按钮**，调 `/speech` 会返回 409 |
| `manual` | 每条 assistant 消息旁给一个播放按钮，点了才合成 |
| `auto` | 收到 SSE `done` 后自动合成并播放本轮回答，同时保留播放按钮供重播 |

改这个值走标准的 `PATCH /api/settings`，和其他配置项一样，改完立刻生效。

### 合成

```http
POST /api/tts/speech
{"text": "## 今天的安排\n- **上午**跑测试"}
```

响应**不是 JSON，是音频二进制**（默认 `Content-Type: audio/mpeg`）：

```ts
const resp = await fetch(`${API}/api/tts/speech`, {
  method: "POST",
  headers: { "Content-Type": "application/json", ...authHeaders },
  body: JSON.stringify({ text: message.text }),
});
if (!resp.ok) throw new Error((await resp.json()).detail);   // 错误时才是 JSON

const url = URL.createObjectURL(await resp.blob());
audioRef.current.src = url;
await audioRef.current.play();
// 播完记得 URL.revokeObjectURL(url)，否则 blob 一直占着内存
```

请求体：

| 字段 | 说明 |
|---|---|
| `text` | **直接传原始 Markdown**，不要自己 strip。清洗在服务端做（代码块整段丢掉、标题井号/列表符号/强调星号去掉但保留文字、链接只念文案），前端渲染用的和朗读用的是两套文本，各 strip 一遍迟早不一致 |
| `voice` | 可选，临时覆盖当前音色，**不写库**。给设置页的「试听」用 |
| `instruct` | 可选，同上，临时覆盖语气指令 |
| `truncate` | 可选，默认 `true`，按 `tts_max_chars` 截断（会断在最近的句末）。想念全文传 `false` |

几个要点：

- **音频不落库也不落盘**，每次都是现合成。内容本来就在 `messages` 里，
  前端想避免重复合成就自己按 `message_id` 缓存 blob URL
- **合成是串行的**（MLX 一次只加载一份权重，并发只会互相拖慢）。
  连点播放会排队，按钮要进 loading 态
- **慢**。本地模型合成几百字要几秒到十几秒，超时上限是 `tts_timeout`（默认 180 秒）。
  必须有加载指示，别让用户以为卡死了
- 错误码：`409` = `tts_mode` 是 `off`；`502` = 连不上 TTS 服务或它报错，
  `detail` 是可以直接展示的中文说明（例如「连不上语音服务 http://127.0.0.1:8001：...」）

⚠️ **这个接口会等整段合成完才返回**，只适合「点按钮重播一条已有消息」。
`auto` 模式下用它，用户要等 `LLM 全程 + TTS 全程` 两段串起来。
自动朗读请走下面的[句级流水线](#边写边读句级流水线)。

### 边下边播（`/prepare`）

`/speech` 要等整段音频做完；想让浏览器**边下边播**，用这个：

```http
POST /api/tts/prepare        # 请求体和 /speech 完全一样
{"text": "## 今天的安排\n- **上午**跑测试"}
```

```json
{"url": "/api/tts/stream/8f2c….mp3", "expires_in": 900}
```

```ts
const { url } = await prepareSpeech({ text: message.text });
audio.src = API_BASE + url;   // 直接喂，不要 fetch 成 blob
await audio.play();
```

这一步**不合成**，只登记文本，所以是即时返回的；真正的合成发生在浏览器 GET
那个 URL 的时候，音频边做边发。实测同一段话首字节 **6.97s → 1.12s**。

为什么要绕这一道：只有 `<audio src>` 这条路径能渐进播放，而它是浏览器自己发的 GET，
**带不了 `X-API-Key`，也带不了 POST body**。所以把文本换成一个一次性令牌放进 URL，
`GET /api/tts/stream/{token}` 不校验 API key —— 令牌本身就是凭证：
32 位随机、用一次即失效、900 秒过期。

代价是**不能重播**：令牌消费掉就没了，再播要重新 `/prepare`。
所以想按 `message_id` 缓存 blob 复用的场景，仍然用 `/speech`。

### 边写边读（句级流水线）

> 🆕 第 6 批新增。**后端已就绪，前端待做** —— 这是 `auto` 模式该走的路径

不等回答写完。模型每吐出一句完整的话，就把这句拿去合成、排进播放队列。
用户听到第一声的时间从 `LLM 全程 + TTS 全程` 变成 `首句 LLM + 首句 TTS`，
后面的句子在听前一句时就做好了。

```http
POST /api/tts/next
{"text": "到目前为止的累计全文", "cursor": 0, "flush": false}
```

```json
{"url": "/api/tts/stream/8f2c….mp3", "text": "这个问题有点复杂，", "cursor": 9, "expires_in": 900}
```

| 字段 | 说明 |
|---|---|
| `text` | **累计全文，不是增量**。原始 Markdown，照旧不用自己 strip |
| `cursor` | 上次返回的 `cursor`，第一次传 `0`。**原样传回来即可**，不用理解它的含义（它是清洗后文本的偏移，和你手上的 Markdown 下标对不上） |
| `flush` | 流结束时传 `true`，把剩下的尾巴全念掉。**不传就会丢最后半句** |
| 返回 `url` | `null` 表示还凑不出一句完整的话 —— **不是错误**，什么都别做，等下一批增量再问 |

**切句和清洗都在服务端**，前端不要自己按标点切：朗读用的文本经过清洗（去代码块、
去 Markdown 符号），你手上的 Markdown 切出来的位置和它对不上，两套规则迟早跑偏。
规则本身也有讲究，都在 `app/tts/segment.py` 里：第一句可以断在逗号上（早一秒出声就少一秒干等），
后面的句子只在句末断（用户还在听前一句，有的是时间，连贯更重要）；
代码围栏只开了一半时那段一律不切，不会把 ``` 念出来。

前端要做的只有三件事：**每收到一批增量调一次，有 `url` 就入队，播完一个播下一个**。

```ts
const queue: string[] = [];
let cursor = 0, playing = false;

async function pump(text: string, flush = false) {
  // 一直问到问不出为止 —— 一批增量里可能同时凑齐了好几句
  for (;;) {
    const r = await ttsNext({ text, cursor, flush });
    cursor = r.cursor;
    if (!r.url) break;
    queue.push(API_BASE + r.url);
    if (!playing) void playNext();
    if (flush) break;          // flush 一次就把尾巴清空了
  }
}

async function playNext() {
  const url = queue.shift();
  if (!url) { playing = false; return; }
  playing = true;
  audio.src = url;             // 直接喂 <audio>，不要 fetch 成 blob
  await audio.play();
}
// audio.onended = () => void playNext();

// 在 SSE 的 text_delta 回调里（建议节流到 ~300ms 一次，别每个 delta 都发）
void pump(accumulatedText);
// 收到 done 时
await pump(accumulatedText, true);
```

几个必须注意的点：

- **`url` 直接喂 `<audio src>`，不要 `fetch` 成 blob**。浏览器发的 GET 带不了
  `X-API-Key`，所以这条路径不校验 API key，凭证是 URL 里那个一次性令牌
- **令牌用一次即失效**，重播要重新走 `/prepare` 或 `/speech`。有效期 900 秒
- **第二句起在你领到 URL 时就已经在后台合成了**，所以 `<audio>` 拿到的是做好的整段音频，
  句与句之间不会卡顿。第一句是流式的（首字节最快）
- **用户按停止时调 `POST /api/tts/stop`**，把队列里没播的令牌全丢掉。
  不调也不会出错（会自己过期），但那些句子会继续占着合成锁，拖慢下一次朗读
- `tts_max_chars` 照旧生效：念到上限后 `url` 一直是 `null`，`cursor` 不再前进

```http
POST /api/tts/stop     → {"dropped": 3}
POST /api/tts/warmup   → {"seconds": 12.4}
```

`/warmup` 一般不用调 —— 后端启动时会自动预热（合成一个「嗯」把模型权重加载进去）。
如果用户是在应用起来之后才手动启动 TTS 服务的，可以在状态灯从「离线」变「在线」时
调一次，把首次合成那十几秒的权重加载挪到用户点播放之前。失败也返回 200，`seconds: 0`。

### 状态与试听

```http
GET /api/tts/status
```

```json
{
  "mode": "manual", "enabled": true,
  "base_url": "http://127.0.0.1:8001",
  "model": "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-6bit",
  "voice": "Vivian", "format": "mp3", "max_chars": 800,
  "reachable": true,
  "models": ["mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-6bit"],
  "detail": ""
}
```

**这个接口会实时探活**（5 秒超时），因为本地 TTS 服务是手动起的，
「配置全对但进程没开」是最常见的失败方式。两种失败要在界面上分开显示：

| 情况 | 响应 | 界面 |
|---|---|---|
| 服务没起 | `reachable: false`，`detail` 是连接错误 | 红点「语音服务离线」，播放按钮置灰 |
| 服务在线但列的是别的模型 | `reachable: true`，`detail: "服务端未加载 xxx"` | 黄点 + 提示换模型名 |
| 正常 | `reachable: true`，`detail: ""` | 绿点，`models` 非空时可做成模型名的下拉候选 |

⚠️ **`models: []` 不是异常**。`/v1/models` 只列**当前已加载**的模型，服务刚起来时是空的，
首次合成才懒加载。只有 `detail` 非空才是真有问题，别拿 `models.length` 判断。

⚠️ **绿灯不保证能合成**。这个接口只探活，探不到「模型加载失败」——
模型是首次合成时才懒加载的，加载失败（磁盘缓存缺文件、代理配置不对）只会在
`POST /speech` 时以 502 暴露。所以**别把播放按钮的可用性绑在状态灯上**，
该点还是让点，失败了把 502 的 `detail` 原样弹出来即可 —— 那里面是 TTS 服务的原始报错，
对排查最有用。

设置页的「试听」= 拿一句固定文本 + 当前表单里的 `voice` / `instruct` 调 `/speech`，
这样用户不用先保存就能听到效果。

### 语音相关的设置项

全部通过 `GET/PATCH /api/settings` 读写，`group` 都是 `"tts"`，
**照着 `fields` 渲染就行，下表只是让你知道会出现什么**：

| key | kind | 默认 | 说明 |
|---|---|---|---|
| `tts_mode` | enum | `off` | `off` / `manual` / `auto`，见上 |
| `tts_model` | str | `mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-6bit` | 要和 `status.models` 里的一致 |
| `tts_voice` | str | `Vivian` | 可空。音色由模型内置，不同模型的可选值不同 |
| `tts_lang_code` | str | `Chinese` | |
| `tts_instruct` | str | 「用温柔、自然、亲切的语气说话…」 | 可空，≤200 字。**效果最明显的一项**，做成多行输入框 |
| `tts_format` | enum | `mp3` | `mp3` / `wav` / `flac` / `opus`。浏览器兼容性最好的是 mp3，没理由别改 |
| `tts_speed_percent` | int | `100` | 50–200。存百分比而不是倍率，整数好渲染好校验；后端除以 100 再发出去 |
| `tts_max_chars` | int | `800` | 50–5000。回答动辄上千字，全念完既慢又会撞服务端的 token 上限 |
| `tts_timeout` | int | `180` | 5–600 秒 |
| `tts_warmup` | bool | `true` | 🆕 第 6 批。启动时合成一个字把模型权重加载进去，消掉首次播放的十几秒冷启动 |

`tts_base_url` 在 `env_only` 里，**界面上不要给入口** —— 容器内外写法不同
（宿主机 `127.0.0.1:8001`，容器内 `host.docker.internal:8001`），改错了只会看到「连不上」。

---

## 自定义指令

> 🆕 第 5 批新增，**后端已就绪，前端待做**

设置项 `custom_instructions`（`kind: "text"`，`group: "prompt"`，上限 4000 字），
用户手写的一段自由文本，**原样追加到 system prompt 末尾**，改完立刻生效。

```http
PATCH /api/settings
{"custom_instructions": "回答控制在三句话以内。代码优先给 diff，不要贴整个文件。"}
```

改完可以马上用 `GET /api/debug/prompt` 看到它出现在返回的 `system` 末尾 ——
设置页把这两件事连起来（保存后给个「查看效果」）体验会很好。

### ⚠️ 它不是记忆，界面上别混在一起

这是前端最容易做错的地方。两者都进 system prompt，但性质完全相反：

| | 记忆页 `/memories` | 自定义指令 |
|---|---|---|
| 谁写的 | **模型自己** | **用户** |
| 谁能改 | 模型 + 用户 | 只有用户 |
| 每日整理 | 会去重、修正、提炼 | **不碰** |
| 存在哪 | `memories` 表，有版本历史 | `app_settings` 表，无版本历史 |
| 界面位置 | 记忆管理页 | **设置页的「人格与指令」分区** |

**不要在记忆管理页里给它入口**，也不要反过来。用户以为它是记忆的话，
会预期「模型能自己更新它」和「有版本历史可回滚」，这两件事都不成立。

参照物：ChatGPT 的 Custom Instructions vs Memory、Claude Projects 的
Project Instructions vs Project Knowledge —— 都是分开两个入口。

### 界面建议

设置页「人格与指令」分区，两项：`owner_name` 单行 + `custom_instructions` 多行。
`<textarea>` 至少 8 行，显示 `已用 X / 4000` 字数计数（超了后端返回 400，
`detail` 是「自定义指令最多 4000 个字符」）。

底下写一句人话解释边界，例如：

> 这段会原样加到每次请求的开头，优先级高于默认设定。它由你维护，模型不会修改它 ——
> 需要模型自己记住的事情，让它写进[记忆](/memories)。

空值合法（表示不注入），用 `PATCH {"custom_instructions": null}` 可以恢复 `.env` 默认。

---

## 调试：看清每次发了什么

> 🆕 第 5 批新增，**后端已就绪，前端待做**

发给模型的东西不等于你在界面上看到的东西：system prompt 是拼的，历史是规整过的，
运行时上下文是注进去的，无签名的 thinking 块是被滤掉的。这组接口把**真正发出去的
那个请求体**原样交出来。

### 当前的 system prompt

```http
GET /api/debug/prompt
```

```json
{
  "system": "你是用户的私人助手，只服务他一个人。……",
  "chars": 2016,
  "approx_tokens": 2016,
  "note": "只含记忆索引；具体记忆正文要模型 view 之后才进上下文"
}
```

不用发消息就能看，随时可调。**这里有个最容易误解的点值得在界面上写出来**：
记忆管理页里能看到全部长期记忆，但每轮请求的 system prompt 里**只有 `MEMORY.md` 索引**
那几行摘要，具体记忆文件的正文要模型自己 `view` 之后才进上下文。
所以「记忆页里有的」≠「这轮模型看到的」。

### 请求快照

先在设置里打开 `debug_prompts`（`kind: "bool"`，`group: "debug"`），否则不记。

```http
GET /api/debug/requests?conversation_id=37&limit=20
```

```json
{
  "enabled": true,
  "capacity": 20,
  "items": [
    {"id": 1, "at": "2026-08-06T10:24:43.982665",
     "provider": "deepseek", "model": "deepseek-v4-flash",
     "conversation_id": 37, "iteration": 0,
     "messages": 2, "system_chars": 2016, "tools": 1,
     "usage": {"prompt_tokens": 1822, "completion_tokens": 25, "total_tokens": 1847,
               "prompt_cache_hit_tokens": 0},
     "stop_reason": "stop", "error": "", "seconds": 1.4}
  ]
}
```

```http
GET /api/debug/requests/1
```

在上面那个对象的基础上多两个字段：

- **`payload`** —— **完整请求体，原样返回**。这就是发给模型的那个 JSON，
  界面上用折叠的 JSON viewer 展示，配一个「复制」按钮
- **`outline`** —— 已经渲染好的可读轮廓，一行一条消息，直接当 `<pre>` 显示即可：

```
system(2016) 你是用户的私人助手，只服务他一个人。你们已经认识很久了。 说话直接…
[1] user      text(24) <runtime_context> 当前时间：2026-08-06 星期四 10:24…
[2] assistant thinking(412, 有签名)
              tool_use view /memories/MEMORY.md
[3] user      tool_result ✓ # 记忆索引…
```

```http
DELETE /api/debug/requests    → 204，清空
```

几个要点：

- **`enabled: false` 时 `items` 一定是空的**。这是「没在记」，不是「没请求过」——
  界面必须把这两种情况分开，否则用户会以为功能坏了。空列表 + `enabled: true`
  才是「还没发过消息」
- `iteration` 是 agent loop 里的第几次请求：`0` 是用户这轮的第一次，模型每调一轮工具 +1。
  **一次回答可能对应好几条快照**，按 `conversation_id` + 时间分组显示更好读
- **只留最近 20 条**（`capacity`），环形缓冲，进程重启就没了。翻更旧的会 404
- 快照存在**进程内存**里，不落库。别做「历史调试记录」这种依赖持久化的功能
- `error` 非空表示这次请求失败了（网络/API 报错），`usage` 会是空的

### 界面建议

设置页加一个「调试」分区（`fields` 里 `group === "debug"` 的那些），放：

1. `debug_prompts` 开关 + 一句「开着会把完整对话历史留在内存里，排查完记得关」
2. 「查看当前 system prompt」按钮 → 弹层展示 `/api/debug/prompt` 的 `system`，带字数
3. 最近请求列表，点开看 `outline` + 完整 `payload`

聊天页也可以在每条 assistant 消息的操作栏加一个「本轮请求」入口
（`?conversation_id=` 过滤后取最近几条），但优先级低于设置页那个。

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

## 四个页面

四个页面加全局搜索都已上线，下面记录的是各页的设计意图和实现要点——
新增功能照着这个风格补，不要另起一套交互。

### 1. 聊天页 `/`

左侧会话列表 + 右侧消息流的经典布局。

- 消息流用上面的 `toTurns()` 规整后渲染
- **思考区默认折叠**，标题写「思考中…」/ 完成后「思考过程」，点开显示全文。思考内容可能很长（实测几百字）且常是英文，别默认展开
- 工具活动渲染成内联的窄状态条，不要做成气泡。文案按 `input.command` 区分：`view` → 「查阅记忆 <path>」，`create`/`str_replace`/`insert` → 「更新记忆 <path>」，`delete` → 「删除记忆 <path>」
- 正文用 `react-markdown` + `remark-gfm`，代码块用 `shiki` 高亮
- 流式时自动滚到底，但**用户手动上滚后要停止自动滚动**
- 发送中禁用输入框，提供「停止」按钮（AbortController）
- 🚧 **第 5 批待做**：`tts_mode !== "off"` 时，每条 assistant 消息的操作栏（和「重新生成」
  同一排）加一个播放按钮，把整条消息的原始 Markdown 传过去。
  合成要几秒到十几秒，按钮必须有 loading 态。全页共用一个 `<audio>` 实例，
  播新的之前先停掉旧的
- 🚧 **第 6 批待做（性能，优先级高于上一条的细节打磨）**：
  - **播放按钮从 `/speech` 换成 [`/prepare`](#边下边播prepare)** —— 拿到 URL 直接喂
    `<audio src>` 边下边播，首字节 6.97s → 1.12s。代价是令牌用一次即失效、不能重播，
    想保留「同一条消息缓存 blob 复用」就两条路并存：首播走 `/prepare`，重播走 `/speech`
  - **`auto` 档改走[句级流水线](#边写边读句级流水线)**，不要再等 `done`。
    在 `text_delta` 回调里节流（~300ms）调 `POST /api/tts/next`，
    拿到 `url` 就入队、播完一个播下一个，收到 `done` 时用 `flush: true` 收尾。
    首声从「LLM 全程 + TTS 全程」变成 1～2 秒
  - **停止播放 / 切换会话 / 组件卸载时调 `POST /api/tts/stop`**，
    把队列里没播的句子丢掉，否则它们会继续占着合成锁

### 2. 记忆管理页 `/memories`

**这页是这个项目区别于普通聊天 UI 的地方，值得多花时间。**

左树右编辑器：

- 左侧文件树，`MEMORY.md` 置顶
- 右侧 Markdown 编辑器（`@uiw/react-md-editor` 或纯 textarea + 预览切换均可），保存走 `PUT`
- 底部/侧边 tab 显示版本历史，选两个版本做 diff
- 版本条目上用 badge 标出 `actor`，一眼能看出「这条是模型自己记的还是我改的」
- 删除要二次确认，目录删除要额外警告会递归

### 3. 每日回顾页 `/review`

一个日期选择器，下面三块：

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

### 4. 设置页 `/settings`

运行时配置在这里直接改，改完立刻生效。

**照着 `fields` 动态渲染**，不要写死字段列表——后端加配置项时前端不用改：

```tsx
{settings.fields
  .filter(f => !f.provider || f.provider === settings.values.provider)
  .map(f => {
    const changed = settings.sources[f.key] === "db";
    switch (f.kind) {
      case "bool":  return <Switch ... />;
      case "enum":  return <Select options={f.choices} ... />;
      case "int":   return <NumberInput min={f.minimum} max={f.maximum} ... />;
      default:      return <Input maxLength={f.maximum ?? undefined} ... />;
    }
  })}
```

几个要点：

- `sources[key] === "db"` 的项显示「已修改」标记 + 「恢复默认」按钮
  （恢复 = `PATCH {"<key>": null}`）
- 按 `fields[].provider` 过滤：`effort` / `max_tokens` 只在 anthropic 下显示，
  `deepseek_*` 只在 deepseek 下显示
- provider 下拉用 `providers[]`，`available: false` 的置灰并显示 `reason`
- `env_only` 里的字段**不要给编辑入口**，可以只读展示并注明「需改 .env 后重启」
- 400 的 `detail` 是可直接展示的中文，贴在对应字段下面即可
- 加一个「立即备份」按钮调 `POST /api/jobs/backup`，**会阻塞几秒**，要 loading；
  成功后显示 `dump_file` 和 `memory_files`
- 🚧 **第 5 批待做**：按 `fields[].group` 分区渲染（`""` 归「模型与整理」，`"tts"` 归「语音」，
  `"debug"` 归「调试」，调试区详见[调试](#调试看清每次发了什么)）。
  语音区顶部放 `GET /api/tts/status` 的在线状态灯 + 一个「试听」按钮
  （拿当前表单里未保存的 `voice` / `instruct` 调 `/speech`）。详见[语音播放](#语音播放)
- 🚧 **第 6 批待做**：状态灯从「离线」变「在线」时调一次 `POST /api/tts/warmup`，
  把首次合成的权重加载（十几秒）挪到用户点播放之前。返回 `{"seconds": 12.4}`，
  失败也是 200 + `seconds: 0`，不用报错

### 聊天页的进阶交互（已实现）

- **重新生成** / **编辑重发**：见[截断](#截断重新生成编辑重发)，一个接口两用
- **归档**：比删除温和且可逆
- **本轮用量**：`done` 事件里就有 `usage`，在消息末尾显示一个淡淡的 token 数

会话级思考开关（`PATCH /api/conversations/{id}` 的 `thinking` 三态）后端和
`lib/api.ts` 里都还在，但顶栏的下拉已经撤掉了——目前没有界面入口，
默认值统一在设置页看。要恢复的话接口是现成的。

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

/** 🆕 第 4 批：GET /api/settings 的完整响应（第 3 批那四个平铺字段仍然保留） */
export interface RuntimeSettings {
  /** 当前生效值（数据库覆盖已叠加在 .env 之上） */
  values: Record<string, string | number | boolean>;
  /** 每项来自哪层：db = 你改过，env = .env 默认 */
  sources: Record<string, "db" | "env">;
  /** 照着它渲染表单，别硬编码字段清单 */
  fields: SettingField[];
  providers: { value: string; available: boolean; reason: string }[];
  /** 这些只能改 .env，界面上不要给入口 */
  env_only: string[];

  // 运行时卡片用的平铺字段，保持不变
  provider: string;
  model: string;
  thinking_default: boolean;
  thinking_toggle: boolean;
  /** 🆕 第 7 批：Obsidian 知识库是否挂载。只读状态位，不在 fields 里 */
  kb_enabled: boolean;
}

export interface SettingField {
  key: string;
  label: string;
  /** text = 多行文本框，校验规则和 str 完全一样 */
  kind: "str" | "text" | "int" | "bool" | "enum";
  /** kind=enum 时的候选值 */
  choices: string[];
  /** 数字是取值范围，字符串是长度范围 */
  minimum: number | null;
  maximum: number | null;
  /** 非空表示只在该 provider 下有意义（如 effort 只对 anthropic） */
  provider: string;
  /** 🆕 第 5 批。界面分区："" = 模型与整理，"tts" = 语音，"debug" = 调试 */
  group: string;
}

/** 🆕 第 5 批 */
export type TtsMode = "off" | "manual" | "auto";

export interface TtsStatus {
  mode: TtsMode;
  enabled: boolean;
  base_url: string;
  model: string;
  voice: string;
  format: string;
  max_chars: number;
  /** false = 服务没起；true 但 detail 非空 = 在线但没加载配置里的模型 */
  reachable: boolean;
  models: string[];
  detail: string;
}

/** 🆕 第 5 批 */
export interface DebugRequestSummary {
  id: number;
  at: string;
  provider: string;
  model: string;
  conversation_id: number | null;
  /** agent loop 里的第几次请求，0 = 用户这轮的第一次 */
  iteration: number;
  messages: number;
  system_chars: number;
  tools: number;
  usage: Record<string, number>;
  stop_reason: string;
  /** 非空表示这次请求失败了 */
  error: string;
  seconds: number;
}

export interface DebugRequestDetail extends DebugRequestSummary {
  /** 完整请求体，就是发给模型的那个 JSON */
  payload: Record<string, unknown>;
  /** 渲染好的可读轮廓，一行一条消息 */
  outline: string[];
}

export interface DebugRequestList {
  /** false 时 items 一定是空的——是「没在记」，不是「没请求过」 */
  enabled: boolean;
  capacity: number;
  items: DebugRequestSummary[];
}

export interface SpeechRequest {
  /** 原始 Markdown，不要自己 strip */
  text: string;
  /** 试听用的临时覆盖，不写库 */
  voice?: string;
  instruct?: string;
  /** 默认 true，按 tts_max_chars 截断 */
  truncate?: boolean;
}

/** 🆕 第 6 批 */
export interface PrepareResult {
  /** 相对路径，拼上 API_BASE 后直接喂 <audio src>。用一次即失效 */
  url: string;
  expires_in: number;
}

/** 🆕 第 6 批 · 句级流水线 */
export interface TtsNextRequest {
  /** 累计全文（原始 Markdown），不是增量 */
  text: string;
  /** 上次返回的 cursor，第一次传 0。原样传回，不要自己解释它 */
  cursor: number;
  /** 流结束时传 true，把尾巴念掉 */
  flush?: boolean;
}

export interface TtsNextResult {
  /** null = 还凑不出一句完整的话，不是错误 */
  url: string | null;
  /** 这次切出来的句子，调试/高亮用 */
  text: string;
  cursor: number;
  expires_in: number;
}

/** 🆕 第 4 批 */
export interface BackupResult {
  dump_file: string;
  dump_bytes: number;
  memory_files: number;
  memory_dir: string;
  created_at: string;
  /** 非空表示 dump 出了问题，但记忆文件仍导出成功 */
  detail: string;
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

## 本地启动完整应用

数据库、后端和前端都由 Docker Compose 管理，一条命令：

```bash
cp .env.example .env             # 首次启动：选择 provider 并填写对应 key
docker compose up -d --build     # db + api + frontend，迁移自动执行
curl localhost:18000/health      # {"status":"ok","provider":"deepseek",...}
```

浏览器打开 `http://localhost:13000`。后端日志用 `docker compose logs -f api`，
前端编译和热更新日志用 `docker compose logs -f frontend`。

宿主机端口由 `.env` 的 `FRONTEND_PORT` 和 `API_PORT` 控制。浏览器请求同源
`/backend`，前端容器通过 `http://api:8000` 转发，因此调整宿主机端口不影响容器通信。

`GET /health` 可以用来做前端启动时的连通性检查。完整接口列表见 `http://localhost:18000/docs`（FastAPI 自动生成）。
