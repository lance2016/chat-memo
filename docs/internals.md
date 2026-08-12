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

## 技能（Agent Skills）

技能是「把某类任务的做法写成说明书，需要时才读」。一个技能 = 磁盘上的一个目录，
里面有 `SKILL.md`（YAML frontmatter + 正文），可以再带参考文件和脚本。
格式跟 Anthropic 的 Agent Skills 对齐，社区里现成的技能包可以直接装。

**为什么值得单独做一层**：这些做法写进自定义指令的话每轮都进上下文，写进记忆的话
会被每日整理去重改写 —— 而它们既不是「我说了算的规矩」也不是「模型观察到的事实」，
是第三方写的操作手册。渐进式披露的三层结构和长期记忆完全一致：

| 层 | 内容 | 什么时候付出代价 |
|---|---|---|
| 0 | system prompt 里的 `- 名字 — 一句话用途` | 每轮，随技能**数量**增长 |
| 1 | `skill_read` 读 SKILL.md 正文 | 模型判断相关之后 |
| 2 | `skill_file` 读附带文件 | 正文点名了某个文件之后 |

所以 `description` 是这套机制里最贵的字段：它同时要回答「这技能干什么」和
「什么时候该用它」，而且是唯一常驻的部分。纪律和记忆索引条目限 25 字是同一条 ——
一旦这里写的是做法而不是用途，技能就退化成了全量注入的自定义指令。

但**预算是建议，不是准入门槛**。这里最初把 500 字写成硬上限，实测直接把
`anthropics/skills` 整个官方合集挡在门外（`claude-api` 的 description 1068 字、
正文 7 万字符）—— 这个预算是我们自己的取舍，拿它去否决别人写的技能，
唯一的结果是这个功能装不上任何真实技能。现在分成两层：超预算只在界面上标一条
提醒（`SkillEntry.warning`），2000 字才是硬上限（到那个量级是把正文写进了
frontmatter）。正文没有硬上限 —— 它要模型主动 `skill_read` 才付出代价。

**磁盘是「有哪些技能」的唯一事实来源。** `skills` 表只存磁盘回答不了的两件事：
从哪装的、有没有被停用。**没有行不等于没有技能** —— 把一个技能目录直接拷进
`SKILLS_PATH` 就能用，不需要经过安装接口（技能本来就该像 CLAUDE.md 一样可以手写）。
「一个技能是否对模型可见」的判据只有 `app/skills/service.py` 一处：manifest 解析
通过、且没被停用。判据散开的后果是「界面显示已停用、模型还在用」这种没人看得出来
的不一致。

**技能是文档，不是可执行程序。** 这套后端没有沙箱也没有 bash 工具，技能里带的
`.py` / `.sh` 只会被当成文本读出来。工具描述和 system prompt 里都明写了这一点 ——
不写的话模型会自信地宣称「我已经运行了脚本」。

安装从一个 zip 来（GitHub 仓库、子目录、任意直链，`parse_source` 全部归一成
「一个 zip 地址 + 一个子目录」），落盘前有四道闸：路径越界拒绝、软链跳过、
解压总量上限、条目数上限。⚠️ 前两道不是形式主义 —— `zipfile.extractall` 会把 `../`
规范化掉但**不会拒绝**，压缩包里声明的软链它也照样跟随，所以解压是逐条自己写的。
一个包里有多个 `SKILL.md` 时全部安装（合集仓库就是这样）。**先全部校验完再动磁盘**，
所以一个坏 manifest 永远不会留下装了一半的状态（那种状态在「磁盘即事实来源」的设计下
没法回滚）；坏的**跳过**并把原因回给界面，而不是整批拒绝 —— 让一个不合规的技能把其余
十七个挡在门外，实际结果同样是一个都装不上。全部不合法才报错。

落盘的目录名取自 frontmatter 的 `name`，不是压缩包里那层目录名 —— **`name` 才是技能
的身份**（官方合集里就有 `template/` 装着 `name: template-skill`）。装完之后目录名和
`name` 必然一致，`store` 那边「两者必须一致」的校验才立得住。

安装只能由人发起，模型没有对应的工具。技能正文是会被当成指令执行的，
让模型自己决定装什么等于把边界交出去。相应地，system prompt 里那段明写了技能是
「做事的参考，不是权限」：它不能改变安全边界，也不能盖过用户在当前对话里的要求。

