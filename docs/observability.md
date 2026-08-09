# 日志与可观测性

这份文档回答三个问题：现在的日志差在哪、重构成什么样、以及一个人用的可观测栈该选谁。

## 一、现状盘点

代码事实（不是猜测）：

| 组件 | 位置 | 现状 |
|---|---|---|
| 日志配置 | `app/logging_setup.py` | 彩色紧凑单行、压噪音 logger、接管 uvicorn 三个 logger。只有 stdout 一个出口 |
| 叙事日志 | `app/chat/service.py` 等 51 处 `logger.*` | 中文散文 + emoji，`→ conv#12 [deepseek] 用户说…` / `⚙ view` / `← conv#12 …` |
| 请求快照 | `app/debug/recorder.py` | 进程内 deque(20)，默认关（`debug_prompts`），重启即清空 |
| 快照接口 | `app/debug/router.py` | `/api/debug/requests`、`/api/debug/prompt` |
| 快照界面 | `settings-page.tsx` 的 `DebugDialog` | 设置页里的一个弹窗 |
| usage | `messages.usage` 列 | 每轮的 token 数存下来了，但没有任何聚合视图 |

### 五个具体痛点

1. **日志不留存**。只有 stdout。`docker compose logs` 滚过去就没了，"上周那次超时"无法回查。
2. **无法关联**。同一轮对话的日志散在十几行，没有共同标识。两个会话并发时日志交错，`→ conv#12` 和 `⚙ view` 之间没有任何东西证明它们属于同一次请求——只能靠时间顺序猜，而流式响应本来就是交错的。
3. **不可查询**。散文格式无法按字段过滤。"找出所有耗时 > 10s 的模型调用"这种问题现在只能用眼睛扫。
4. **请求记录不是看板**。内存、20 条、默认关、藏在设置页弹窗里。它是「排查时临时打开的窥视孔」，不是「随时能看的记录」。
5. **没有成本与趋势**。token 存了但没算过钱，没有 P95 延迟、错误率、工具失败率、后台任务成功率。定时任务（consolidate / notify ticker）失败了只留一行 warning，没人会发现。

## 二、设计目标

要达到的：

- 一次用户请求在日志里是**一个可追踪的整体**，能用一个 ID 串起从 HTTP 进来到最后一次工具调用的全过程。
- 日志同时**给人看**（终端，保留现在的可读性）和**给机器查**（JSON，进日志库）。
- 所有模型调用有**持久化记录和看板**：能按会话/模型/用途/状态筛，能看 token、费用、延迟、错误，能点开看完整请求体。
- 可观测组件**不能影响主链路**：埋点失败、后端挂了，聊天照样работа。
- ~~外部依赖**可选**~~。**这条已推翻**，见下面「为什么后来改成默认开启」。

明确不做的：

- 不做多租户、不做采样、不做分布式 trace 传播（单进程单人用，没有下游服务）。
- 不自己实现日志存储、检索、trace UI。那都是解决过的问题。
- 不为了「以后可能有」而抽象。

## 三、架构

```
                        ┌─ 终端（人读）  ColorFormatter，保留现在的样子 + trace 短码
   业务代码              │
   logger.info() ───────┤
                        └─ JSON stdout ──→ Vector ──→ VictoriaLogs（日志检索，可选）

   anthropic SDK ──┐
   openai SDK ─────┼── OpenInference 自动埋点 ──→ OTLP ──→ Phoenix（LLM 看板）
                   │
        ContextVar / OTel span：trace_id · session_id(=conversation_id) · purpose
```

### 分界线：三类数据各归各家

这是整个设计最重要的一条决定。

**模型调用 → Phoenix**（`arizephoenix/phoenix`，一个容器）。一次 agent loop 天然是一棵 span 树：多次 iteration、每次的完整 messages、tool 定义与结果、token、延迟、错误全在里面。这套东西自己写要一张表加一整页界面，而 Phoenix 免费给，且 trace UI 比手写的强。

**基础链路**：本项目三条模型链路正好被两个自动埋点包覆盖——

| 链路 | SDK | 埋点包 |
|---|---|---|
| Anthropic 聊天 (`anthropic_provider.py`) | `anthropic` | `openinference-instrumentation-anthropic` |
| DeepSeek 聊天 (`deepseek_provider.py`) | `AsyncOpenAI` + `base_url` | `openinference-instrumentation-openai` |
| 硅基流动/智谱标题 (`llm/title.py`) | `AsyncOpenAI` + `base_url` | 同上 |

