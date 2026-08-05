# 个人 AI 助手

只给自己用的 AI 聊天应用。核心不是聊天本身，而是**把每天的对话沉淀成结构化的长期记忆** ——
模型用 memory 工具自己读写记忆文件，每天凌晨再做一次全局整理。

后端 FastAPI + PostgreSQL，前端 Next.js 位于 `frontend/`。

## 快速开始

```bash
cp .env.example .env              # 选 PROVIDER，填对应的 key
docker compose up -d --build      # db + api 一起起来，迁移自动执行
curl localhost:8000/health
```

就这两步。`api` 容器挂载了 `./app`，**改代码自动热重载**，不用重启容器。
两个容器都是 `restart: unless-stopped`，Docker 一起来就会自动拉起。

```bash
docker compose logs -f api        # 看日志（彩色，一轮对话是一段可读的叙事）
docker compose exec api pytest -q # 在容器里跑测试（107 个，不需要 API key）
docker compose restart api        # 改了 .env 或依赖后重启
docker compose down               # 停掉（数据保留在 pgdata 卷里）
```

改了依赖（`pyproject.toml`）要重建镜像：`docker compose up -d --build api`。

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
```

## 前端建议范围（Next.js 15 + Tailwind + shadcn/ui）

1. **聊天页** —— 左侧会话列表，右侧消息流。thinking 折叠，tool_use 显示为内联状态条。
   Markdown 用 `react-markdown` + `shiki`。
2. **记忆管理页** —— 左树右编辑器，看/改/删记忆 + 版本历史 diff。
   这页是这个项目区别于普通聊天 UI 的地方，值得做好。
3. **每日回顾页**（可后置）—— 按天看会话摘要和当天的记忆变更。

前端不直接调 Anthropic，一律走后端。

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
```

## 配置在哪改

分三层，后面覆盖前面：

```
会话覆盖   conversations.thinking          PATCH /api/conversations/{id}
数据库设置 app_settings 表                  PATCH /api/settings（设置页，立刻生效）
.env 默认  Settings                        改完要重启容器
```

**密钥和基础设施只能改 `.env`**：`*_API_KEY`、`DATABASE_URL`、`API_KEY`、`CORS_ORIGINS`、
`LOG_*`、`TZ`。接口一律拒绝写这些——改坏 `api_key` 或 `cors_origins` 会把设置页自己锁在门外。

## 备份

```bash
curl -X POST localhost:8000/api/jobs/backup
```

产出在宿主机 `backups/`：`.dump`（`pg_restore` 可完整恢复）+ `memories/` 真实文件树
（可读、可 grep、可 git）。**记忆平时只以数据库行存在，磁盘上没有 .md 文件**，
这里是唯一落成文件的地方。

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
- **当前时间不能进 system prompt**（破坏缓存），走 `build_runtime_context` 注入到 user 侧。
  不注入的话模型不知道今天几号，也会把自己的身份瞎猜成 Claude。

## 加新模型

实现 `app/llm/provider.py` 里的 `LLMProvider` 协议，在 `factory.py` 注册即可，
chat / memory / jobs 层不用动。OpenAI 兼容的接口可以直接照抄 `deepseek_provider.py`，
通常只需要改 base_url 和模型名。