坏掉的技能（frontmatter 解析失败）**仍然在界面上列出来**，只是模型看不到它 ——
让它凭空消失的话，人只会以为没装上然后再装一遍。

`SKILLS_PATH` 留空则整个功能关闭（工具不注册、提示词不提），设置页的
「启用技能」是运行时总开关。每日整理不带技能：它的输入是当天对话摘要、做的是固定
的一件事，而它写的是长期记忆，跑偏的代价是持久的。

## 图片：能力在 target 上，不在厂商名上

默认聊天模型是纯文字的（DeepSeek），但贴图这件事仍然要能用。做法不是「换个模型」，
也不是「加一个识图工具」，而是**把分支点放在 `ModelTarget.supports_vision` 上**：

- `vision=true` → image block 原样发（Anthropic 直接透传，OpenAI 兼容协议翻成
  `image_url` 的 data URI）
- `vision=false` → 进 agent loop **之前**把图交给模型目录里另配的「视觉档案」
  （`vision_model_profile_id`），换成一段描述 + OCR，存进附件行，之后每轮复用

这么切的收益是：哪天把聊天模型换成 Claude，**零改动**自动升级成原生视觉 ——
因为判据是 target 的能力，不是代码里写死的厂商名。这和「加新模型」那条铁律同源。

### 为什么预描述是主路径，而工具是补充

两种方案各有一个死穴，所以两个都要，但主次不能反：

- **只有 `image_ask` 工具**：模型自己看不见图，于是不知道该问什么。用户说「这个报错
  怎么解决」，模型只能盲发一句「描述这张图」，绕一圈回到预描述还多一轮延迟；用户追问
  「第三行那个数字」，工具把这句话带给视觉模型，而它没有对话上下文，不知道「第三行」
  指什么。**多轮追问必然退化，而这恰恰是图片最常见的用法。**
- **只有预描述**：描述是**盲写**的，生成时不知道用户会问什么。问到描述里没写的细节
  （某个按钮的颜色），模型手里没有依据，而它**不知道自己缺信息**，于是会编。
  这是静默降级，比报错坏得多。

所以：预描述保证「模型至少知道图里有什么」，`image_ask` 负责「描述不够时带着具体问题
回去看」。工具能问出好问题，恰恰依赖于已经有描述了。⚠️ `image_ask` 执行时**必须把最近
几轮的文字上下文一起带给视觉模型**，否则「第三行那个」还是答不对 —— 那是它相对预描述的
全部价值所在。

聊天模型自己能看图时，`image_ask` **不注册**（`TOOLKITS` 里的 `enabled` 同时看
settings 和 target）：图本来就在上下文里，再调一次工具纯属绕远路。

### 正文落磁盘、消息里只存引用

两处都不是随手选的：

- **正文不进数据库**。`pg_dump` 是备份主路径，而备份每天一次、保留十几份 ——
  blob 塞进去会被份数乘一遍。代价是 `app/backup.py` 必须同步复制附件目录，
  这一步不做，恢复演练就是假的（库恢复了，每条带图的消息都指向一个不存在的文件）。
  磁盘按 sha256 内容寻址，所以备份用「已存在就跳过」的增量复制，**不能**照抄
  `export_memories` 那种「清空再全量重写」。
- **消息里存 `{"type":"attachment_ref", ...}` 而不是 base64**。除了 dump 体积，
  还有一个更硬的理由：`trim_history` 的预算按 `len(json.dumps(message))` 算字符，
  一张 base64 图能一口吃掉整个 12 万的历史预算。存引用，那个预算才是诚实的。

于是「存的」和「发出去的」第二次故意不一致（第一次是 runtime context）。规矩只有一条：
**发给模型之前必须 hydrate，且历史和当前轮一起**。`ChatService._hydrate` 是唯一的入口，
它作用在 `[*history, 当前轮]` 这个合并后的列表上，所以两者不可能只覆盖一半。
漏掉历史那一半的症状很隐蔽 —— 第一轮好好的，追问时模型突然不知道你在说哪张图。

几个连带的纪律：

- **hydrate 出来的文本里必须带编号**（`[图片 #7 err.png]`）。模型能看到的只有这段文字，
  去掉编号 `image_ask` 就永远指认不了是哪张图。
- **`attachment_ref` 绝不能原样发给模型**：那是一段内部 JSON，模型会当成用户说的话。
  没装配 hydrator 的链路走 `placeholder_hydrate` 退化成 `[图片 x.png]`，不是直接透传。