`consolidate.py` 和 `notify/compose.py` 走的也是这两条 provider。自动埋点负责协议级的
token、延迟和错误信息；但流式调用在不同 SDK/instrumentation 版本上不保证把完整消息正文
写入 Phoenix，所以 provider 发请求的地方还会创建一个应用自己的 `LLM` span，把**实际
发送的完整 payload** 写入 `input.value`，把完整响应写入 `output.value`。这样 Phoenix
里不再依赖某个自动埋点版本是否捕获正文，也能直接看到当前请求的 system、history、tools
和本轮用户消息。

**普通日志 → 日志库或 `docker logs`**。HTTP 访问日志、异常栈、DB 日志、后台任务 stdout。Phoenix 是 trace 后端不是日志后端，这层它不管，得自己解决（第七节）。

**长期费用聚合 → 可选的一张极薄表**。见第五节，默认先不做。

### 为什么原本打算不引 OTel，现在翻案

初版判断是「单进程应用的 span 树信息量不如结构化日志，不值得引 SDK + 一个容器」。这个判断错在只看 span 树本身，没算 Phoenix 附带的东西：请求列表、payload 查看、session 聚合、成本核算、eval，以及 **prompt playground**——能直接改 system prompt 重跑。这个项目的 system prompt 是拼出来的（`memory/prompt.py`），"改一个字看效果"现在只能重启服务发消息，playground 的价值很高。

代价说清楚：多了 OTel SDK 这层依赖，而且**看板在 `localhost:6006` 而不是在应用里**。如果「集成到一起」的要求是"我的 workspace 里多一页"，Phoenix 不满足，只能从 topbar 开一个链接过去。

### 模块布局

```
app/obs/
  __init__.py      # 对外只暴露 trace() / bind()
  context.py       # trace/session/purpose，读写 OTel 当前 span
  logging.py       # 从 logging_setup.py 搬来 + TraceFilter + JsonFormatter
  middleware.py    # HTTP 中间件：开 trace、记录耗时和状态码
  tracing.py       # phoenix.otel.register() + 两个 instrumentor，一次性初始化
```

`app/logging_setup.py` 保留为薄转发（`from app.obs.logging import *`），避免一次改 20 个 import。`app/debug/` 整个可以**删掉**（recorder + log + router）——Phoenix 完整覆盖了它的职责，而且没有 20 条上限、重启不丢。唯一要保留的是 `/api/debug/prompt`（不发消息就能看当前 system prompt），把它挪到别处，比如 `app/memory/router.py`。

### trace context

trace id **从 OTel 当前 span 取**，不自己生成——这样日志里的 `trace_id` 和 Phoenix 里的 trace 是同一个值，`grep` 到一行日志就能直接去 Phoenix 里搜到那次调用的完整 span 树。这是两套系统最有价值的联动点，也是必须用 OTel 格式的理由。

```python
# app/obs/context.py
def current_trace_id() -> str:
    span = trace.get_current_span()
    ctx = span.get_span_context()
    return f"{ctx.trace_id:032x}" if ctx.is_valid else ""

@contextmanager
def trace(kind: str, name: str, **fields) -> Iterator[None]:
    """HTTP 中间件和后台任务的入口。kind: http | job | ticker"""
```

- HTTP 请求：中间件开 span。
- 后台任务：`run_daily_consolidation` / `run_notification_ticker` / `notify.sweep` 各自在循环体里开 span。**这条很关键**——定时任务现在完全没有执行记录，失败了没人知道。
- 会话与用途：用 OpenInference 的 context manager 打在 span 上：

```python
from openinference.instrumentation import using_session, using_metadata

with using_session(str(conversation.id)), using_metadata({"purpose": "chat"}):
    async for event in self._drive(...):
```

`session_id` 用 conversation_id，Phoenix 的 Sessions 视图就能把一个会话的所有 trace 聚在一起看——正好对上这个项目的数据模型。`purpose` 区分 chat / title / consolidate / notify_copy，否则「费用涨了」无法归因。这两个 context manager 是**全部**的手工埋点工作量。

### 双 formatter

`LOG_FORMAT=pretty|json`，默认 pretty（本地开发），compose 里设 json。

