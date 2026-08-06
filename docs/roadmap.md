# 规划与问题

这份文档是跨机器开发的交接清单：**未来规划**记还没做的事，**问题修复**记已经定位的缺陷
（含已修好的和原因，避免换台机器重构时又踩回去）。

结论都带证据和文件位置，动手前先按「怎么验证」那一栏复现一遍再改 —— 有些点可能已经
被另一台机器上的进度做掉了。

前端的体验与架构改造另有一份分阶段计划：**[roadmap-frontend.md](roadmap-frontend.md)**。

---

## 未来规划

来自 2026-08-06 的记忆架构评审。整体架构不用动，优化按性价比分三档。当时因为本地代码
不是最新而搁置，**逐条核对现状后确认下面这些仍未实现**（核对时间：2026-08-06）。

### 第一档：小改动补短板

| # | 事项 | 现状 |
|---|---|---|
| 1 | 整理任务后机械校验 `MEMORY.md` 索引与实际文件是否一致，差异喂回下次整理 prompt | 现在只靠 prompt 让模型自己更新索引（`app/jobs/consolidate.py:46`），没有任何机械校验 |
| 2 | 加 `consolidation_runs` 记录 + 启动时补跑错过的每日整理 | 表不存在。`.env` 的注释已经说明了痛点：进程重启会重置计时器，笔记本凌晨多半在睡眠，很容易整天不触发 |
| 3 | `build_system_prompt` 不要每轮 `list_all()` 全量加载，只查索引单行 | `app/memory/prompt.py:100` 仍是每轮 `await store.list_all()`。记忆越多，每轮的固定开销越大 |

### 第二档

- 整理任务加一个只读的「回查对话原文」工具。摘要是有损的，这是**质量收益最大的单项**
- 摘要生成并行化，同时给整理 prompt 加体量保护
- 把 `stats` 里的 `unused` / `missed_reads` 数据喂回整理流程，让整理知道哪些记忆没被用过

### 第三档：等信号再做

- 索引分层，控制它随记忆数量线性增长
- pgvector 归档检索
- 写记忆时打 prompt cache 的缓解

### 知识库写权限

Obsidian vault 目前是**只读**接入（`kb_search` / `kb_read` / `kb_list` / `kb_backlinks`），
写保护做在挂载层（compose 里 `:ro`），不依赖 prompt 约束。

要不要开写权限**靠埋点表 `kb_reads` 的数据决定**，不靠拍脑袋。真要开，顺序是
`append` → 白名单 `create` → `str_replace`，一步一停。

配置提醒：iCloud 上的 vault 有 dataless 文件读不到的坑，`.env` 里配 `VAULT_PATH` 时注意。

---

## 问题修复

### 已修复：生成标题优化

**症状**：第一轮对话正文已经说完，还要再等约 3 秒流才结束 —— 这段时间输入框是禁用的、
停止按钮还挂着，后端那把会话锁也还占着。

**根因**：标题生成开着扩展思考。`run()` 是显式关思考的
（`app/llm/deepseek_provider.py:93`，`want_thinking` 为假时发
`extra_body={"thinking": {"type": "disabled"}}`，注释也写明「不传就是默认开着」），
但标题走的是 `complete()`，**它从来不发这个字段**。Anthropic 那边更直接，
`app/llm/anthropic_provider.py:186` 写死了 `thinking={"type": "adaptive"}`。
两条 provider 路径都在为一个 16 字的标题做扩展思考。

而 `stream_reply` 在 yield `done` **之前** await 标题（`app/chat/service.py`），
所以流不关闭 → 前端的 `await streamChat(...)` 不返回 → `sending` 一直是 true。

**实测证据**

同一会话，第一轮 vs 第二轮（第二轮已有标题，不再调用）：

| | 最后一个 `text_delta` | 流关闭 | 正文说完后的死等 |
|---|---|---|---|
| 第 1 轮（生成标题） | 0.91s | 3.77s | **2.86s** |
| 第 2 轮（不生成） | 1.02s | 1.06s | 0.04s |

标题调用本身，容器内直连同一模型同一 prompt：