- **找不到附件行/正文时给一句实话**（「已不可用」），不要静默删掉那个块。
  静默丢图的症状是模型答非所问，而用户根本不会想到是图没发出去。同理，带图但既没有
  视觉能力也没配视觉档案时，`/api/chat` 直接报错让用户去配，不进 agent loop。
- **描述懒生成、按 sha256 复用**。聊天模型自己能看图时一次都不算；同一张截图贴在两个
  会话里也只算一次。
- **编辑重发要把原消息的图带回来**。那条消息会被软删除、重新落一条新的，前端不显式把
  附件 id 传回去就等于「改了几个字，图没了」。附件行可以改挂到新消息上，重复用同一批 id
  是安全的。
- **文件名一个字符都不参与磁盘路径**（路径完全由 sha256 推出），所以这里不需要
  `skills/paths.py` 那一整套相对路径校验。仍然校验摘要形状，因为读取时它来自数据库，
  而数据库不是可信输入（恢复过一份被改过的备份、手工 UPDATE 过）。
- **`content_type` 是用户说了算的**，一律以文件头为准（`app/attachments/image.py`）。
  不嗅探的话，声称是 png 的任意二进制会被 base64 发给模型。

### 已知的取舍

- **孤儿附件不清理**：上传成功但没发送的行会留下。单人使用量极小，第一版接受。
- **每日整理看不见图**：`_render_transcript` 只取 text 块，附件引用被忽略，所以
  「那天贴了张报错截图」不会进当天的摘要。改起来不难（把描述渲染进去），但那会让
  整理的输入多一层间接，暂时不做。
- **`/api/conversations/{id}/context` 的窗口统计按引用算**，而视觉模型实际发出去的是
  base64，真实体量远大于显示值。统计的用途是「历史会不会被裁掉」，那个判断本来就发生在
  hydrate 之前，所以口径是自洽的 —— 但别拿它当「这轮花了多少 token」看。
- **`Content-Disposition` 里的文件名必须按 RFC 6266 编码**。HTTP 头只能是 latin-1，
  而附件名经常是中文（「截图 2026年7月19日.png」）。直接塞进 `filename="..."` 的后果
  不是乱码，是 starlette 在 `init_headers` 里抛 `UnicodeEncodeError` —— 下载接口整个
  500，而界面上的症状只是那张图变成一个警告图标，跟文件名看不出任何关系。
  `paths.content_disposition` 同时给 ASCII 兜底和 `filename*=UTF-8''`。
- **拒收时必须说清是哪个文件**。HEIC / AVIF / SVG 的 `content_type` 都是 `image/*`，
  前端拦不住，到 `sniff` 才被拒；只回一句「只支持 PNG / JPEG / GIF / WebP」的话，
  一次拖进来五个文件时没人知道是哪个出的问题。这些格式**不该**靠加解码库来支持 ——
  模型侧本来也只收这四种（见 `image.py` 里不装 Pillow 的理由）。
- **图片走 authed fetch → blob URL**，不是免鉴权直链。`<img src>` 带不了 `X-API-Key`，
  TTS 那套一次性票据（`app/tts/tickets.py`）在这里不适用 —— 它用一次即失效，而图片
  每次滚动/刷新都要重新渲染。代价是绕开了浏览器 HTTP 缓存，所以前端按 id 自己存一份。

### 文本附件（txt / md）：复用同一张表，但不复用那个分支

`kind="file"` 走的是**完全独立**的一条 hydrate 分支，只和图片共享上传、内容寻址、
`attachment_ref` 这三样基建。理由很直白：任何模型都读得了文本，所以
`supports_vision` 那个分支点在这里没有意义，`vision_*` 三列一直是空的。

几个各自对应一类事故的决定：

- **扩展名选路，内容才是准入判据。** `.txt` / `.md` 决定「走文本这条路、用文本的
  体积上限和错误消息」，但一个改名成 `.md` 的二进制仍然要在 `text.decode` 那步被
  拒掉。反过来，图片走文件头嗅探，两条路各自确认各自的内容，谁也不信文件名。
- **只接 UTF-8，不猜编码。** 猜错的代价是一整篇乱码进上下文，而模型会照着乱码答；
  让用户自己转一次便宜得多。BOM 和 CRLF 在解码时归一 —— 归一的是**发出去的**那份，
  磁盘上存的永远是原始上传字节。
