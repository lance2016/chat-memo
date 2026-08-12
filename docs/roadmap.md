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

### 1. ~~每日整理可靠运转~~ ✅ 已实现

原来 `consolidate_auto` 默认关，原因写在注释里：进程一重启计时器就从头开始，
笔记本凌晨多半在睡眠，定时器很容易整天不触发 —— 于是「一个帮人记事的助手，
自己的记忆整理却依赖人记得去触发」。

- **补跑式 ticker**（`app/jobs/backfill.py`）：查「哪天该整理但没整理」，从旧到新补，
  一次 tick 只补一天（一次 agent loop 要几十秒到几分钟，串着补七天会把循环卡死）。
  往回最多补 7 天 —— 离线很久后醒来不该雪崩，和 notify 的补跑风暴闸门同一个道理
- **`consolidation_runs` 表**：一天一行，重跑是覆盖。备份那边用文件名当记录就够了，
  这里不行 —— 整理可能合法地「什么都没做」，不显式记一笔就会每十分钟重跑同一天
- **`consolidate_auto` 默认改成 True**：补跑式扛得住重启和睡眠，才有资格默认开着
- **`GET /api/jobs/consolidate/health`**：待补的日子 + 最近几次的结果。
  「静默不运转」比「报错」危险得多，这是那双眼睛

**已验证**：接口一上线就报出 8/06、8/07 两天从没整理过（真实的静默失败）。
补跑 8/06 → `consolidation_runs` 留下 `ok` 记录 → 待补列表里只剩 8/07。

⚠️ 实现时踩到的坑，已由测试钉住：**agent loop 出错时不抛异常**，它把消息作为
`Error` 事件塞进 `result.detail` 然后正常返回。只看异常的话这种情况会被记成 `ok`，
补跑逻辑再也不碰这一天 —— 那天的记忆就此永久缺失，而且完全静默。
同理「全部摘要失败」时 `skipped` 也是 True，但那不是「没内容可整理」而是「读不到输入」，
判定顺序不能反。

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

### 8. ~~附件基座~~ ✅ 已实现

`attachments` 表 + `ATTACHMENTS_PATH` 可写挂载（磁盘按 sha256 内容寻址）+
`POST/GET /api/attachments`。正文不进数据库的理由和代价（`app/backup.py` 必须
同步增量复制、`docs/backup.md` 的演练已更新）都记在 internals「图片」那节。

本次只做了 `kind=image`；表结构给 `file` 留了位，第 10 条不用再迁移一次。

⚠️ 已知取舍：**孤儿附件不清理**（上传成功但没发送的行会留下）。单人使用量极小，
真成问题时再加一个按 `message_id is null and created_at < ?` 的清理任务。

### 9. ~~图片识别：路由到视觉档案~~ ✅ 已实现

分支点就在 `ModelTarget.supports_vision` 上，判据不是厂商名 —— 所以把聊天模型换成
Claude 之后，原生视觉是**零改动**自动生效的。看不了图时走 `vision_model_profile_id`
配的视觉档案转描述，按 sha256 缓存复用。

`image_ask` 工具也做了，但**是补充而不是主路径**，且只在聊天模型看不了图时注册。
两种方案各自的死穴（工具方案下模型不知道该问什么；预描述是盲写的、问到没写的会编）
记在 internals「为什么预描述是主路径，而工具是补充」。

还没做、但已经知道值得做的：**每日整理看不见图**（`_render_transcript` 只取 text 块），
所以「那天贴了张报错截图」不会进当天摘要。

### 10. 文件上传与检索：用已经装好的 pgvector，不引入向量库

⚠️ **前提先纠正：这个项目不需要「找一个开源向量库」。** `CREATE EXTENSION vector`
从第一版迁移（`29f8662da2d5_initial_schema.py`）就跑了，至今零表使用；P3 里那条
「pgvector 归档检索 — 等信号」等的就是这个信号。再拉一个 Chroma / Qdrant / Milvus 进来，
和否决 Redis 是同一条理由：单人单机，多一个服务就多一个要备份、要恢复演练、
要在 compose 里对齐版本的东西，而它能力上不比 pgvector 多给什么。

- **Embedding 不本地跑**。走已有的 OpenAI 兼容链路（硅基流动的 BGE-M3 /
  Qwen3-Embedding，和标题模型同一个 key）。本地跑 embedding 是 CPU 密集任务 ——
  「明确不做：独立 worker 容器」那条里，它恰好被列为**重新考虑拆 worker 的信号之一**，
  现在没有理由主动去触发它