| 输入 | 思考开（现状） | 思考关 |
|---|---|---|
| `你好` ×3 | 3.27s / 2.36s / **8.25s**，输出 208 / 127 / 542 token | 0.69s / 0.67s / 0.67s，输出 2 / 1 / 1 token |
| `记住：我用 uv 管理 Python 依赖…` | **13.50s** → `记住用uv不用pip` | 0.60s → `使用uv管理Python依赖` |
| `帮我看看为什么 docker compose…502` | **21.53s** → `容器编排前端连后端502排查` | 0.86s → `Docker Compose前端502排查` |
| `我下周三要和张老师开会…` | 1.33s → `提醒准备论文实验数据` | 0.64s → `提醒准备周三论文会` |

为一个 5～10 字的标题烧掉 127～542 个思考 token、2.4～21.5 秒，**而且标题质量并没有更好** ——
关掉思考后 `Docker Compose前端502排查` 明显比 `容器编排前端连后端502排查` 干净。
这不是速度换质量的取舍，是纯亏。

**改法**：标题单独走一条便宜的路（智谱开放平台的 `glm-4.7-flash`，免费档），并且**两条路都关掉推理**。

- `app/llm/title.py`（新增）：`TitleClient` 走智谱的 OpenAI 兼容端点
  （`https://open.bigmodel.cn/api/paas/v4`），发 `extra_body={"thinking": {"type": "disabled"}}`
  —— GLM 默认开着思考，不显式关掉标题照样白等。`get_title_client()` 在没配
  `ZHIPU_API_KEY` / `TITLE_MODEL` 时返回 `None`
- `complete()` 全线加 `thinking: bool = True` 开关（`app/llm/provider.py` 协议、
  DeepSeek 发 `extra_body={"thinking": {"type": "disabled"}}`、Anthropic 把写死的
  `adaptive` 改成按参数走）
- `app/chat/service.py` 的 `_complete_title`：配了 key 走智谱，
  否则退回聊天 provider 并传 `thinking=False`。`max_tokens` 收到
  `TITLE_MAX_TOKENS = 500`（不敢更小：万一模型忽略关闭指令，预算太紧会只剩思考、
  标题静默变空）
- `app/jobs/consolidate.py` **没动** —— 每日整理是质量最敏感、频率最低的活，
  `.env` 还专门留了 `CONSOLIDATE_MODEL` 给它，思考该留着。
  `tests/test_title_generation.py::test_consolidation_keeps_thinking` 守着这条线
- `TITLE_TIMEOUT` 作为兜底保留。标题降到约 0.7s 后它和正文（约 1s）并行，
  正常会**先于正文完成**，死等趋近于 0，兜底基本不触发

配置：`ZHIPU_API_KEY` 和 `ZHIPU_BASE_URL` 是 `ENV_ONLY`（和其他 key 一致，
界面上不给入口）；`TITLE_MODEL` 可以在设置页改。

`tests/test_title_generation.py` 覆盖两条路，两个「关推理」断言都做过 RED 检查
（去掉开关就红）。

**已验证**（下面这组数据测于最初的 OpenRouter 小模型，换成智谱 Flash 后同属
一个量级）：配好 key 后实测，死等从 **2.86s 降到 0.05s**。
日志里能直接看到标题比正文早 3 秒就完成了，所以它已经完全不占用户的时间：

```
21:13:00  🏷 解决 Docker Compose 前后端连接问题 [zhipu/glm-4.7-flash · 1.3s]
21:13:03  ← conv#31 4.6s · 2 工具 · 4143 tok · 缓存 3456
```

**怎么确认标题走了哪条路**：`_complete_title` 每次都会打一行 `🏷`，带上标题、
路由（`zhipu/<模型>` 或 `<provider> 不思考`）和耗时。

```bash
docker compose logs -f api | grep 🏷
```

复现下面这段可以自己量一次：

```bash
set -a && . ./.env && set +a
CID=$(curl -s --noproxy '*' -X POST -H "X-API-Key: $API_KEY" \
  http://localhost:13000/backend/api/conversations | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
curl -sN --noproxy '*' -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d "{\"conversation_id\":$CID,\"content\":\"你好\"}" http://localhost:13000/backend/api/chat
```