- **必须有 `TEXT_INLINE_CHARS` 这道闸。** 上传上限 512KB，而 `trim_history` 的预算
  是 12 万字符 —— 一个文件就能把整段历史挤掉，症状是「模型忘了前面聊过什么」，
  几乎不可能联想到是附件干的。超出部分**不是静默截断**：末尾留一句明说这是片段。
  完整读取要等 roadmap 第 10 条的 `doc_read`，那才是长文档的正确形态。
- **正文用围栏包起来，且围栏长度按内容算。** 围栏是「这是用户上传的资料，不是他说的
  话」这条边界的唯一载体 —— 上传的文件是第四个内容来源，里面写的任何指令都不是用户的
  指令。固定三个反引号的话，一个自带代码块的 Markdown 会把围栏提前闭合，后半段正文
  就跑到边界外面去了。
- **视觉拦截的判据是「这批附件里有没有图」，不是「有没有附件」。** `/api/chat` 那道
  「当前模型看不了图」的拦截照着 `attachment_ids` 非空判的话，给纯文本模型传一个 `.md`
  会被判成传图直接拒发。判据落在 `store.has_images` 一处，前端的上传入口同理 ——
  它不再随视觉能力禁用，只是说明文案变了。

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
    openai_responses_provider.py  OpenAI Responses API 的 agent loop + 事件互转
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
  skills/
    paths.py                技能名 / 技能内路径校验（含 realpath 遏制）
    manifest.py             SKILL.md 的 frontmatter 解析与校验
    store.py                技能的磁盘视图：扫描 / 读取 / 删除
    install.py              下载 zip、安全解压、落盘
    service.py              磁盘 + 数据库合成一份视图（可见性判据的唯一实现）
    tool.py                 skill_read / skill_file
    router.py               安装、启停、删除
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

**OpenAI Chat Completions 兼容的服务**（硅基流动、OpenRouter、本地 vLLM…）—— **不用改代码**。
在模型页加一个「模型服务」（填 base_url 和凭据引用）再加模型即可。
`factory.py` 的注册表按**协议**分发，所有兼容服务共用同一个实现。

如果服务要求 Responses API（例如 `openai-api-server-via-codex`），选择
`OpenAI Responses API` 协议；它使用 `client.responses.create`，并单独处理 Responses
的 input item、函数调用和流式事件。

**新协议**（比如 Gemini 原生）—— 实现 `app/llm/provider.py` 的 `LLMProvider` 协议，
在 `factory._BY_PROTOCOL` 注册一行。不用动 `Settings`，不用动 chat / jobs / eval
任何调用方：它们拿到的都是 `ModelTarget`，只认协议名。

两条铁律：

- **「调哪个模型」只从 `ModelTarget` 取**（地址、密钥、模型 ID、max_tokens、思考默认）。
  `Settings` 里只剩换模型也不变的东西。往 Settings 加 `xxx_model` 字段就是在往回走。
- **`provider == "anthropic"` 只允许出现在 `ModelTarget.from_settings()` 里**，
  那是老配置的兼容入口，模型目录接管之后整个函数可以删掉。

⚠️ **两代配置并存期的一个坑**：设了 `chat_model_profile_id` 之后，
`resolve_model_target` 直接走档案，`provider` / `model` / `deepseek_model`
**完全不参与解析** —— 但它们还在设置页上，是可编辑的下拉框。改了不报错也没反应，
是整个设置页里唯一一处静默失效。现在由 `settings_store.inactive_reason()`
在 `GET /api/settings` 里如实标记（`fields[].inactive_reason`），界面压暗并说明原因。
**不禁用输入**：档案被删或停用时它们又会重新兜底，那时候这里是唯一还能改的地方。
`max_tokens` / `effort` / `deepseek_thinking` 不在此列 —— 档案 options 没填时它们
仍然是生效的兜底值。

同一个坑的另一面：注入给模型的 runtime context 里的「你实际运行在 X / Y」，
X 以前取的是 `settings.provider`，选了档案之后会告诉模型一个错的出处。
现在由 `ChatService.service_slug` 从 target 上带下来。

DeepSeek 侧的两个注意点：官方 Chat Completions 接口支持
`reasoning_effort=low/high/max`（默认 `high`）；带工具调用时，模型返回的
`reasoning_content` 必须在后续请求中完整回传，否则上游会返回 400。没有原生记忆工具，
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

