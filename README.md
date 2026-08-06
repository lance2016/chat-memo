# 个人 AI 助手

只给自己用的 AI 聊天应用。核心不是聊天本身，而是**把每天的对话沉淀成结构化的长期记忆** ——
模型用 memory 工具自己读写记忆文件，每天凌晨再做一次全局整理。

后端 FastAPI + PostgreSQL，前端 Next.js 位于 `frontend/`。

## 快速开始

```bash
cp .env.example .env              # 选 PROVIDER，填对应的 key
docker compose up -d --build      # db + api + frontend 一起起来，迁移自动执行
curl localhost:8000/health
```

就这两步，然后打开 <http://localhost:3000>。`api` 和 `frontend` 都挂载了源码，
**改代码自动热重载**，不用重启容器。三个服务都是 `restart: unless-stopped`，
Docker 一起来就会自动拉起。

```bash
docker compose logs -f api        # 看日志（彩色，一轮对话是一段可读的叙事）
docker compose logs -f frontend   # 看前端编译和热更新日志
docker compose exec api pytest -q # 在容器里跑测试（282 个，不需要 API key）
docker compose restart api        # 改了 .env 或依赖后重启
docker compose down               # 停掉（数据保留在 pgdata 卷里）
```

改了后端依赖（`pyproject.toml`）或前端依赖（`frontend/package-lock.json`）后，
执行 `docker compose up -d --build api frontend` 重建对应镜像。

<details>
<summary>不用 Docker，直接在宿主机跑后端</summary>

`.env` 里的 `DATABASE_URL` 默认指向 `localhost:5433`，所以只起数据库即可：

```bash
docker compose up -d db
uv run alembic upgrade head
uv run uvicorn app.main:app --reload

# 另一个终端启动前端
cd frontend
cp .env.example .env.local
npm install
npm run dev
uv run pytest
```

前端开发请保持 `npm run dev` 进程运行。Next.js 会自动监听 `frontend/` 下的
`.tsx`、`.ts` 和 `.css` 修改，完成重新编译并刷新浏览器；不需要手动重启。
`npm run start` 是生产模式，不监听源码，使用前需要先 `npm run build`。
开发服务使用 `.next-dev`，生产构建使用 `.next-build`，因此运行构建检查不会再
覆盖热更新缓存或导致开发服务必须重启。

容器内的 `DATABASE_URL` 由 compose 覆盖成 `db:5432`，两种方式互不干扰。
</details>

## 支持的模型

`.env` 里的 `PROVIDER` 切换，业务层不变：

| PROVIDER | 模型 | 记忆工具 | 思考 | 缓存 |
|---|---|---|---|---|
| `anthropic` | `claude-opus-5` | 原生 `memory_20250818` | adaptive thinking | `cache_control` 显式 |
| `deepseek` | `deepseek-v4-flash` / `-pro` | 手写 function schema | `reasoning_content` | 自动，无需配置 |

内部统一用 Anthropic 的 content block 数组作为**标准存储格式**，DeepSeek provider
负责双向翻译（`to_openai_messages` / `to_content_blocks`）。所以换 provider 之后
已有的对话历史仍然可读，不用迁移数据。

DeepSeek 侧的两个注意点：思考内容不能回传（翻译时丢弃）；没有原生记忆工具，
schema 写在 `app/memory/tool.py` 的 `MEMORY_TOOL_PARAMETERS`，模型表现依赖这段描述质量。

## 记忆是怎么工作的

三层，越往上越浓缩：

| 层 | 存在哪 | 说明 |
|---|---|---|
| L0 原始对话 | `messages` | content block 数组原文，永不删 |
| L1 会话摘要 | `conversation_summaries` | 每天增量生成，带水位线避免重复处理 |
| L2 长期记忆 | `memories` | 模型自己读写，**只有这层进 prompt** |

三层在界面上分别对应：L0 = 聊天页的消息流，L1 = 每日回顾页的会话摘要，
**L2 = 记忆管理页 `/memories`**，也就是通常说的「长期记忆」。