> 免费档的模型有速率限制，偶尔会慢或失败。标题本来就是锦上添花 ——
> 失败会被 `_complete_title` 吞掉、超时有 `TITLE_TIMEOUT` 兜着，
> 两种情况都只是保留「新对话」并在下一轮重试，不影响这次回答。

```bash
set -a && . ./.env && set +a
CID=$(curl -s --noproxy '*' -X POST -H "X-API-Key: $API_KEY" \
  http://localhost:13000/backend/api/conversations | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
curl -sN --noproxy '*' -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d "{\"conversation_id\":$CID,\"content\":\"你好\"}" http://localhost:13000/backend/api/chat
```

---

### 已修复：流式看起来不流式

一次回归里叠了好几层，**每一层单独都足以让界面看起来「转圈很久，然后整段蹦出来」**。
记在这里是因为其中两层没法用测试锁住，换台机器重构很容易又踩回去。

#### 1. SSE 被同源代理的 gzip 缓冲（传输层，主因）

浏览器经 Next 的 `/backend` rewrite 访问 API，而 Next **在转发前无条件挂了 gzip 中间件**：
`router-server.js` 里只要 `compress !== false` 就创建它，并且远在 `proxyRequest` 之前
就把 `res` 包掉了。`text/event-stream` 命中它的 compressible 正则、阈值 1KB、且**从不 flush** ——
每个回答的前 1KB 都被扣着不发。

`X-Accel-Buffering: no` 只有 nginx 认。这个中间件认的是 `no-transform`。

修法：`app/chat/router.py` 的 SSE 响应头声明 `Cache-Control: no-cache, no-transform`。
已由 `tests/test_chat_regressions.py::test_sse_headers_opt_out_of_proxy_compression` 锁住。

> 别改成在 `next.config.ts` 里写 `compress: false` —— 那会把其余接口的压缩也一起关掉。
> `no-transform` 是 RFC 9111 的标准信号，对 Next、nginx、任何前置代理都有效。

#### 2. `.message-scroll` 的平滑滚动打死了自动跟随（渲染层）

**这一层没有测试保护，最容易复发。**

流式跟随靠每个 delta 赋值 `scrollTop`。一旦给 `.message-scroll` 设了
`scroll-behavior: smooth`，赋值就变成动画，而**动画自己派发的 scroll 事件和用户上滚
无法区分** —— `onScroll` 里那个「用户上滚了就停止跟随」的判定于是自己把跟随关掉，
正文全长到视口外面去了。

`frontend/app/globals.css` 里 `.message-scroll` 的 `scroll-behavior` **必须保持默认的 auto**，
原地留了注释说明。需要平滑的那处跳转在 `chat-page.tsx` 里显式传 `behavior` 参数，不受影响。

#### 3. 流结束时闪一下

`send` 的 `finally` 原先先清 `draft` 再 `loadMessages()`，而后者会挂
「加载消息中…」占位 —— 刚说完的回答先整段消失、列表被占位替换一瞬、再完整重现，
观感就是「正文是一次性出现的」。

现在 `loadMessages` 有 silent 模式，并且**在 `setTurns` 的同一批状态更新里**清掉
`pendingUser` / `draft`。分开清会先渲染出「权威历史 + 还没清掉的 draft」那一帧，也就是重影。

#### 4. 首个 token 之前没有任何反馈

`displayTurns` 原先要求 `draft` 已有内容才给出助手气泡，所以从点发送到首字之间界面上
什么都没有，看着像卡死。现在只要 `sending` 就给出气泡，配合那个一直没人用的
`.streaming-cursor` 显示闪烁光标。顺带解决了偏好里关掉 thinking/tools 时渲染出空白卡片的问题。

#### 5. 标题超时兜底（止血，非治因）

`app/chat/service.py` 的 `TITLE_TIMEOUT = 5.0` 把最坏等待挡住了，但这只是止血：
标题超时后会被丢掉，会话停在「新对话」，留到下一轮重试。
真正的治因是上面那条**已修复：生成标题优化** —— 标题降到约 0.7s 后这个兜底基本不再触发。
