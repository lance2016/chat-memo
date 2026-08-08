# 路线图

跨机器开发的交接清单：**按优先级**记还没做的事，每条带证据和文件位置。
动手前先核对现状 —— 有些点可能已经被另一台机器上的进度做掉了。

- 已修复缺陷的原因档案（防复发注记）在 **[fixes.md](fixes.md)**
- 前端的体验与架构改造另有分阶段计划：**[roadmap-frontend.md](roadmap-frontend.md)**
- 时间事项模块的设计、边界和阶段计划见 **[timeline.md](timeline.md)**
- 日志与可观测性的完整方案见 **[observability.md](observability.md)**
- 备份与恢复演练见 **[backup.md](backup.md)**
- 记忆质量怎么测、指标怎么选见 **[evaluation.md](evaluation.md)**

## 排序原则

1. **产品价值 = 记忆质量 × 可靠性**。这是个记东西的助手，"静默不运转"比"报错"危险得多 ——
   报错会被看见，静默失败要过好几天才隐约觉得"它怎么不记得了"。保命项永远排最前。
2. **数据驱动，不拍脑袋**。好几条决策明确写着"等信号"（KB 写权限看 `kb_reads`，
   性能优化看实测），而信号来自可观测性 —— 所以它排在功能前面。
3. **单人单机，不为不存在的规模付税**。评审过并否决的东西记在"明确不做"，
   免得反复讨论。

---

## P0 — 保命：核心循环与数据安全

### 1. 每日整理可靠运转

**现状**：`consolidate_auto` 默认关（`app/config.py`），靠人手动 `POST /api/jobs/consolidate`。
原因写在 `.env` 注释里：进程一重启计时器就从头开始，笔记本凌晨多半在睡眠，定时器很容易
整天不触发。**一个帮人记事的助手，自己的记忆整理却依赖人记得去触发。**

解法在这个仓库里已经验证过一遍：notify 模块的补跑式扫描（`app/notify/sweep.py` 开头
写着同一条教训）—— 查"该做而没做的"，不做精确定时，睡醒就补。照搬：

- 加 `consolidation_runs` 表记录每次执行（时间、状态、错误）
- 启动时 + ticker 里查"昨天该整理但没记录"，有就补跑
- ~~顺带：整理完成后**机械校验** `MEMORY.md` 索引与实际记忆文件是否一致，差异喂回下次
  整理 prompt~~ ✅ 已实现（`app/memory/audit.py`，方案见
  [evaluation.md](evaluation.md) 第三节）：整理后自检写进 `ConsolidationResult`
  和日志，问题清单进下次整理的 prompt，另有 `GET /api/memories/audit` 随时可查

**验证**：改系统时间或注入时钟跨过 `consolidate_hour`，杀进程重启，观察补跑且
`consolidation_runs` 有记录。索引校验部分已由 `tests/test_memory_audit.py` 和
`tests/test_consolidate.py` 钉住。

### 2. ~~备份闭环~~ ✅ 已实现（剩一个挂载决策）

方案与恢复手册见 **[backup.md](backup.md)**。

- ~~纯手动~~ → 补跑式 ticker（判据是「今天备份过没有」，文件名就是记录，不建表），
  默认开，留最近 14 份，开关和份数在设置页
- ~~恢复从没演练过~~ → **2026-08-08 真跑过一遍**：干净的 pgvector 容器 →
  `pg_restore` → 五张表行数全对 + 记忆正文抽查 → 真后端连上去 `/health` ok、
  API 读得到数据、索引校验结果和生产库一致。每一步命令都写进手册了

**还剩一件事，是挂载决策不是代码**：`backups/` 仍和数据同一块盘，磁盘坏了一起走。
改 compose 里一行挂到 iCloud / 外置盘 / 另一台机器即可，见 backup.md「同一块盘」那节。

---

## P1 — 放大器：眼睛与守门

### 0. 评测（✅ 脚手架已就绪，欠的是标注）

方案与指标选型见 [evaluation.md](evaluation.md)，代码在 `app/eval/`，
用法在 [../evals/README.md](../evals/README.md)。