pretty 只在现有格式上加 trace 短码，其他不变：

```
14:22:07 INF chat     a3f19c → conv#12 [deepseek] 帮我看下明天的日程
14:22:09 INF chat     a3f19c   ⚙ view timeline/2026-08-08.md
14:22:11 INF chat     a3f19c ← conv#12 2 次工具 · 3821 tok · 4.2s
```

有了这 6 位短码，`docker compose logs api | grep a3f19c` 就是一次请求的完整故事，而且这个短码还能拿去 Phoenix 里搜。痛点 2 的全部解法就是一个 `logging.Filter` 加 formatter 里一个字段。

json 出口每行一个对象，字段名对齐 OTel 语义约定：

```json
{"time":"2026-08-07T14:22:11.331+08:00","level":"info","logger":"app.chat.service",
 "message":"← conv#12 2 次工具 · 3821 tok · 4.2s","trace_id":"a3f19c…","session_id":"12",
 "service.name":"chat-memo-api"}
```

写 JSON 时**必须去掉 ANSI 转义**。现在 `colorize()` 只看 `NO_COLOR` 环境变量就无条件插色码（`logging_setup.py:108`），进了日志库会变成垃圾字符。要改成读同一个全局开关。

## 四、Phoenix 落地细节

### compose

```yaml
  phoenix:
    image: arizephoenix/phoenix:latest
    container_name: chat-phoenix
    restart: unless-stopped
    profiles: [obs]          # 默认不启动，`docker compose --profile obs up -d` 才起
    environment:
      # Phoenix ≥ 9.0：到期自动清 trace。里面存的是完整对话原文，这个必须配。
      PHOENIX_DEFAULT_RETENTION_POLICY_DAYS: ${PHOENIX_RETENTION_DAYS:-14}
      PHOENIX_WORKING_DIR: /mnt/data
      TZ: ${TZ:-Asia/Shanghai}
    ports:
      - "127.0.0.1:${PHOENIX_PORT:-16006}:6006"   # UI + OTLP HTTP，只给本机访问
    volumes:
      - phoenix_data:/mnt/data
```

存储用**默认的 SQLite + volume**，不复用 `chat-db`。Phoenix 也支持 `PHOENIX_SQL_DATABASE_URL` 指向 Postgres 14+，但接到业务库上会让 span 数据和对话数据共享磁盘与连接池，还得在 `app/backup.py` 里排除——为省一个 volume 引一堆纠缠不值得。

api 服务加两个环境变量：

```yaml
      PHOENIX_COLLECTOR_ENDPOINT: ${PHOENIX_COLLECTOR_ENDPOINT:-http://phoenix:6006}
      OBS_TRACING: ${OBS_TRACING:-0}    # 0 时完全不初始化 OTel，零开销
      OBS_TRACE_READS: ${OBS_TRACE_READS:-0}  # 默认只追踪 POST/后台任务，避免 GET 轮询刷屏
      OBS_TRACE_HTTP_PATHS: ${OBS_TRACE_HTTP_PATHS:-/api/chat,/api/jobs/consolidate}  # HTTP trace 白名单
```

### 应用侧初始化

```python
# app/obs/tracing.py
def setup_tracing(settings: Settings) -> None:
    """没配 endpoint 或没开开关就直接返回 —— 不装 Phoenix 也要能跑。"""
    if not settings.obs_tracing or not settings.phoenix_collector_endpoint:
        return
    from phoenix.otel import register
    from openinference.instrumentation.anthropic import AnthropicInstrumentor
    from openinference.instrumentation.openai import OpenAIInstrumentor

    provider = register(project_name="chat-memo", endpoint=..., batch=True)
    AnthropicInstrumentor().instrument(tracer_provider=provider)
    OpenAIInstrumentor().instrument(tracer_provider=provider)
```

`batch=True`（BatchSpanProcessor）不是可选项——同步导出会把网络往返算进用户等待时间。导出失败由 OTel SDK 内部吞掉并重试，不会冒到主链路。这满足「不能影响主链路」这条目标，不需要我们自己写队列。

依赖（4 个包，加到 `pyproject.toml` 的可选组里）：

```
arize-phoenix-otel
openinference-instrumentation-anthropic
openinference-instrumentation-openai
opentelemetry-exporter-otlp
```