- **第一版只解析 md / txt / pdf**。这就是「一开始不需要支持那么多文件」的正确切法：
  纯文本零依赖，pdf 加一个 `pypdf`（纯 Python）。docx / xlsx 等真需要了再说 ——
  它们各自要一个解析库，而目前没有证据说明会用到。
  ⚠️ **txt / md 的「上传 + 整块进上下文」已经先做掉了**（`kind="file"`，
  见 internals「文本附件」那节），但那**不是**这一条：它没有 `documents` 表、
  没有切块、没有检索，长文件靠 `TEXT_INLINE_CHARS` 截断并明说。
  这一条要补的正是截断之外的那部分 —— 落库、切块、`doc_search` / `doc_read`。
  解析层要抽成按 mime 分发的 parser 协议，txt / md 那个 parser 直接复用
  `app/attachments/text.py`，pdf 只是多注册一个
- `documents` + `document_chunks` 两张表，chunk 上一列 `vector(1024)`
- **检索用混合而不是纯向量**：`pg_trgm` 子串（基建已在 `messages.search_text` 上跑了）
  + 向量余弦。这个仓库踩过「Postgres 中文全文检索要额外装分词」的坑，
  向量补的正是关键词漏召回，两者是互补不是替代
- 工具 `doc_search` / `doc_read`，和 `kb_*` 同构；system prompt 第 0 层只列文档名，
  照抄技能和记忆索引那套渐进式披露

⚠️ **动手前必须先定清楚一件事：上传的文件属于第几层？** 它不是记忆（模型写的），
不是 vault（用户手写的知识），是「用户丢给助手的资料」——第四个来源。
这条边界不写进 prompt，会出现「同一份 PDF 既在 vault 里又在上传里，模型该引用哪个」，
而这种冲突是静默的。

前置：8 ✅（已就绪，`attachments.kind` 留了 `file` 这个值，不用再迁移表）。

### 11. 代码执行：独立沙箱容器，且先想清楚为什么要

三条里**最重、价值最不确定的一条**，建议排最后。

真正说服人的理由只有一个：**它解锁刚做完的技能功能**。官方那 18 个技能里
pdf / docx / pptx / xlsx / canvas-design 全靠跑脚本，现在装得上但用不了
（见 internals「技能是文档，不是可执行程序」）。除此之外的用途 —— 算数、画图 ——
都有更便宜的办法。所以先回答「除了跑技能脚本，我还要它干什么」，再动手。

选型（都看过了，结论是**别直接用现成服务**）：

| 方案 | 为什么不选 |
|---|---|
| e2b（Firecracker microVM） | macOS 上 Docker Desktop 里再跑 microVM 是套娃；自托管明显偏云 |
| Judge0 | 要 Redis + Postgres + worker 三个服务，和「明确不做 Redis」直撞 |
| Jupyter kernel gateway | 有会话状态是优点，但沙箱本身还得自己做，等于问题没解决 |
| 挂 Docker socket 进 API 容器 | **等于把宿主机 root 交给模型输出**，一票否决 |

推荐：compose 里加一个 `sandbox` 服务，API 通过内网 HTTP 调它。
`network_mode: none` + `read_only: true` + 一个 tmpfs 工作目录 + 非 root 用户 +
`cap_drop: ALL` + `security_opt: no-new-privileges` + `pids_limit` / `mem_limit` / `cpus`。
想省事可以先直接跑 `piston` 的镜像（多语言、轻、无外部依赖），不合用再自己写那 200 行。

⚠️ **暴露面 checklist 要加一条**：代码执行 + 联网搜索 + 文件上传三者叠加之后，
「模型读到一段不可信文本 → 照着它执行代码 → 把结果发到外网」这条链就闭合了。
沙箱断网是这条链上唯一的硬约束，不能只靠 prompt。

前置：无（不依赖 8），但优先级在 9 和 10 之后。

---

## P3 — 等信号再做

