# 内部机制与设计取舍

README 只讲「是什么、怎么跑」；这里记**为什么这么做**：记忆机制的细节、语音的性能优化、
调试手段、以及动手改代码前必读的踩坑清单。

- 还没做的事在 [roadmap.md](roadmap.md)
- 已修复缺陷的原因档案在 [fixes.md](fixes.md)
- 前端对接契约在 [frontend-api.md](frontend-api.md)

## 记忆机制详解

三层在界面上分别对应：L0 = 聊天页的消息流，L1 = 每日回顾页折叠区里的会话摘要，
**L2 = 记忆管理页 `/memories`**，也就是通常说的「长期记忆」。

每日回顾页真正给人看的不是这三层，而是整理时另外产出的两样东西：`daily_digests`
（一天一句话 + 几条收获，一天只有一行，重跑是覆盖）和 `open_loops`（没有明确日期、
但之后可能仍需关注的事，跨天存活，下一次整理时由模型判断是否已处理）。明确日期的
安排进入时间线，不在这里重复。这两样不进 prompt，只服务于回看。
L1 摘要因此是一次调用产出两份文本：`summary` 给记忆（只留事实），`recap` 给回顾
（今天推进了什么）——口径相反，但读的是同一段对话，没有分开调用的理由。

⚠️ **L2 进 prompt 的只是索引，不是全部正文**。每轮请求的 system prompt 里
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

**渐进式披露**：system prompt 里只放索引（命中 prompt cache），模型需要细节时用
`view` 读具体文件。记忆正文可以持续增长；常驻上下文只随一行一条的索引摘要缓慢增长。
索引被包在明确的“背景数据”边界内，不参与行为指令优先级；当前请求、用户持久偏好、
长期记忆和默认工作方式的冲突顺序由固定核心提示词统一约束。

⚠️ 上面那句「只随索引缓慢增长」是有前提的：**索引条目必须只写主题，不写结论**
（提示词里限 25 字，`tests/test_memory_prompt.py` 钉住）。一旦条目开始写「uv 不用 pip；
macOS + Ghostty」这类自足的答案，索引就变成了记忆的压缩版 —— 常驻开销跟着记忆内容涨
而不是跟着文件数涨，正文也永远等不到被 `view`，等于退化成全量注入还多背一层工具说明。
代价是短期内 `view` 变多、回答慢一点：记忆规模小的时候渐进式其实不赚（正文全部才几千
字符，还完全可缓存），它是为记忆长大之后准备的，而规矩越晚立越难改。

为什么不用 embedding 做主检索：个人记忆条目就几百到几千条，全量注入索引比向量召回准得多；
而且记忆需要去重和修正（「我换工作了」要覆盖旧记录），这是写操作，让模型直接改文件才对。
pgvector 已装好，留给将来的**归档检索**（翻两个月前某次对话原文），那才是向量真正合适的场景。

两个写入时机：

1. **聊天中实时写** —— 模型觉得值得记就直接调工具，`actor=chat`
2. **每日整理**（默认凌晨 4 点）—— 把当天摘要一起交给模型做去重、修正、提炼，`actor=consolidation`

第二个更重要，因为它有全局视角；实时写只看得到当前这轮对话。

每次记忆变更都在 `memory_versions` 留快照，可审计、可回滚，前端记忆页的版本历史就靠它。

## 知识库（Obsidian vault）

`.env` 里设了 `VAULT_PATH`（vault 在宿主机的绝对路径）后，compose 会把它**只读**挂到
容器的 `/vault`，模型多出四个工具：

| 工具 | 作用 |
|---|---|
| `kb_search` | 子串搜文件名和内容，`tag:#标签` 只搜标签 —— 知识库的首选入口 |
| `kb_read` | 带行号读一篇笔记 |
| `kb_list` | 浏览目录 |
| `kb_backlinks` | 查哪些笔记 `[[双链]]` 到给定笔记 |

设计上和记忆是两回事：**vault 是你写的知识，记忆是模型自己的工作记忆**。
vault 内容绝不进 system prompt（那里只有一段稳定的使用说明，不破坏 prompt cache），
模型要用就现场搜——不建索引，几千个文件的遍历只要几十毫秒，而且 vault 被 Obsidian
随时外部改动，没有索引就没有失效问题。

写保护做在挂载层（`:ro`），不依赖提示词自觉。每次 kb 调用都落 `kb_reads` 埋点
（搜了什么、有没有搜到），将来要不要开写权限靠这张表的数据说话。
`found=false` 的 search 是内容缺口信号——模型想查但你的笔记里没有。

几个注意点：留空 `VAULT_PATH` 功能整体关闭（工具不注册、提示词不提）；
iCloud 同步的 vault 里没下载到本地的文件容器读不到；`.obsidian/`、`.trash/`
等点目录和非文本附件对模型不可见；每日整理任务不带 kb 工具（它的输入是对话摘要）。

## 语音：别让用户干等

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

语音输入（ASR）和 TTS 共享同一个 mlx-audio 进程和一把串行锁：朗读进行中去转写会得到
409「先停止播放再说话」，而不是静默排队 —— 转写是同步交互，宁可立刻说清楚。

## 自定义指令为什么不是记忆

设置页的自定义指令原样追加到 system prompt 末尾，**它和记忆是两个正交的东西**：

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
curl -X PATCH localhost:18000/api/settings -H 'Content-Type: application/json' \
  -d '{"debug_prompts": true}'