放进 `[project.optional-dependencies]` 的 `obs` 组，这样不想用的人 `uv sync` 不会装。

### 落地第一件事：验证流式埋点

**这是整个方案唯一的真实风险，必须第一步验证，不要假设它可以工作。**

两条链路都是流式的：`anthropic_provider.py:103` 用 `client.messages.stream()` 上下文管理器 + `get_final_message()`，`deepseek_provider.py` 用 openai 的 `stream=True`。自动埋点对流式 + tool_use 的覆盖程度需要实测确认三件事：

1. span 里有没有完整的 `messages` 和 `tools`（决定能不能替代 `debug/recorder`）
2. `llm.token_count.*` 有没有值（决定成本核算能不能用）
3. agent loop 的多次 iteration 是不是同一个 trace 下的多个 span（决定 trace 视图有不有用）

验证方式：开着 Phoenix 跑一轮带工具调用的对话，去 UI 里逐条对。**如果 1 缺失**，`debug/recorder` 就不能删，保留它作为 payload 的补充。**如果 2 缺失**，成本这块要么放弃要么退回自建表。先花二十分钟验证，再决定后面做多少。

### 降噪与前端联动

- `OBS_TRACE_READS=false` 时，`GET` 请求和 `/health` 只保留普通访问日志，不创建 Phoenix span；聊天 `POST /api/chat`、模型子 span 和后台任务仍然保留。
- `OBS_TRACE_HTTP_PATHS` 控制 HTTP 入口白名单，默认只有 `/api/chat` 和手动整理入口；像 `/api/tts/stop`、设置保存、归档等控制请求不建 Phoenix span。需要排查某个入口时再临时追加路径。
- 手工 span 写入 `openinference.span.kind`，HTTP/任务显示为 `CHAIN`，工具类 span 可显示为 `TOOL`，避免 Phoenix 列表全部变成 `unknown`。
- LLM span 使用 `openinference.span.kind=LLM`；input/output 以 JSON 写入 `input.value` /
  `output.value`。文本和工具内容会保留，图片正文只保留 MIME/大小占位符，不写入完整
  base64；Phoenix retention 和本地端口访问限制仍必须保持开启。
- 聊天 SSE 会发送当前完整 `trace_id`。对话顶部显示短码，复制按钮复制完整 ID，打开按钮只打开本机 Phoenix UI；浏览器不直连 Phoenix API，也不接触 collector endpoint。
- Phoenix 默认绑定 `127.0.0.1`，因为 trace 里包含完整 prompt/response。需要局域网访问时再显式修改 compose 端口绑定。
- Phoenix compose 已关闭 UI telemetry、外部资源、MCP/MCP code mode 和 Prometheus；本地只保留 UI、OTLP 收集和 SQLite trace 存储。旧 span 不会因新过滤规则消失，需按时间/项目清理，或等待 retention 到期。

### 通知 ticker 的决策记录

`notify.tick` 不只表示「定时器执行过」。每分钟 tick 会在同一个 span 上记录配置、决策和扫描结果，并在 Phoenix 的 Events 面板写入可读事件：

| 字段 / 事件 | 含义 |
|---|---|
| `notify.tick.evaluated` | 本轮读取到的通知开关、配置通道和实际可用通道 |
| `notify.tick.skipped` | 没有执行扫描的原因：`disabled` 或 `no_channel` |
| `notify.scan_performed` | 是否真正查询了待提醒事项 |
| `notify.due_candidate_count` | 本轮命中的到点事项数量，受 `notify.due_limit` 限制 |
| `notify.briefing_*_count` | 今日、逾期、长期待确认事项的数量 |
| `notify.delivery.completed` | 每条通知的送达通道、尝试次数和失败数量 |
| `notify.tick.completed` | 本轮最终送达数量，以及 `sent` / `no_match` 决策 |

因此看到 `notify.tick` 但没有子任务时，直接看 Events：如果是 `notify.tick.skipped`，事件里的 `reason` 和 `detail` 就是本轮为什么没有继续；如果执行了扫描，则看 `notify.sweep.completed`、`notify.due.evaluated` 和 `notify.delivery.completed`。

### 成本核算的一个手工步骤