还有一样东西也进 system prompt 但**不是记忆**：设置页的[自定义指令](#自定义指令)。
三层记忆是模型写的，自定义指令是你写的，两者的区别见下面。

⚠️ 但要注意 **L2 进 prompt 的只是索引，不是全部正文**。每轮请求的 system prompt 里
只有 `MEMORY.md` 那几百 token 的一行行摘要；`profile/preferences.md` 之类的正文
要模型自己 `view` 之后才进上下文。所以「记忆管理页里看到的」⊋「这轮请求里模型看到的」。
想确认某一轮到底注入了什么，用 `GET /api/debug/prompt` 和下面的[请求快照](#看清每次到底发了什么)。

模型每次 `view` 记忆都记在 `memory_reads`，`GET /api/memories/stats` 汇总。
注意 **reads=0 不等于没用上** —— 索引里的一行摘要往往就够回答了，短记忆本来就不需要展开。

L2 逻辑上是 `/memories` 下的一棵文件树，物理上是 Postgres 的行：

```
/memories/
  MEMORY.md              索引，每条记忆一行摘要 —— 每轮对话全量注入 system prompt
  profile/identity.md
  profile/preferences.md
  projects/<项目>.md
  people/<名字>.md
  timeline/2026-08.md
```

**渐进式披露**：system prompt 里只放索引（几百 token，命中 prompt cache），
模型需要细节时用 `view` 读具体文件。所以上下文成本恒定，记忆总量可以一直长。

为什么不用 embedding 做主检索：个人记忆条目就几百到几千条，全量注入索引比向量召回准得多；
而且记忆需要去重和修正（「我换工作了」要覆盖旧记录），这是写操作，让模型直接改文件才对。
pgvector 已装好，留给将来的**归档检索**（翻两个月前某次对话原文），那才是向量真正合适的场景。

两个写入时机：

1. **聊天中实时写** —— 模型觉得值得记就直接调工具，`actor=chat`
2. **每日整理**（默认凌晨 4 点）—— 把当天摘要一起交给模型做去重、修正、提炼，`actor=consolidation`

第二个更重要，因为它有全局视角；实时写只看得到当前这轮对话。

每次记忆变更都在 `memory_versions` 留快照，可审计、可回滚，前端记忆页的版本历史就靠它。

## API 契约（前端对接）

> 写前端请直接看 **[docs/frontend-api.md](docs/frontend-api.md)** —— 那份是完整版，
> 带真实响应样例、SSE 消费代码、历史消息规整逻辑和 TypeScript 类型定义。
> 下面只是速查。

所有 `/api/*` 在 `.env` 里设了 `API_KEY` 时需要带 `X-API-Key` 头；留空则不校验。

### 会话

```
POST   /api/conversations                    → {id, title, created_at, updated_at}
GET    /api/conversations?limit=50           → 上述对象数组，按 updated_at 倒序
GET    /api/conversations/{id}/messages      → [{id, role, content, created_at}]
DELETE /api/conversations/{id}               → 204
```

`content` 是 Anthropic content block 数组原文。前端渲染时只需处理 `type == "text"`，
其余（`thinking` / `tool_use` / `tool_result`）按需折叠显示或直接跳过。

### 聊天（SSE）

```
POST /api/chat
body: {"conversation_id": 1, "content": "你好"}
→ text/event-stream，每行 data: {...}
```

事件类型：

| type | 字段 | 前端处理 |
|---|---|---|
| `thinking_delta` | `text` | 追加到可折叠的思考区 |
| `text_delta` | `text` | 追加到正文 |
| `tool_use` | `name`, `input` | 显示「正在更新记忆…」，`input.path` 可展示具体文件 |
| `tool_result` | `name`, `ok`, `summary` | 更新上面那条状态 |
| `title` | `title` | 首轮自动生成的标题，用它更新侧边栏 |
| `done` | `usage` | 本轮结束，token 用量 |
| `message_id` | `message_id` | 这轮最后一条消息的 id |
| `error` | `message` | 展示错误并结束 |

事件顺序保证：`title`（如果有）一定在 `done` 之前；`done` 之后只跟一个 `message_id`。
所以前端可以放心地把 `done` 当作终止信号。

流里可能出现多组 `tool_use` / `tool_result`（模型可以连续调多轮工具），`done` 只出现一次。
出错时只有 `error`，不会有 `done`。

用 `fetch` + `ReadableStream` 消费，不要用 `EventSource`（它只支持 GET）。

### 记忆

路径参数是**不带 `/memories` 前缀**的相对路径。

```
GET    /api/memories                          → [{path, is_dir, size}]，扁平树
GET    /api/memories/{path}                   → {path, content, created_at, updated_at}
PUT    /api/memories/{path}   {"content":...} → 覆盖写，actor=manual
DELETE /api/memories/{path}                   → 204，目录会递归删除
GET    /api/memories/{path}/versions?limit=50 → [{id, path, content, operation, actor, created_at}]
```

`operation` ∈ `created|modified|deleted`，`actor` ∈ `chat|consolidation|manual`。
版本历史按时间倒序，前端可以拿相邻两条做 diff。

### 任务

```
POST /api/jobs/consolidate?day=2026-08-05    → 手动触发记忆整理，day 不传则为今天
  → {date, summarized_conversations, tool_calls, memory_writes, skipped, failed_summaries, detail}
```

### 其他

```
POST   /api/conversations/{id}/archive[?archived=false]   归档 / 取消归档
DELETE /api/conversations/{id}/messages?after={id}        截断（重新生成 / 编辑重发）
GET    /api/summaries?day=&conversation_id=               会话摘要
GET    /api/memories/versions?day=&actor=                 全局记忆变更（含已删除的）
GET    /api/memories/stats?days=30                        记忆使用率统计
GET    /api/usage?days=7                                  按天用量，已跨 provider 归一化
GET    /api/search?q=&limit=20                            搜对话历史 + 记忆
GET    /api/settings                                      当前配置 + 可选项 + 每项来源
PATCH  /api/settings                                      改配置，立刻生效不用重启
POST   /api/jobs/backup                                   pg_dump + 记忆导出成 .md 文件树
POST   /api/tts/speech    {"text": "原始 Markdown"}        合成语音，返回音频二进制（等整段做完）
POST   /api/tts/prepare   {"text": "原始 Markdown"}        换一个播放 URL，喂 <audio src> 边下边播
POST   /api/tts/next      {"text": ..., "cursor": 0}      句级流水线：切出下一句并给出播放 URL
POST   /api/tts/stop                                      丢掉队列里还没播的句子
POST   /api/tts/warmup                                    把模型权重加载进 MLX（启动时自动做）
GET    /api/tts/status                                    语音配置 + 实时探活本地 TTS 服务
GET    /api/debug/prompt                                  当前 system prompt 原文
GET    /api/debug/requests[?conversation_id=]             最近发给模型的请求，摘要
GET    /api/debug/requests/{id}                           某一次的完整 payload
DELETE /api/debug/requests                                清空快照
```

## 前端现状（Next.js 15，`frontend/`）

四个页面都已上线：

| 页面 | 做了什么 |
|---|---|
| `/` 聊天页 | 会话列表 + 流式消息，thinking 折叠，tool_use 内联状态条，重新生成 / 编辑重发 / 归档，每条回答带播放按钮（`auto` 模式边写边读） |
| `/memories` 记忆管理页 | 左树右编辑器，看/改/删 + 版本历史 diff + 恢复，另有使用率统计视图 |
| `/review` 每日回顾页 | 按天看会话摘要、记忆变更时间线、用量，可手动触发整理 |
| `/settings` 设置页 | 按后端 `fields` 动态渲染的可编辑配置、立即备份、外观与聊天偏好 |

全局搜索在每个页面都能用（`Cmd/Ctrl + K`），搜对话历史 + 记忆。

**长期记忆是完全暴露给界面的**：全文可读可改可删、历史可回滚、命中搜索。
这是有意为之——记忆能被审计和修正才敢让模型自己写。代价是没有分级和脱敏，
所有防护都压在 `API_KEY` 和 `CORS_ORIGINS` 上，放公网前必须先把这两项配好。

还没做的：会话级思考开关的界面入口已撤掉（接口还在）；pgvector 的归档检索还没接界面。
`env_only` 的配置项在设置页「系统与数据」里只读列出，提示改完要重启后端。

前端不直接调任何模型 API，一律走后端。

## 语音播放

回答可以只出文字，也可以念出来。语音由跑在宿主机上的 [mlx-audio](https://github.com/Blaizzy/mlx-audio)
合成（OpenAI 兼容的 `/v1/audio/speech`），默认模型 `Qwen3-TTS-12Hz-1.7B-CustomVoice-6bit`。

```bash
# 先把 TTS 服务起在 8001，然后
curl -X POST localhost:8000/api/tts/speech \
  -H 'Content-Type: application/json' \
  -d '{"text":"## 你好\n**世界**"}' --output out.mp3
```

一个开关决定全部行为，`tts_mode`（设置页可改，立刻生效）：

| 值 | 行为 |
|---|---|
| `off`（默认） | 纯文字。调 `/speech` 返回 409 |
| `manual` | 每条回答旁给播放按钮，点了才合成 |
| `auto` | 边写边读：模型每说完一句就合成、排队播放 |

音色、语气、语速、朗读字数上限都在设置页里（`tts_*` 十项）。**语气指令效果最明显** ——
默认那句「用温柔、自然、亲切的语气说话，像朋友聊天一样，语速稍慢」比调语速参数管用得多。

### 别让用户干等

整段回答写完再合成，等待是 `LLM 全程 + TTS 全程`；本地模型念几百字要十几秒，
这么串起来体验就废了。三处优化，加起来把首声压到 1～2 秒：

- **句级流水线**（`POST /api/tts/next`）。模型每吐出一句完整的话就拿去合成、排进播放队列，
  等待变成 `首句 LLM + 首句 TTS`。切句和清洗都在服务端 —— 朗读用的文本经过清洗，
  前端拿 Markdown 切出来的位置和它对不上。规则在 `app/tts/segment.py`：
  第一句可以断在逗号上（早一秒出声就少一秒干等），后面的句子只在句末断（连贯优先），
  代码围栏只开一半时那段不切
- **提前合成**。第二句往后的令牌在领的时候就在后台把音频做好了，浏览器来取时直接给，
  句与句之间不留空隙。第一句例外，走流式（首字节 6.97s → 1.12s）
- **启动预热**。mlx-audio 是懒加载的，权重要到第一次合成才读进显存。
  不预热的话这十几秒会算在用户第一次点播放的头上，看起来就是「语音特别慢」

三个设计取舍：

- **前端不直连 8001，统一走后端代理**。配置只有一份，不用给 TTS 服务额外配 CORS，
  「要不要开、念多长」是服务端策略而不是浏览器策略
- **传原始 Markdown，清洗在服务端做**。代码块整段丢掉（念出来听不懂，还能轻松吃掉
  服务端的 `max_tokens`），标题井号/列表符号/强调星号去掉但保留文字，链接只念文案。
  前端渲染用的和朗读用的是两套文本，各 strip 一遍迟早不一致
- **音频不落库也不落盘**。内容本来就在 `messages` 里，重放一次的成本远低于管理一堆
  音频文件的生命周期

## 代码结构

```
app/
  config.py                 pydantic-settings
  security.py               X-API-Key 校验
  db/models.py              SQLAlchemy 模型
  llm/
    provider.py             LLMProvider / ToolExecutor 协议
    anthropic_provider.py   Claude 的流式 agent loop
    deepseek_provider.py    DeepSeek 的 agent loop + 消息格式互转
    factory.py              按 PROVIDER 选实现
    events.py               agent 事件定义
  memory/
    paths.py                路径校验（拒绝穿越）
    store.py                Postgres 虚拟文件系统，六个命令
    tool.py                 memory 工具派发
    prompt.py               system prompt 组装
    router.py               记忆管理 API
  chat/
    service.py              对话编排 + 落库
    router.py               会话 CRUD + SSE
  jobs/
    consolidate.py          每日记忆整理
    scheduler.py            lifespan 里的定时循环
  tts/
    client.py               本地 TTS 服务的客户端 + Markdown 清洗 + 预热
    segment.py              把还在生成中的回复切成一句一句
    tickets.py              一次性播放令牌 + 提前合成
    router.py               合成 / 流水线 / 探活
  debug/
    recorder.py             请求快照的环形缓冲 + 轮廓渲染
    log.py                  把轮廓打进日志
    router.py               看快照 / 看当前 system prompt
```

## 配置在哪改

分三层，后面覆盖前面：

```
会话覆盖   conversations.thinking          PATCH /api/conversations/{id}
数据库设置 app_settings 表                  PATCH /api/settings（设置页，立刻生效）
.env 默认  Settings                        改完要重启容器
```

**密钥和基础设施只能改 `.env`**：`*_API_KEY`、`DATABASE_URL`、`API_KEY`、`CORS_ORIGINS`、
`LOG_*`、`TZ`、`TTS_BASE_URL`。接口一律拒绝写这些——改坏 `api_key` 或 `cors_origins` 会把设置页自己锁在门外。

## 备份

```bash
curl -X POST localhost:8000/api/jobs/backup
```

产出在宿主机 `backups/`：`.dump`（`pg_restore` 可完整恢复）+ `memories/` 真实文件树
（可读、可 grep、可 git）。**记忆平时只以数据库行存在，磁盘上没有 .md 文件**，
这里是唯一落成文件的地方。

## 自定义指令

设置页有一块自由文本，原样追加到 system prompt 末尾，改完立刻生效：

```bash
curl -X PATCH localhost:8000/api/settings -H 'Content-Type: application/json' \
  -d '{"custom_instructions": "回答控制在三句话以内。代码优先给 diff，不要贴整个文件。"}'
curl localhost:8000/api/debug/prompt      # 立刻能看到它出现在末尾
```

**它和记忆是两个正交的东西**，虽然都进 system prompt：

| | 谁写 | 谁能改 | 每日整理会碰吗 |
|---|---|---|---|
| 记忆 `/memories` | 模型自己 | 模型 + 你 | **会**，去重、修正、提炼 |
| 自定义指令 | 你 | **只有你** | 不会 |

分开的理由是**权威性**。自定义指令是「我说了算」的那部分：它在 prompt 里明确声明
优先级高于默认人格，且明确告诉模型不许用 memory 工具改它。如果把它塞进 `/memories`
当成一条普通记忆，每日整理迟早会把它「优化」掉或者和别的记忆合并 ——
那正是记忆层该做的事，但对指令是灾难。

主流方案都是这么切的，可以对照着理解：

| 产品 | 人写的（authored） | 模型写的（extracted） |
|---|---|---|
| ChatGPT | Custom Instructions | Memory |
| Claude Projects | Project Instructions | Project Knowledge |
| Claude Code | `CLAUDE.md` | —— |
| Letta / MemGPT | core memory `persona` 块 | archival / recall memory |
| 这个项目 | `custom_instructions` | `/memories` 三层 |

几个实现细节：

- **放在 system prompt 最末尾**。结尾是指令遵循最强的位置，而这段权威性最高。
  对 prompt cache 没有影响 —— 整个 system 是一个缓存块，块内顺序不影响命中
- **上限 4000 字**（约 4000 token）。每轮都进 prompt，首轮之后命中缓存成本可接受；
  再长就该写成记忆文件让模型按需 `view`，而不是无条件占着上下文
- **留空时整段不出现**，不给模型一个空标题让它猜
- 改它会让 prompt cache 失效一次，属于正常代价 —— 这种东西一个月改不了几回

还没做（也建议先别做）：让模型也能改这块，也就是 Letta 那种双向可写的 core memory。
模型已经有 `/memories` 可写了，再给一块可写的只会让「哪条该写哪」变成新的模糊地带。

## 看清每次到底发了什么

system prompt 是拼出来的，历史是规整过的（`sanitize_history`），运行时上下文是注进去的，
无签名 thinking 是被滤掉的（`strip_unsigned_thinking`）—— 经手的地方太多，
光看数据库和代码猜不出最终 payload 长什么样。所以有个开关把原物留下来：

```bash
# 设置页开「记录发给模型的请求」，或者
curl -X PATCH localhost:8000/api/settings -H 'Content-Type: application/json' \
  -d '{"debug_prompts": true}'
```

开了之后：

```bash
docker compose logs -f api        # 日志里每次请求打一段轮廓
curl localhost:8000/api/debug/requests            # 最近 20 次，摘要
curl localhost:8000/api/debug/requests/1          # 某一次的完整 payload
curl localhost:8000/api/debug/prompt              # 当前 system prompt 原文，不用发消息
```

日志里长这样，一眼能看出历史串没串、system 变没变、thinking 块滤掉没有：

```
🔍 conv#37 请求#1 deepseek/deepseek-v4-flash · 第 1 次 · system 2016 字 · 2 条消息 · 1 工具
   system(2016) 你是用户的私人助手，只服务他一个人。你们已经认识很久了。 说话直接，不用客套话开场…
   [1] user      <runtime_context> 当前时间：2026-08-06 星期四 10:24 你实际运行在：deepseek…
```

三个取舍：

- **日志里放轮廓，完整 JSON 只在接口里**。一轮对话的 payload 动辄几十 KB，
  整段吐到终端就没法看了，而 90% 的问题看轮廓就能定位
- **记的就是真正发出去的那个 dict**，不另拼一份给调试看 ——
  那样迟早会和真实请求不一致，比没有调试信息更糟
- **存进程内存，不落库**。调试数据的价值只有几分钟，落库要建表、要清理、
  还要考虑里面有对话原文。重启即清空是特性

`iteration` 是 agent loop 里的第几次请求：0 是用户这轮的第一次，模型每调一轮工具 +1。
一次回答有工具调用时会产生好几条快照，环形缓冲只留最近 20 条。

**排查完记得关掉** —— 开着会把完整对话历史一直留在内存里。

## 几个容易踩的坑

- **消息必须整块存整块回传**。`content` 里的 thinking 块带签名，只抽 text 回传下一轮会 400。
- **system prompt 不能放变动内容**。prompt cache 是前缀匹配，插一个时间戳整段缓存就失效。
  需要「今天几号」放进 user 消息。验证方法：看第二轮起 `usage.cache_read_input_tokens > 0`。
- **`max_tokens` 大时必须流式**，否则撞 SDK 的 HTTP 超时。
- **SSE 生成器要自己管数据库会话**，不能用 `Depends(get_session)` —— 请求函数返回后依赖就清理了，
  而生成器那时才刚开始跑。
- 记忆路径来自模型输出，`validate_path` 的穿越校验不能省。导出成真实文件时**再验一次**。
- **同一会话不能并发生成**。两个请求各读一份历史再各自追加，顺序会错乱成
  `user,user,assistant,assistant`，两条回复各看到半边上下文。已加按会话的锁。
- **无签名的 thinking 块发给 Anthropic 会 400**。DeepSeek 产生的和中断兜底存的都没有签名，
  `strip_unsigned_thinking` 在发请求前滤掉。这是切 provider 的前置条件。
- **搜索用三元组子串匹配，不是全文检索**。Postgres 的中文分词要装 zhparser/pg_jieba，
  镜像里没有；`pg_trgm` 不需要分词，中英文一视同仁（实测 20 万行 cost 4612 → 108）。
  代价是没有词干还原和同义词。消息正文冗余在 `messages.search_text` 列上才能建索引。
- **「一天」按本地时区切，不是 UTC**。时间戳存 UTC，但「今天的对话」是本地概念；
  UTC+8 下直接按 UTC 切天会漏掉本地 00:00–08:00 的对话。见 `local_day_bounds`。
  **容器默认 UTC 会让这个修复失效**，compose 里必须设 `TZ`。
- **中断的对话要修复再用**。tool_use 没有配对的 tool_result 会让会话之后每条消息都 400，
  `sanitize_history` 在加载历史时补齐。点停止/关标签页/热重载都会触发。
- **容器里连不上宿主机的 TTS 服务**。容器内的 `127.0.0.1` 是容器自己，要走
  `host.docker.internal:8001`。compose 已经覆盖成它了（Linux 上还要 `extra_hosts`
  映射到 `host-gateway`），不用 Docker 直接跑后端时才是 `.env` 里那个 `127.0.0.1`。
- **TTS 合成必须串行**。MLX 后端一次只加载一份模型权重，并发请求只会互相拖慢并放大
  显存峰值。`app/tts/client.py` 里用一把进程内的锁排队。
- **当前时间不能进 system prompt**（破坏缓存），走 `build_runtime_context` 注入到 user 侧。
  不注入的话模型不知道今天几号，也会把自己的身份瞎猜成 Claude。

## 加新模型

实现 `app/llm/provider.py` 里的 `LLMProvider` 协议，在 `factory.py` 注册即可，
chat / memory / jobs 层不用动。OpenAI 兼容的接口可以直接照抄 `deepseek_provider.py`，
通常只需要改 base_url 和模型名。