| 事项 | 等什么信号 |
|---|---|
| `build_system_prompt` 别每轮 `list_all()`（`app/memory/prompt.py:100`） | Phoenix 显示 system prompt 体积/耗时随记忆量可感增长 |
| 摘要生成并行化 + 整理 prompt 体量保护 | 整理耗时开始可感 |
| `stats` 的 `unused` / `missed_reads` 喂回整理流程 | 埋点数据积累出可读的规律 |
| 记忆索引分层 | 索引随记忆数量线性增长到碍事 |
| pgvector 归档检索（翻两个月前的对话原文） | 关键词检索开始明显漏召回。⚠️ 和 P2-10 是**两件事**：那条是「用户上传的资料」，这条是「历史对话」。但它俩共用同一套 embedding 链路和 pgvector 基建，10 做完之后这条的成本只剩建表和灌数据 |
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
- **编辑消息后的多分支**（ChatGPT 那种 `< 1/2 >`，2026-08-08 评审）：方案本身可行
  （`messages.parent_id` + `conversations.head_message_id`，后端约 200 行加一个迁移，
  前端分支导航 UI 是大头），但它解决的是「想对比两个回答」的体验问题，而真正的伤害
  —— 编辑=不可逆删除、撤回的内容留在记忆里 —— **已经由软删除解掉了**
  （见 [internals.md](internals.md#编辑消息为什么不删行)）。
  单人使用下翻旧分支的需求没有证据支撑，不值得让「对话历史」从一条链变成一棵树：
  那会让七八个读取点每个都复杂一档。重新考虑的信号：软删除跑一阵子后，真的出现
  「想翻回被编辑掉的那一版」的实际需求。数据已经留在库里，届时回填 `parent_id` 即可，
  今天不做不付额外代价
- **独立 worker 容器跑定时任务**（2026-08-08 评估）：worker 的主要卖点「进程重启不丢任务」
  已经被补跑式调度消解了；后台任务全是 `await` HTTP，不占事件循环（实测 API 常驻 173MB、
  CPU 0.35%）；三个 ticker 各自都有 `except Exception` 兜底。**拆了反而引入一个真风险**：
  两个进程同时跑 ticker 会让同一天整理两次（`backfill.record` 是 SELECT-then-insert，
  挡不住并发），双倍 token + 模型对同一批摘要写两遍记忆。完整论证见
  [internals.md](internals.md#后台任务为什么还在-api-进程里)。
  耦合真正的代价在开发体验上，Compose 默认用 `RELOAD=1`；生产或需要稳定 ticker 时再显式设 `RELOAD=0`，临时调试后台任务才使用 `JOBS_ENABLED=0`。
  重新考虑的信号：API 要跑多副本（和否决 Redis 那条同一个信号）、Phoenix 上看得出整理
  占用可感的内存/CPU、或者出现真正 CPU 密集的后台任务（比如本地跑 embedding 建归档索引）
- **自动向外部日历写入**：时间线只进不出，出错的代价不对称
- **多用户、插件化、通用 RAG 框架**：单人助手，没有这些需求存在的证据

---

## 暴露面 checklist

单机 localhost 时都不是问题；**任何一步把服务暴露到局域网之前过一遍**：

- [ ] `api_key` 非空 —— `app/security.py` 里空值 = 完全不校验。**设置页现在会显示这条**（运行与环境 → 仅环境变量）。配 `notify_public_base_url`
      让手机能点开通知**就属于这一步**，这两个设定目前是独立的，没人把它们放在一起看
- [x] Phoenix 端口（默认 16006）只绑 localhost —— 里面是完整对话原文和记忆正文
- [ ] vault 保持挂载层只读（compose `:ro`），写保护不依赖 prompt 约束
- [ ] （代码执行上线后）沙箱容器 `network_mode: none`。**代码执行 + 联网搜索 + 文件上传
      三者一旦齐活，「读到不可信文本 → 照着执行 → 把结果发出去」这条链就闭合了**，
      而技能正文本身就是从 GitHub 下来的第三方内容。断网是这条链上唯一的硬约束

## 环境与配置坑

- iCloud 上的 Obsidian vault 有 dataless 文件读不到的坑，`.env` 配 `VAULT_PATH` 时注意
- 容器时区必须 `TZ=Asia/Shanghai`（compose 已配）：UTC 会让「今天」的判断、注入给模型的
  日期、`local_day_bounds` 全部差 8 小时

---

## 已完成里程碑

倒序，只记一行；细节和防复发注记在 [fixes.md](fixes.md) 与各模块文档。

- **2026-08-08** `JOBS_ENABLED` 把「后台任务能跑」和「后端能热重载」解耦，
  补了 `POST /api/notify/sweep`；同时评估并否决了独立 worker 容器（见「明确不做」）
- **2026-08-08** 编辑重发改软删除：原来是 `DELETE`，那一轮写出去的记忆和时间线事项
  撤不回来、也再查不到出处。现在行留着，读历史统一过 `live_message()`。
  同时评审否决了多分支（见「明确不做」）
- **2026-08-07** 时间线第二版：到点推送到手机（Bark，补跑式 ticker + `dedupe_key` 幂等），
  顺带修掉逾期项消失、`recurrence=yearly` 不生效两个静默缺陷
- **2026-08-07** 可观测性方案定稿（[observability.md](observability.md)）
- **2026-08-0x** 会话管理收敛到侧栏；设置页按「我要改什么」重新分组
- **2026-08-0x** 标题生成走免费小模型 + 全线关思考，死等 2.86s → 0.05s（fixes.md）
- **2026-08-0x** 流式回归五层修复：gzip 缓冲、平滑滚动、结束闪烁等（fixes.md）
- **2026-08-06** 记忆架构评审：整体不用动，优化按性价比分档 —— 即上面 P0-1、P2-5 和 P3 各条
- 更早：时间线第一版（提取 + CRUD + 三视图）、每日回顾改叙事、界面全量 i18n、
  语音输入/朗读（本地 mlx-audio）、Obsidian vault 只读接入