Phoenix 内置 OpenAI / Anthropic 的价格表，但 **DeepSeek 和 Qwen 要自己加**：UI 里 Settings → Models → Add new model，填模型名 regex、provider、input/output 单价（美元 / 百万 token），可以设生效起始日期。

这是**只能在 UI 点、不能声明式配置**的一步，容器重建后靠 volume 里的 SQLite 保住。记在这里免得以后忘了为什么费用显示是 0。查单价时去各家定价页核对，别照记忆里的数字，缓存读写单价和普通 input 差一个数量级。

## 五、可选：应用内的费用视图

Phoenix 覆盖了排查和分析，但有两件事它做不好：

- **年度费用**。retention 到期 trace 就被清了，`14d` 之后"今年花了多少"就没了。
- **应用内可见**。想在 workspace 里直接看到今日用量，得调 Phoenix API 或者自己存。

如果需要，加**一张极薄的表**（注意：这是 Phoenix 之外的额外工作，**建议先跑两周再决定要不要**）：

```sql
-- 一次模型调用的账单摘要。不存 payload —— 那是 Phoenix 的活。
CREATE TABLE llm_usage (
    id             BIGSERIAL PRIMARY KEY,
    trace_id       TEXT        NOT NULL,   -- 与 Phoenix 里的 trace 对得上
    at             TIMESTAMPTZ NOT NULL,
    purpose        TEXT        NOT NULL,   -- chat | title | consolidate | notify_copy
    provider       TEXT        NOT NULL,
    model          TEXT        NOT NULL,
    conversation_id INTEGER,
    duration_ms    INTEGER,
    input_tokens   INTEGER NOT NULL DEFAULT 0,
    output_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens  INTEGER NOT NULL DEFAULT 0,
    cost_usd       NUMERIC(10, 6),
    error          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX llm_usage_at_idx ON llm_usage (at DESC);
```

一行约 100 字节，一天几百次调用一年也才几十 MB，**永久保留**。写入走一个自定义 `SpanProcessor`（`on_end` 时把 span 属性转成一行），复用 OTel 已有的批量机制，不用自己写队列。

前端就是 topbar 或设置页里一个小卡片，不是一整页：

```
┌ 今天 42 次 · 118k tok · $0.031  ·  本月 $0.87 ────── 在 Phoenix 中查看 ↗ ┐
```

**不加图表库**。项目现在零图表依赖，真要 sparkline 就手写二十行 SVG，不为一根折线引 300KB。

## 六、可观测栈选型：一个人该用什么

个人本地的约束和企业完全不同：**内存是硬约束**（Docker Desktop 通常只给 4~8GB，而 Postgres + api + frontend + mlx-audio 已经占掉不少），**运维时间是零**（没人愿意为自己的项目调 Grafana dashboard），**数据量极小**（一天几万行日志）。

### LLM 侧

| 方案 | 容器数 | 内存量级 | 个人适配度 |
|---|---|---|---|
| **Arize Phoenix** | 1（SQLite） | ~300-500MB | ★★★★★ 推荐 |
| Langfuse 自托管 v3 | 5（CH + Redis + MinIO） | 2GB+ | ★★☆☆☆ LLM trace 表达最好，但太重 |
| Laminar / Helicone | 3+ | 1GB+ | ★★☆☆☆ 偏网关/团队场景 |
| 自建表 + 自写看板 | 0 | 0 | ★★★☆☆ 数据完全自主，但工作量是 Phoenix 的十倍 |

**Phoenix 胜出的理由**：单容器、SQLite 就能跑、OTLP 原生、自带 retention 策略、cost 核算内置、附送 playground 和 eval。Langfuse 功能更强但要 ClickHouse + Redis + MinIO 四件套，个人笔记本上这个代价换不回价值。

### 日志侧

| 方案 | 容器数 | 内存量级 | 自带 UI | 个人适配度 |
|---|---|---|---|---|
| **VictoriaLogs + Vector** | 2 | ~150MB | 有（vmui，LogsQL） | ★★★★★ 要检索就选它 |
| **Dozzle** | 1 | ~15MB | 有，但**只看实时不存储** | ★★★★☆ 只想在浏览器看日志就够了 |
| OpenObserve | 1 | ~200MB | logs/metrics/traces 全包 | ★★★★☆ 想一体化选它 |
| Grafana + Loki + Alloy | 3 | ~600MB | 要自己搭 dashboard | ★★★☆☆ 会 Grafana 才划算 |
| SigNoz | 5+（ClickHouse） | 2~4GB | APM 很完整 | ★★☆☆☆ 笔记本上偏重 |
| Elastic + Kibana | 2 | 2GB+ | 有 | ★☆☆☆☆ 杀鸡用牛刀 |

