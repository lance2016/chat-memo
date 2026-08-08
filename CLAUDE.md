# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

个人 AI 助手：FastAPI + PostgreSQL 后端（`app/`），Next.js 15 App Router 前端（`frontend/`）。
核心特性是把对话沉淀成可查看、可编辑、可回滚的长期记忆。

## 常用命令

开发环境走 Docker Compose（源码挂载 + 热重载），启动时 entrypoint 会自动 `alembic upgrade head`：

```bash
cp .env.example .env
docker compose up -d --build      # 前端 http://localhost:13000，API http://localhost:18000
docker compose logs -f api
docker compose exec api pytest -q
```

后端测试用内存 SQLite（`tests/conftest.py`），不需要数据库，也可以直接在宿主机跑：

```bash
uv run pytest -q
uv run pytest tests/test_memory_store.py -q          # 单个文件
uv run pytest tests/test_tts.py::test_name -q        # 单个用例
uv sync --group dev                                  # 安装含 dev 依赖
```

前端（在 `frontend/` 目录下）：

```bash
npm run test        # vitest run
npm run test -- lib/turns.test.ts
npm run lint        # eslint
npm run typecheck   # tsc --noEmit
```

评测（记忆整理质量，手动触发、要花模型 token、不进 CI）。
**界面入口在记忆页 → 质量评测**；命令行和它共用 `app/eval/service.py` 的同一份编排：

```bash
uv run python -m app.eval noise --repeat 3          # 先量噪声，再解读任何分数
uv run python -m app.eval run                       # 跑一轮，自动和上次结果对比
uv run python -m app.eval run --no-judge            # 只跑第 0/1 层，不花裁判的钱
uv run python -m app.eval export --day 2026-08-06   # 从生产库导出一天去人工标注
```

数据库迁移：

```bash
docker compose exec api alembic revision --autogenerate -m "描述"
docker compose exec api alembic upgrade head
```

Phoenix 链路观测：**默认开启，不需要配任何环境变量**。开关和「是否保存对话正文」
在设置页（开发者 → 链路观测），改完立刻生效（`apply_tracing` 是幂等的状态调和，
开→关走 `uninstrument()`）。界面在 <http://localhost:16006>。

## 配置分层（改配置前必读）

- **`.env` 只放启动必需项**：数据库连接、API Key、外部服务地址、`TZ`、`VAULT_PATH` 等挂载路径。
- **运行时配置在设置页改，存数据库**（`app_settings` 表）：模型、provider、助手规则、
  自定义指令、记忆整理、通知、TTS/ASR 偏好。
- 解析顺序：`conversations.thinking` 会话覆盖 → `app_settings` → `Settings` 代码默认。
  入口是 `app/settings_store.py` 的 `resolve_settings(session)`；**不做缓存**，改完立刻生效。
- 新增可写配置项必须加进 `settings_store.py` 的 `WRITABLE` 白名单（前端表单由它自动渲染）；
  密钥、基础设施、CORS、`api_key` 一律不进白名单。
- 业务代码里**不要直接用 `get_settings()`** 当生效配置——那是启动快照，会漏掉数据库覆盖。

## 架构要点

请求流：浏览器 → Next.js 同源 `/backend` rewrite（`frontend/next.config.ts`）→ FastAPI。
所有 `/api` 路由过 `require_api_key`（`API_KEY` 留空则不校验）。前端不直连任何模型 API。

- `app/main.py` — `create_app()` 装配所有 router；`lifespan` 里起三个后台任务
  （每日整理、通知 ticker、TTS 预热）。目前没有独立 worker，后台任务跑在 API 进程内。
- `app/llm/` — `provider.py` 定义 `LLMProvider`/`ToolExecutor` 协议，
  `target.py` 的 `ModelTarget` 是「调哪个模型」的唯一载体（地址/密钥/模型 ID/
  max_tokens/思考默认），`Settings` 只剩「怎么调」（工具轮次上限、请求快照开关）。
  `factory.py` **按协议**而不是厂商名分发：加一个 OpenAI 兼容服务只需在模型目录加一行
  记录，代码零改动；加一个新协议写一个 provider 类并注册一行。
  `catalog.py` 从数据库解析模型服务/档案，`composite.py` 按工具名路由。
  ⚠️ `ModelTarget.from_settings()` 是**全代码库唯一**允许出现 `provider == "anthropic"`
  的地方；别在别处再判断厂商，要什么就从 target 上取。