**剩下的唯一一件事是人工标注**：`evals/cases/` 现在那 6 条是编的示例，覆盖的是
「想得到的」失败模式。真实分布要用 `python -m app.eval export --day` 从自己的对话
里导 10～20 天出来手工标 `expect`。这件事没法外包给模型 —— 让它标期望再拿去评它，
等于让它自己出考卷。

标完之后，下面这些「等信号」的条目才真的等得到信号：P2-5 的质量收益、
P3 里索引分层和写记忆时缓存失效那几条。

### 3. 可观测性：接 Phoenix（应用侧阶段 0/1/2 已落地，已补降噪与 trace 联动）

现在的日志只有 stdout 一个出口：不留存、不可查询、一轮对话的十几行没有共同标识；
请求快照是内存 20 条 + 默认关（`app/debug/recorder.py`）；token 存了没算过钱；
定时任务失败只留一行 warning。完整方案与选型论证见 [observability.md](observability.md)。

核心结论：**用 Arize Phoenix（一个容器 + SQLite）替掉自建看板** —— 三条模型链路
（anthropic SDK、DeepSeek 和硅基流动都走 `AsyncOpenAI`）正好被
`openinference-instrumentation-{anthropic,openai}` 全覆盖，5 处调用点零手工埋点，
就有请求列表、完整 payload、session 聚合、成本核算、prompt playground。

| # | 阶段 | 内容 |
|---|---|---|
| 0 | **先验证流式埋点** | 半小时。两条链路都是流式，自动埋点对流式 + tool_use 的覆盖度**不能假设**。确认三件事：span 里有没有完整 messages/tools、`llm.token_count.*` 有没有值、agent loop 多次 iteration 是否同一 trace。**结果决定后面做多少** |
| 1 | trace + 双 formatter | `app/obs/`，trace id 取 OTel 当前 span（日志短码能直接去 Phoenix 搜）。`LOG_FORMAT=json` 时不能插 ANSI —— 现在 `colorize()` 只看 `NO_COLOR` 就无条件上色 |
| 2 | session 与 purpose | `using_session(conversation.id)` + `using_metadata({"purpose": ...})`。Phoenix UI 手动加 DeepSeek / Qwen 价格（内置表只有 OpenAI / Anthropic，**只能在 UI 点**） |
| 2.5 | 降噪与联动 | 默认跳过 GET/health span；手工 span 标注 OpenInference kind；对话顶部显示、复制 trace_id，并可打开本机 Phoenix |
| 3 | 删旧代码 | `app/debug/` 及前端 `DebugDialog`，**前提是阶段 0 确认 payload 完整**。`/api/debug/prompt` 挪走保留 |
| 4 | 按需 | 应用内费用卡片（retention 到期 trace 就清，年度费用会丢）、Dozzle 或 VictoriaLogs、复用 Bark 做告警 |

两个已知让步：Phoenix 界面在 `localhost:6006` **不在 workspace 里**；它默认全存完整
对话原文，`PHOENIX_DEFAULT_RETENTION_POLICY_DAYS` 必须配。

### 4. ~~最小 CI~~ ✅ 已实现

`.github/workflows/ci.yml`（后端 pytest + ruff，前端 typecheck + lint + vitest + build）
和本地同款的 `make check`。

**已验证**：在共享组件里注入 `useSearchParams()`，`next build` 如期报
「should be wrapped in a suspense boundary」并非零退出 —— 正是 fixes.md 第一条那个错。
类型检查和单测都发现不了它，所以 `next build` 这步不能省。

顺带确认了一件更要紧的事：**整套测试在没有 `.env`、没有任何密钥的环境里能过**
（593 passed）。这是 CI 能存在的前提，也是一条要守住的纪律 —— 一旦某个用例依赖
真实 key，CI 就再也跑不起来了。

---

## P2 — 功能：用了才涨价值

### 5. 整理任务的回查原文工具

给每日整理加一个只读的「回查对话原文」工具。摘要是有损的，这是 2026-08-06 评审认定的
**质量收益最大的单项**。前置是 P0-1 —— 先让整理可靠地跑起来，再优化它的质量。

### 6. 当天事项注入 runtime_context

聊天时助手直接知道今天有什么安排，不用先调 timeline 工具。小改动（system prompt
组装处加一段），体验提升明显 —— 时间线遗留项里性价比最高的一个。