> 内存数字是量级估算（空载 + 个人数据量），不是基准测试结果。

**加上 Phoenix 之后，日志侧的优先级明显下降**。Phoenix 已经覆盖了最需要回溯的那类问题（模型调用出错、变慢、上下文不对）。剩下的日志——HTTP 404、DB 连接失败、TTS 起不来——用 `docker compose logs` 现查其实够用，因为它们通常是"现在坏了"而不是"上周坏过"。

所以建议：**先只上 Phoenix，日志侧等真的被"日志滚没了"坑到再加**。要加的话，`docker compose logs` 之上最省的一步是 Dozzle（一个容器 15MB，浏览器里看实时日志、按容器过滤），真需要留存和 LogsQL 检索再上 VictoriaLogs + Vector。

**不选 Grafana 系**的核心理由：Grafana 的价值在于统一多数据源和做 dashboard，而这里业务指标已经归 Phoenix 了，剩下的日志检索用 vmui 就够。为了查日志装 Grafana 是本末倒置。

### 日志库配置（阶段 4 再做）

```yaml
  logs:
    image: victoriametrics/victoria-logs:latest
    container_name: chat-logs
    restart: unless-stopped
    profiles: [obs]
    command: [-storageDataPath=/vlogs, -retentionPeriod=30d]
    ports:
      - "${LOGS_PORT:-19428}:9428"   # vmui: /select/vmui
    volumes:
      - vlogs:/vlogs

  collector:
    image: timberio/vector:latest-alpine
    container_name: chat-collector
    restart: unless-stopped
    profiles: [obs]
    depends_on: [logs]
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./ops/vector.yaml:/etc/vector/vector.yaml:ro
```

```yaml
# ops/vector.yaml
sources:
  containers:
    type: docker_logs
    include_containers: [chat-api, chat-frontend, chat-db]
transforms:
  parsed:
    type: remap
    inputs: [containers]
    source: |
      # api 输出 JSON 时解析出字段，其他容器保留原始文本
      parsed, err = parse_json(.message)
      if err == null { . = merge(., object!(parsed)) }
sinks:
  vlogs:
    type: elasticsearch
    inputs: [parsed]
    endpoints: ["http://logs:9428/insert/elasticsearch/"]
    mode: bulk
    api_version: v8
    query:
      _msg_field: message
      _time_field: time
      _stream_fields: container_name
```

VictoriaLogs 也支持 syslog 入口，给 api 配 `logging: {driver: syslog, ...}` 能省掉 Vector 只剩一个容器。代价是 `docker compose logs api` 拿不到本地副本——容器起不来时排查会很难受，不推荐作为默认。

### 告警：复用 Bark，不装 Alertmanager

个人场景装 Prometheus + Alertmanager 只为发通知是荒谬的——**`app/notify/` 已经能推 Bark 了**。规则挂在 SpanProcessor 或后台任务里：

- 同一 purpose 连续失败 ≥ 3 次 → 推一条
- 单日费用超过阈值 → 推一条（一天只推一次）
- 后台任务（consolidate / notify sweep）抛异常 → 推一条

这是个人项目相对企业方案最实在的优势：告警通道本来就有。

## 七、落地阶段

每阶段独立可用可验证。

### 当前实现进度（2026-08-08）

阶段 0/1/2 的应用侧骨架已落地：

- `app/obs/` 提供可选的 Phoenix 注册、HTTP 流式 trace、session/purpose context，以及
  pretty/JSON 双 formatter；`app/logging_setup.py` 保留为兼容转发。
- chat / title / consolidate / notify_copy 和后台 consolidation / notification ticker
  已接入 context；未安装 obs 依赖或 `OBS_TRACING=0` 时继续走无 trace 分支。
- compose 已加入 `obs` profile 的 Phoenix + SQLite volume，默认 retention 14 天。
- 默认跳过 GET/health span，手工 span 标注 OpenInference kind；对话 SSE 会把 trace_id
  传给前端，顶部可复制完整 ID 并打开本机 Phoenix。