- `app/agent.py` — **一次 agent 运行的统一装配**：provider + 工具 + system prompt。
  聊天、每日整理、评测、`/api/debug/prompt` 都走它。工具在 `TOOLKITS` 表里声明
  「怎么建 + 什么用途启用 + 什么条件可用 + 界面上叫什么」，system prompt 的分段和
  `GET /api/tools` 目录都由实际注册的工具推导（schema 直接问 executor 要）——
  **加一个新工具只改这张表**，不要回到各处手写 executor 列表或工具清单。
- `app/memory/` — 三层记忆的 L2，逻辑上是 `/memories` 文件树，物理上是 Postgres 行。
  `store.py` 是虚拟文件系统（每次变更写 `memory_versions` 快照，支持回滚），
  `prompt.py` 组装 system prompt，`paths.py` 做路径穿越校验，
  `audit.py` 是索引一致性校验（纯函数，三处复用：整理后自检、下次整理的 prompt、评测指标）。
- `app/jobs/consolidate.py` — 每日整理（默认凌晨 4 点），产出 L1 摘要、`daily_digests`、
  `open_loops`，并让模型对 L2 做去重/修正/提炼。
- `app/kb/` — 可选的 Obsidian vault 只读接入（`VAULT_PATH`），四个 `kb_*` 工具，
  无索引现场扫描，写保护做在 `:ro` 挂载层。
- `app/tts/` + `app/asr/` — 本地 mlx-audio 服务的代理；共享一把串行锁。
- `app/debug/` — 请求快照环形缓冲（进程内存，不落库），配合 `GET /api/debug/requests`
  和 `/api/debug/prompt` 看清每轮真正发出去的 payload。
- `app/eval/` — 记忆整理的评测。`service.py` 是**编排的唯一实现**，`router.py`（界面）
  和 `cli.py`（命令行）都只是它的外壳 —— 别在任何一个外壳里重写编排逻辑。
  `runner.py` 在一次性内存库里重放**真正的** `Consolidator`（不是简化版），
  `judge.py` 是 LLM-as-judge，`metrics.py` 的 `compare()` 默认判「无法区分」
  除非差异超过噪声。评测不写真实记忆；`eval-runs/` 和 `evals/` 必须挂进容器，
  否则界面和命令行会各写各的历史。

`docs/internals.md` 是设计取舍与踩坑清单的权威文档，改任何一个模块前先读对应章节；
`docs/frontend-api.md` 是前后端契约；`docs/roadmap.md` 是未做事项；
`docs/evaluation.md` 是记忆质量怎么测（三层指标：机械 / 过程 / 模型裁判）。

## 改代码时最容易踩的坑

以下每条都对应过真实缺陷，完整版见 `docs/internals.md`：

- **system prompt 不能放变动内容**（时间戳/日期），否则 prompt cache 前缀失效。
  当前时间走 `build_runtime_context` 注入到 user 侧。
- **消息必须整块存、整块回传**：thinking 块带签名，只抽 text 回传下一轮会 400。
  无签名 thinking（DeepSeek 产生的、中断兜底存的）必须经 `strip_unsigned_thinking` 滤掉。
- **中断的对话要先修复**：孤立的 tool_use 会让之后每条消息 400，`sanitize_history` 负责补齐。
- **SSE 生成器自己管数据库会话**，不能用 `Depends(get_session)` —— 依赖在请求函数返回时就清理了。
- **同一会话不能并发生成**，已有按会话的锁；去掉会让历史错乱成 `user,user,assistant,assistant`。
- **`max_tokens` 大时必须流式**，否则撞 SDK 的 HTTP 超时。
- **「一天」按本地时区切**，用 `local_day_bounds`（`app/timeutils.py`）；容器必须设 `TZ`。
- **记忆/vault 路径来自模型输出**，`validate_path` 的穿越校验不能省；导出成真实文件时再验一次。
- **记忆索引条目只写主题不写结论**（限 25 字，`tests/test_memory_prompt.py` 钉住），
  否则渐进式披露退化成全量注入。
- **搜索是 `pg_trgm` 三元组子串匹配**，不是全文检索；正文冗余在 `messages.search_text` 列上。
- **TTS 合成必须串行**（MLX 一次只加载一份权重）；朗读文本的清洗和切句在服务端
  （`app/tts/segment.py`），前端按 Markdown 切的位置对不上。
- **容器里连宿主机 TTS 走 `host.docker.internal:8001`**，用独立的 `TTS_DOCKER_BASE_URL` 覆盖。

## 约定

- Python 3.12+，全量类型注解，异步 SQLAlchemy 2.0；包管理用 `uv`，不用 `pip`。
- 注释用中文写「为什么」，不写「做了什么」；仓库现有注释密度较高，跟随周边风格。
- Commit 用 Conventional Commits（`feat/fix/refactor/docs/chore(scope): 描述`），中文描述。
