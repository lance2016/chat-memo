# 缺陷档案：已修复问题的原因与防复发注记

这里记**已经定位并修好**的缺陷：症状、根因、实测证据、改在哪。价值在防复发 ——
有些坑没法用测试锁住，换台机器重构很容易又踩回去。动手改相关代码前先扫一遍。

还没做的事在 [roadmap.md](roadmap.md)。

---

## 共享侧栏不能用 `useSearchParams()`

侧栏（`workspace-topbar.tsx`）每个页面都渲染，一旦在里面调 `useSearchParams()`，
`/review`、`/timeline`、`/_not-found` 的静态预渲染会全部 bail，`next build` 直接失败
（`useSearchParams() should be wrapped in a suspense boundary`）。

现在改读 `window.location.search`（`currentConversationId()`），会话切换靠
`selectedConversationChangedEvent` 广播。**往侧栏里加东西时别再引入这个 hook。**

---

## 生成标题优化

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

**改法**：标题单独走一条便宜的路，并且**两条路都关掉推理**。

- `app/llm/title.py`（新增）：`TitleClient` 走 OpenAI 兼容端点，发
  `extra_body` 显式关思考 —— 这类模型默认开着思考，不显式关掉标题照样白等。
  没配 key 时 `get_title_client()` 返回 `None`
- `complete()` 全线加 `thinking: bool = True` 开关（`app/llm/provider.py` 协议、
  DeepSeek 发 `extra_body={"thinking": {"type": "disabled"}}`、Anthropic 把写死的
  `adaptive` 改成按参数走）
- `app/chat/service.py` 的 `_complete_title`：配了专用 key 走小模型，
  否则退回聊天 provider 并传 `thinking=False`。`max_tokens` 收到
  `TITLE_MAX_TOKENS = 500`（不敢更小：万一模型忽略关闭指令，预算太紧会只剩思考、
  标题静默变空）
- `app/jobs/consolidate.py` **没动** —— 每日整理是质量最敏感、频率最低的活，
  `.env` 还专门留了 `CONSOLIDATE_MODEL` 给它，思考该留着。
  `tests/test_title_generation.py::test_consolidation_keeps_thinking` 守着这条线
- `TITLE_TIMEOUT` 作为兜底保留。标题降到约 0.7s 后它和正文（约 1s）并行，
  正常会**先于正文完成**，死等趋近于 0，兜底基本不触发

> 首发时走智谱 `glm-4.7-flash`；后来改为**优先硅基流动的免费 Qwen3-8B**
> （`SILICONFLOW_API_KEY`），智谱配置保留作兼容回退，见 `app/llm/title.py` 开头的注释。

`tests/test_title_generation.py` 覆盖两条路，两个「关推理」断言都做过 RED 检查
（去掉开关就红）。

**已验证**：配好 key 后实测，死等从 **2.86s 降到 0.05s**。
日志里能直接看到标题比正文早 3 秒就完成了，所以它已经完全不占用户的时间：

```
21:13:00  🏷 解决 Docker Compose 前后端连接问题 [zhipu/glm-4.7-flash · 1.3s]
21:13:03  ← conv#31 4.6s · 2 工具 · 4143 tok · 缓存 3456
```

**怎么确认标题走了哪条路**：`_complete_title` 每次都会打一行 `🏷`，带上标题、
路由（`siliconflow/<模型>`、`zhipu/<模型>` 或 `<provider> 不思考`）和耗时。

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

---

## 流式看起来不流式

一次回归里叠了好几层，**每一层单独都足以让界面看起来「转圈很久，然后整段蹦出来」**。
记在这里是因为其中两层没法用测试锁住，换台机器重构很容易又踩回去。

### 1. SSE 被同源代理的 gzip 缓冲（传输层，主因）

浏览器经 Next 的 `/backend` rewrite 访问 API，而 Next **在转发前无条件挂了 gzip 中间件**：
`router-server.js` 里只要 `compress !== false` 就创建它，并且远在 `proxyRequest` 之前
就把 `res` 包掉了。`text/event-stream` 命中它的 compressible 正则、阈值 1KB、且**从不 flush** ——
每个回答的前 1KB 都被扣着不发。

`X-Accel-Buffering: no` 只有 nginx 认。这个中间件认的是 `no-transform`。

修法：`app/chat/router.py` 的 SSE 响应头声明 `Cache-Control: no-cache, no-transform`。
已由 `tests/test_chat_regressions.py::test_sse_headers_opt_out_of_proxy_compression` 锁住。

> 别改成在 `next.config.ts` 里写 `compress: false` —— 那会把其余接口的压缩也一起关掉。
> `no-transform` 是 RFC 9111 的标准信号，对 Next、nginx、任何前置代理都有效。

### 2. `.message-scroll` 的平滑滚动打死了自动跟随（渲染层）

**这一层没有测试保护，最容易复发。**

流式跟随靠每个 delta 赋值 `scrollTop`。一旦给 `.message-scroll` 设了
`scroll-behavior: smooth`，赋值就变成动画，而**动画自己派发的 scroll 事件和用户上滚
无法区分** —— `onScroll` 里那个「用户上滚了就停止跟随」的判定于是自己把跟随关掉，
正文全长到视口外面去了。

`frontend/app/globals.css` 里 `.message-scroll` 的 `scroll-behavior` **必须保持默认的 auto**，
原地留了注释说明。需要平滑的那处跳转在 `chat-page.tsx` 里显式传 `behavior` 参数，不受影响。

### 3. 流结束时闪一下

`send` 的 `finally` 原先先清 `draft` 再 `loadMessages()`，而后者会挂
「加载消息中…」占位 —— 刚说完的回答先整段消失、列表被占位替换一瞬、再完整重现，
观感就是「正文是一次性出现的」。

现在 `loadMessages` 有 silent 模式，并且**在 `setTurns` 的同一批状态更新里**清掉
`pendingUser` / `draft`。分开清会先渲染出「权威历史 + 还没清掉的 draft」那一帧，也就是重影。

### 4. 首个 token 之前没有任何反馈

`displayTurns` 原先要求 `draft` 已有内容才给出助手气泡，所以从点发送到首字之间界面上
什么都没有，看着像卡死。现在只要 `sending` 就给出气泡，配合那个一直没人用的
`.streaming-cursor` 显示闪烁光标。顺带解决了偏好里关掉 thinking/tools 时渲染出空白卡片的问题。

### 5. 标题超时兜底（止血，非治因）

`app/chat/service.py` 的 `TITLE_TIMEOUT = 5.0` 把最坏等待挡住了，但这只是止血：
标题超时后会被丢掉，会话停在「新对话」，留到下一轮重试。
真正的治因是上面那条**生成标题优化** —— 标题降到约 0.7s 后这个兜底基本不再触发。