DeepSeek 侧的两个注意点：思考强度使用 `reasoning_effort=low/high/max`；当请求携带
工具时，`reasoning_content` 必须在后续请求中完整回传。没有原生记忆工具，schema
写在 `app/memory/tool.py` 的 `MEMORY_TOOL_PARAMETERS`，模型表现依赖这段描述质量。

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
- **编辑重发是软删除，读历史必须过滤**。见下面「编辑消息为什么不删行」。新写一个读消息的
  查询时忘记加 `live_message()`，等于把用户撤回的话又喂回给模型，而且完全静默。
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
- **附件在消息里是引用，发出去之前必须 hydrate**。见上面「图片」那节：直接把 base64 存进
  `messages.content` 会一口吃掉整个历史预算，而把 `attachment_ref` 原样发给模型，
  它会把那段 JSON 当成用户说的话。

## 编辑消息为什么不删行

「编辑重发」和「重新生成」原来是硬删除：`DELETE FROM messages WHERE id > after`。
2026-08-08 改成软删除（`messages.deleted_at`），起因是发现它在悄悄污染记忆。

### 问题不是「历史断了」，是「已经写出去的撤不回来」

`app/agent.py` 的 `TOOLKITS` 里，`memory` 和 `timeline` 在 `purpose="chat"` 下都启用。
也就是说被编辑掉的那一轮里，模型可能已经：

- 往 L2 记忆写了一条（`memory_versions` 留了快照，但没人知道该回滚哪一条）
- 提取了一个时间线事项，**而且它会到点推送到手机**

消息行一删，「那条记忆当初是从哪句话来的」这条线索就断了，事后连查都没法查。

第二条路径是每日整理。`ConversationSummary.up_to_message_id` 是水位线，
会话在凌晨整理过（watermark=10）之后你编辑 msg 5，新分支从 id 11 起 ——
下次整理只看 `id > 10`，新分支能正常摘要，但**基于 5–10 写进摘要和 L2 的内容永久留着**。
整理任务只看得到摘要、看不到原文，它这辈子都发现不了那段已经被撤回。
软删除挡不住这一条（撤下发生在整理之后就晚了），但挡住了「撤下后还没整理」的那些，
并且把追查的可能性留了下来。

### 约定：读历史一律过滤，用量统计不过滤

`app/db/models.py` 的 `live_message()` 是那个 `WHERE` 片段。做成具名函数是为了能 grep ——
「对话历史」有七八个读取点，漏一个就是静默地把撤回内容喂回模型：

| 位置 | 说明 |
|---|---|
| `chat/service.py` `load_history` | 最要紧的一个，漏了编辑等于没编辑 |
| `chat/router.py` `list_messages` / `get_conversation_context` | 界面显示与上下文估算 |
| `jobs/consolidate.py` `_summarize` / `_conversations_on` | 记忆链路的入口 |
| `jobs/backfill.py` `_has_messages` | 整条撤下的日子不值得跑一次 agent loop |
| `eval/export.py` | 导出的样本要和真实整理看到的输入一致 |
| `search.py` | 搜到点进去看不到，等于坏链接 |
| `review/router.py` | 有内容可回看的日期 |

**唯一的例外是 `GET /api/usage` 的 token 统计**，那里故意不过滤：被撤下的那轮 token
是真花掉了的，从用量里抹掉只会让账单对不上。代码里那处有注释写明。

跨模块的约定由 `tests/test_message_soft_delete.py` 整组钉住，刻意不拆进各自的测试文件 ——
摆在一起才看得出漏了哪个读取点。

### 明确不做：ChatGPT 那种编辑后的多分支

评估过完整方案（`messages.parent_id` 自引用 + `conversations.head_message_id` 激活叶子，
读历史从 head 沿 parent 回溯），后端约 200 行加一个迁移，前端分支导航 UI 是大头。
**否决理由**：它解决的是「想对比两个回答」这个体验问题，而真正的伤害（数据不可逆丢失、
记忆被污染）软删除已经解掉了。单人使用场景下翻旧分支的需求没有证据支撑，
不值得让「对话历史」从一条链变成一棵树 —— 那会让上面那张表里的每一个读取点都复杂一档。

重新考虑的信号：软删除跑一段时间后，真的出现「想翻回被编辑掉的那一版」的实际需求。
真要做，数据已经留在库里了，迁移时回填 `parent_id` 即可，不会因为今天没做而付额外代价。