真实的阶段 0 覆盖验证仍需在有模型 key 的环境中完成。安装可选依赖并启动 Phoenix：

```bash
uv sync --extra obs
INSTALL_OBS=1 OBS_TRACING=1 docker compose --profile obs up -d --build api phoenix
```

然后跑一轮带 `view` 工具调用的对话，在
`http://localhost:16006` 核对完整 messages/tools、`llm.token_count.*` 和多次 agent
iteration 是否在同一 trace 下。确认 payload 完整前保留 `app/debug/`；确认后再做阶段 3
清理。直接在宿主机运行 API 时，`PHOENIX_COLLECTOR_ENDPOINT` 应填
`http://localhost:16006`；应用会补齐 OTLP HTTP 的 `/v1/traces` 路径。

**阶段 0 — 验证流式埋点**（半小时，决定后面做多少）
- `pyproject.toml` 加 `obs` 可选依赖组，compose 加 phoenix 服务，`app/obs/tracing.py` 十几行初始化
- 跑一轮带工具调用的对话，去 `localhost:16006` 逐条核对第四节那三件事
- 这一步做完就已经有一个能用的 LLM 看板了

**阶段 1 — trace + 双 formatter**（改动最小、收益最大）
- 新建 `app/obs/{context,logging,middleware}.py`，`logging_setup.py` 转成转发
- `colorize/dim` 改为读全局开关，JSON 模式不插 ANSI
- 后台任务加 span：`jobs/scheduler.py` 两个循环、`notify/sweep.py`
- 验证：`docker compose logs api | grep <短码>` 拿到一次完整请求的所有行；同一个短码在 Phoenix 里搜得到；`LOG_FORMAT=json` 时每行是合法 JSON 且不含转义字符

**阶段 2 — session 与 purpose**
- `ChatService` 包一层 `using_session(conversation.id)`，5 处调用点各自 `using_metadata({"purpose": ...})`
- Phoenix UI 里手动加 DeepSeek / Qwen 的价格
- 验证：Sessions 视图能按会话聚合；按 purpose 筛得出 title 调用；费用不再是 0

**阶段 3 — 清理旧代码**
- 删 `app/debug/{recorder,log,router}.py` 及前端 `DebugDialog` 那套（**前提是阶段 0 确认 payload 完整**）
- `/api/debug/prompt` 挪到 `app/memory/router.py`
- `debug_prompts` 配置项含义变了：不再控制内存快照，改成控制 Phoenix 是否记录完整 messages（或直接删掉，用 `OBS_TRACING` 代替）
- 验证：`pytest` 全绿（`tests/test_debug_requests.py` 要跟着改或删）；前端 `tsc --noEmit` + `vitest` 通过

**阶段 4 — 可选，按需**
- 应用内费用卡片（第五节，先观察两周再定）
- Dozzle 或 VictoriaLogs（被日志滚没了坑到再加）
- Bark 告警规则

## 八、取舍与风险

- **流式埋点覆盖度是唯一硬风险**。见第四节，阶段 0 先验。没验之前不要删 `app/debug/`。
- **隐私**：Phoenix 里存的是完整对话原文和记忆正文，**默认全存**，不受 `debug_prompts` 控制。所以 `PHOENIX_DEFAULT_RETENTION_POLICY_DAYS` 必须配（建议 14 天），端口不要暴露到局域网。`app/backup.py` 不涉及 —— Phoenix 用自己的 volume，天然隔离。
- **Phoenix 在 workspace 之外**。这是选它最大的让步。换成自建看板能做到应用内，但工作量大约十倍，且做出来的 trace 视图不会比它好。
- **手工配价格**只能在 UI 点，容器重建靠 volume 保住。删了 volume 要重配。
- **`purpose` 依赖调用点手工传值**。漏一处那部分费用就归不了因。落地时 grep 确认 5 处全覆盖，之后新增调用点也要带上——这是唯一需要人维护的约定。
- **OTel SDK 是新依赖面**。放进可选依赖组、用 `OBS_TRACING` 开关兜底，确保「不装 Phoenix 也能跑」是被测试覆盖的路径，而不是理论上成立。
- **不做的事情要守住**：不要因为「顺手」就加采样、加多后端抽象、加自定义 dashboard。这套东西的价值在于一个容器和几十行胶水代码，复杂度上去了就没人维护了。