### 7. 时间线重复与冲突检测

通知上线后这条升级了：以前提取重复只是列表难看，现在是**手机响两次**。
判重先做最朴素的（同日 + 标题相似），有误杀再调。

---

## P3 — 等信号再做

| 事项 | 等什么信号 |
|---|---|
| `build_system_prompt` 别每轮 `list_all()`（`app/memory/prompt.py:100`） | Phoenix 显示 system prompt 体积/耗时随记忆量可感增长 |
| 摘要生成并行化 + 整理 prompt 体量保护 | 整理耗时开始可感 |
| `stats` 的 `unused` / `missed_reads` 喂回整理流程 | 埋点数据积累出可读的规律 |
| 记忆索引分层 | 索引随记忆数量线性增长到碍事 |
| pgvector 归档检索 | 关键词检索开始明显漏召回 |
| 写记忆时 prompt cache 失效的缓解 | 成本数据（Phoenix）证明值得做 |
| KB（Obsidian vault）写权限 | `kb_reads` 埋点数据。真要开，顺序是 `append` → 白名单 `create` → `str_replace`，一步一停 |
| Web Push 通道 | Bark 真的不够用（现在够用） |
| 通知点开落进预置上下文的新对话 | 通知功能日常用起来之后 |
| ICS 导入导出、时间线接全局搜索 | 使用信号 |

---

## 明确不做（评审过，别再提）

- **Redis**（2026-08-07 评审）：每个典型用途在这里要么不存在（缓存热点、限流、会话）、
  要么已被更合适的东西解决（幂等靠 `dedupe_key` 唯一约束、队列靠补跑式 DB 扫描、SSE 单进程
  直接 yield）。单进程是前提不是缺陷；真出现队列需求 Postgres 的
  `FOR UPDATE SKIP LOCKED` / `LISTEN/NOTIFY` 也先顶着。重新考虑的信号：多 worker/多实例部署
- **自动向外部日历写入**：时间线只进不出，出错的代价不对称
- **多用户、插件化、通用 RAG 框架**：单人助手，没有这些需求存在的证据

---

## 暴露面 checklist

单机 localhost 时都不是问题；**任何一步把服务暴露到局域网之前过一遍**：

- [ ] `api_key` 非空 —— `app/security.py` 里空值 = 完全不校验。**设置页现在会显示这条**（运行与环境 → 仅环境变量）。配 `notify_public_base_url`
      让手机能点开通知**就属于这一步**，这两个设定目前是独立的，没人把它们放在一起看
- [x] Phoenix 端口（默认 16006）只绑 localhost —— 里面是完整对话原文和记忆正文
- [ ] vault 保持挂载层只读（compose `:ro`），写保护不依赖 prompt 约束

## 环境与配置坑

- iCloud 上的 Obsidian vault 有 dataless 文件读不到的坑，`.env` 配 `VAULT_PATH` 时注意
- 容器时区必须 `TZ=Asia/Shanghai`（compose 已配）：UTC 会让「今天」的判断、注入给模型的
  日期、`local_day_bounds` 全部差 8 小时

---

## 已完成里程碑

倒序，只记一行；细节和防复发注记在 [fixes.md](fixes.md) 与各模块文档。

- **2026-08-07** 时间线第二版：到点推送到手机（Bark，补跑式 ticker + `dedupe_key` 幂等），
  顺带修掉逾期项消失、`recurrence=yearly` 不生效两个静默缺陷
- **2026-08-07** 可观测性方案定稿（[observability.md](observability.md)）
- **2026-08-0x** 会话管理收敛到侧栏；设置页按「我要改什么」重新分组
- **2026-08-0x** 标题生成走免费小模型 + 全线关思考，死等 2.86s → 0.05s（fixes.md）
- **2026-08-0x** 流式回归五层修复：gzip 缓冲、平滑滚动、结束闪烁等（fixes.md）
- **2026-08-06** 记忆架构评审：整体不用动，优化按性价比分档 —— 即上面 P0-1、P2-5 和 P3 各条
- 更早：时间线第一版（提取 + CRUD + 三视图）、每日回顾改叙事、界面全量 i18n、
  语音输入/朗读（本地 mlx-audio）、Obsidian vault 只读接入