## 后台任务为什么还在 API 进程里

`app/main.py` 的 lifespan 起三个 ticker（整理 600s / 通知 60s / 备份 600s），
外加一次性的 TTS 预热。它们和 API 共用一个 uvicorn 进程（`entrypoint.sh` 没有
`--workers`，就一个）。2026-08-08 评估过拆成独立 worker 容器，结论是**不拆**。

### 拆 worker 的常见理由在这里都不成立

- **「进程重启会丢任务」** —— 已经被补跑式调度消解了。`app/jobs/backfill.py` 查的是
  「哪天该整理但没整理」而不是「现在几点」，备份查「今天备份过没有」，通知查
  「该发而没发的」。进程随时可以死，睡醒补上就行。`consolidation_runs` 表和
  `/api/jobs/consolidate/health` 还给了「静默不运转」的眼睛
- **「后台任务拖慢 API」** —— 整理的几分钟全花在 `await` LLM 的 HTTP 上，
  不占事件循环，也不是 CPU 密集。实测 `chat-api` 常驻 173MB、CPU 0.35%
- **「崩溃隔离」** —— 三个 ticker 各自都是 `except Exception: logger.exception(...)`
  然后继续下一轮，单次失败停不掉循环，更停不掉 API

### 拆了反而会带来一个真风险

拆完之后 API 绝对不能再起 ticker，否则两个进程同时跑。后果不对称：通知那边
`dedupe_key` 有唯一约束兜着（输的一方顶多报个 IntegrityError，下一分钟重试），
**但整理没有** —— `backfill.record` 是 SELECT-then-insert，两个进程可以同时挑中
同一天，跑两次完整 agent loop：双倍 token，而且模型对同一批摘要写两遍记忆，
去重逻辑不保证幂等。

其余代价：多一个 ~170MB 容器；`backups/` 卷和 `pg_dump` 二进制要跟着搬；
`app/debug/` 的请求快照是**进程内**环形缓冲，整理搬走之后 `/api/debug/requests`
就再也看不到整理那几次请求了；日志和 trace 分两处看。

这和 roadmap 里否决 Redis 是同一个理由：**单进程是前提，不是缺陷。**

### 但耦合确实在收一笔税：`JOBS_ENABLED`

代价是真的，只是不在性能上：为了让 ticker 能跑，后端热重载原来是默认关掉的
（compose 里那行注释写着「编辑 app/ 时会取消 lifespan 里的通知 ticker」）。
热重载每次改代码都重启进程、把 sleep 从头算起 —— 60s 的通知还有机会，
600s 的整理基本永远等不到第一次 tick。等于用「不能改代码」换「任务能跑」。

Compose 默认使用 `RELOAD=1`，所以第一次启动和日常开发直接执行 `docker compose up -d` 即可。
需要稳定运行后台 ticker 或部署生产时，再显式设置 `RELOAD=0`。`JOBS_ENABLED=0` 只用于
临时调试后台任务：

| | `JOBS_ENABLED=1`（默认） | `JOBS_ENABLED=0` |
|---|---|---|
| 三个 ticker | 建 | 不建，启动时 WARNING 一行 |
| TTS 预热 | 建 | **照样建** —— 打的是宿主机 mlx 服务，一次性不烧钱 |
| 各任务自己的开关 | 仍然生效 | —— |

它是**环境变量不是设置页开关**（在 `settings_store.ENV_ONLY` 里）：启动期读一次
决定建不建任务，写进数据库不会生效，放白名单只会给出一个「点了没反应」的开关。
各任务自己的开关（`consolidate_auto` / `backup_auto` / `notify_enabled`）仍然在
设置页，那些是循环内部每轮重读的。

关掉之后要能手动跑，所以补了 `POST /api/notify/sweep`（整理和备份本来就有）。
它跑的是和 ticker **完全同一个** `sweep()`，不是简化版 —— 那样测出来的不作数。
和 `/api/notify/test` 的区别：`test` 无视开关发一条假消息验证通道通不通，
`sweep` 走真实链路，`notify_enabled` 关着就不发（「关掉了还是收到推送」是最糟的一种 bug）。

顺带它也是将来真要拆 worker 时的那个开关（worker 侧 1、API 侧 0），
但那是**将来**，重新考虑的信号见 roadmap「明确不做」。