```

开了之后：

```bash
docker compose logs -f api        # 日志里每次请求打一段轮廓
curl localhost:18000/api/debug/requests           # 最近 20 次，摘要
curl localhost:18000/api/debug/requests/1         # 某一次的完整 payload
curl localhost:18000/api/debug/prompt             # 当前 system prompt 原文，不用发消息
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

## 代码结构

```
app/
  config.py                 pydantic-settings（只剩「怎么调」，不含模型路由）
  security.py               X-API-Key 校验
  agent.py                  一次 agent 运行的统一装配：provider + 工具 + system prompt
  db/models.py              SQLAlchemy 模型
  llm/
    provider.py             LLMProvider / ToolExecutor 协议
    target.py               ModelTarget：「调哪个模型」的唯一载体
    catalog.py              模型服务 / 模型档案（数据库里的模型目录）
    anthropic_provider.py   Claude 的流式 agent loop
    deepseek_provider.py    OpenAI 兼容协议的 agent loop + 消息格式互转
    factory.py              按**协议**选实现
    composite.py            按工具名把多个 executor 拼成一个
    events.py               agent 事件定义
  memory/
    paths.py                路径校验（拒绝穿越）
    store.py                Postgres 虚拟文件系统，六个命令
    tool.py                 memory 工具派发
    prompt.py               system prompt 组装
    router.py               记忆管理 API
  kb/
    paths.py                vault 路径校验（含 realpath 防符号链接逃逸）
    search.py               对 vault 的无状态扫描（搜索 / 读 / 列目录 / 反链）
    tool.py                 四个 kb_* 只读工具 + kb_reads 埋点
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
  asr/
    client.py               本地转写服务的客户端（与 TTS 共享串行锁）
    router.py               录音上传 + 转写 / 状态探活
  debug/
    recorder.py             请求快照的环形缓冲 + 轮廓渲染
    log.py                  把轮廓打进日志
    router.py               看快照 / 看当前 system prompt
```

## 加新模型

分两种情况，成本差很远：

**OpenAI 兼容的服务**（硅基流动、OpenRouter、本地 vLLM…）—— **不用改代码**。
在模型页加一个「模型服务」（填 base_url 和凭据引用）再加模型即可。
`factory.py` 的注册表按**协议**分发，所有兼容服务共用同一个实现。

**新协议**（比如 Gemini 原生）—— 实现 `app/llm/provider.py` 的 `LLMProvider` 协议，
在 `factory._BY_PROTOCOL` 注册一行。不用动 `Settings`，不用动 chat / jobs / eval
任何调用方：它们拿到的都是 `ModelTarget`，只认协议名。

两条铁律：

- **「调哪个模型」只从 `ModelTarget` 取**（地址、密钥、模型 ID、max_tokens、思考默认）。
  `Settings` 里只剩换模型也不变的东西。往 Settings 加 `xxx_model` 字段就是在往回走。
- **`provider == "anthropic"` 只允许出现在 `ModelTarget.from_settings()` 里**，
  那是老配置的兼容入口，模型目录接管之后整个函数可以删掉。

DeepSeek 侧的两个注意点：思考内容不能回传（翻译时丢弃）；没有原生记忆工具，
schema 写在 `app/memory/tool.py` 的 `MEMORY_TOOL_PARAMETERS`，模型表现依赖这段描述质量。

## 加新工具

改一张表：`app/agent.py` 的 `TOOLKITS`。一条声明包含三件事 —— 怎么建 executor、
哪些用途（chat / consolidation）启用、额外的可用条件（比如知识库要挂了 vault）。

**三件事必须绑在一起**，这正是这张表存在的理由。它们原先散在三处手写实现里，
产生过两类静默故障：提示词讲了某个工具却没注册它（模型反复调用不存在的工具），
以及工具注册了但提示词没提（模型不知道自己有）。现在 system prompt 的分段由
「实际注册了哪些工具」推导，结构上不可能再对不上。

给人看的工具目录（`GET /api/tools`）也从同一张表推导，schema 直接问 executor 要 ——
executor 是工具定义的唯一事实来源，也正是聊天时交给模型的那份。这里原来是第二份
手写清单，漏改的后果是界面上少一个工具**而且不报错**。

所以加一个工具的完整成本是：写一个 `ToolExecutor`（带 `names` 属性），
在 `TOOLKITS` 加一条。聊天注册、整理排除、提示词分段、界面目录四处自动跟上，
`tests/test_agent_context.py` 和 `tests/test_api_endpoints.py` 会钉住它们不漂移。

DeepSeek 侧的两个注意点：思考内容不能回传（翻译时丢弃）；没有原生记忆工具，
schema 写在 `app/memory/tool.py` 的 `MEMORY_TOOL_PARAMETERS`，模型表现依赖这段描述质量。

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
  `host.docker.internal:8001`。Compose 使用独立的 `TTS_DOCKER_BASE_URL` 覆盖它
  （Linux 上还要 `extra_hosts` 映射到 `host-gateway`）；不用 Docker 直接跑后端时
  才使用 `TTS_BASE_URL=http://127.0.0.1:8001`。
- **TTS 合成必须串行**。MLX 后端一次只加载一份模型权重，并发请求只会互相拖慢并放大
  显存峰值。`app/tts/client.py` 里用一把进程内的锁排队。
- **当前时间不能进 system prompt**（破坏缓存），走 `build_runtime_context` 注入到 user 侧。
  不注入的话模型不知道今天几号，也会把自己的身份瞎猜成 Claude。
