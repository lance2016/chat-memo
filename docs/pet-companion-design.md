# 宠物伴侣设计：让「朝花夕拾」有一个会生活的界面

> 状态：Proposal  
> 日期：2026-08-10  
> 范围：宠物承载位置、动作系统、触发规则、道具、自定义规则、自定义宠物包、生图素材与实施边界。

## 0. 结论

这个功能值得做，但宠物不应该只是一个循环播放 GIF 的装饰。它最适合成为「朝花夕拾」的轻量状态化身：聊天在思考时它忙起来，记忆写入时它把东西收进册子，时间线到点时它拿出对应道具，夜深时它安静地提醒休息。

推荐把功能命名为 **「拾伴」**，宠物居住的固定区域叫 **「记忆窗台」**。

第一版不需要为每一种情境单独画动画。采用以下组合可以用有限素材产生足够多的变化：

```text
宠物表现 = 核心动作 × 道具 × 表情/台词 × 快捷操作 × 时间窗台
```

- 所有宠物必须支持 9 个核心动作，保证任何自定义宠物都能正常工作。
- 内置宠物可以再支持 14 个扩展动作，获得真正的睡觉、吃饭、喝水、伸懒腰等细节。
- 道具独立于宠物图集，提醒种类增加时不必重画所有宠物。
- 自定义规则使用「当……如果……就……最多……」的可视化结构，而不是要求用户写代码。
- 宠物永远不能成为关键功能的唯一入口；提醒、错误和时间线仍要有标准 UI。

## 1. 产品角色与设计原则

### 1.1 它在产品里负责什么

拾伴只负责四件事：

1. **让系统状态可感知**：等待、思考、读取记忆、写入时间线、成功、失败都有不同反馈。
2. **把时间提醒变得更有人情味**：午饭、喝水、休息、睡觉和用户自定义日程由宠物表达。
3. **提供低成本快捷操作**：完成、稍后提醒、打开时间线、停止生成等动作可从气泡直接完成。
4. **承载个性化**：宠物形象、名字、性格、台词、出现频率、安静时段和自定义规则都可以调整。

### 1.2 明确不做什么

- 不用「宠物饿了、病了、离家出走」惩罚用户没有打开应用。
- 不用连续签到和掉好感制造负担；亲密度只解锁内容，不会倒退。
- 不把每个 token、每次鼠标移动都做成动画。
- 不让宠物遮住输入框、消息操作、底部导航或表单按钮。
- 不在错误发生前庆祝，也不把模型仍在处理误报为完成。
- 不默认播放声音或 TTS；声音必须由用户主动开启。

## 2. 界面承载：记忆窗台

现有应用已经有持久化 `WorkspaceFrame`，最稳妥的位置是左侧栏底部、用户资料上方。宠物不会因路由切换重新挂载，能自然表现跨页面状态。

### 2.1 桌面端

```text
┌──────── 左侧栏 264px ────────┐┌──────────── 内容区 ────────────┐
│ 朝花夕拾                      ││                                  │
│ 新对话 / 主导航               ││                                  │
│ 最近对话                      ││                                  │
│                               ││                                  │
│ ┌────── 记忆窗台 224×112 ───┐ ││                                  │
│ │  天光/夜色     宠物 + 道具 │ ││                                  │
│ │  「该吃午饭啦」      ···   │ ││                                  │
│ └───────────────────────────┘ ││                                  │
│ 用户资料                      ││                                  │
└───────────────────────────────┘└──────────────────────────────────┘
```

- 宠物只在窗台内部左右移动，不能走进正文区域。
- 默认高度 112px；最近会话很长时窗台固定在底部，不挤压资料区。
- 侧栏折叠到 82px 时，窗台收成 56×64 的小窗，只显示宠物和状态点。
- 点击宠物打开右侧浮层，包含「现在」「今天」「装扮」「规则」四个入口。
- 气泡最多两行，8 秒后收起；重要提醒保留一个小圆点，直到处理或打开。

### 2.2 移动端

- 宠物缩成顶部栏中的 40×40 小窗，不采用悬浮球，避免遮挡聊天和底部导航。
- 点击后从底部打开半屏抽屉；抽屉内可完成「稍后提醒」「完成」「打开时间线」等操作。
- 键盘弹起时只保留静态头像，不显示气泡。

### 2.3 视觉方向

视觉沿用现有浅靛蓝、薄荷绿、暖橙和宋体标题，不再引入一套卡通 UI。唯一的视觉冒险是 **窗台天光会随本地时间变化**：清晨偏雾蓝，中午近白，黄昏带暖橙，夜间是低饱和深蓝。它让时间自然流进界面，同时只有这一处具有明显氛围变化。

建议颜色：

| 角色 | 颜色 | 用途 |
| --- | --- | --- |
| 记忆靛蓝 | `#5871D1` | 选中、互动、记忆类动作 |
| 薄荷绿 | `#4D9C83` | 完成、健康习惯、安静状态 |
| 日落橙 | `#D88A52` | 午饭、黄昏、一般提醒 |
| 提醒琥珀 | `#B8783C` | 临近截止、需要注意 |
| 夜空蓝 | `#263452` | 晚间窗台、勿扰状态 |
| 柔雾白 | `#F7F8FC` | 白天窗台和气泡底色 |

## 3. 动作协议

### 3.1 所有宠物必须具备的 9 个核心动作

核心图集采用 8 列 × 9 行、单格 192×208px 的固定协议，最终尺寸为 1536×1872px。它与宠物形象解耦，也方便自动校验和导入用户自定义包。

| 动作 | 帧数 | 建议时长 | 语义 |
| --- | ---: | --- | --- |
| `idle` | 6 | 280/110/110/140/140/320ms | 呼吸、眨眼；所有空闲状态的基础 |
| `running-right` | 8 | 前 7 帧 120ms，末帧 220ms | 在窗台向右移动 |
| `running-left` | 8 | 前 7 帧 120ms，末帧 220ms | 在窗台向左移动 |
| `waving` | 4 | 前 3 帧 140ms，末帧 280ms | 问候、轻提醒、回应点击 |
| `jumping` | 5 | 前 4 帧 140ms，末帧 280ms | 完成、开心、里程碑 |
| `failed` | 8 | 前 7 帧 140ms，末帧 240ms | 请求失败、断网、规则冲突 |
| `waiting` | 6 | 前 5 帧 150ms，末帧 260ms | 等待首 token、等待任务完成 |
| `running` | 6 | 前 5 帧 120ms，末帧 220ms | 正在工作；不是向某方向跑步 |
| `review` | 6 | 前 5 帧 150ms，末帧 280ms | 阅读、检查、思考、回顾 |

`prefers-reduced-motion` 开启时，所有动作都退化为该行第一帧，只有状态文字和道具变化。

### 3.2 内置宠物可选的 14 个扩展动作

扩展动作放在独立 `extras.webp` 中，每行 6 帧、单格仍为 192×208px。没有扩展动作时必须按下表回退，功能不能消失。

| 扩展动作 | 用途 | 无扩展图时的回退 |
| --- | --- | --- |
| `wake-up` | 第一次打开、清晨醒来 | `waving` + 小太阳 |
| `stretch` | 久坐、上午开始工作 | `jumping` + 拉伸垫 |
| `eat` | 午饭、晚饭 | `review` + 便当盒 |
| `drink` | 喝水提醒 | `waving` + 水杯 |
| `sleepy` | 夜深、等待用户收尾 | `waiting` + 月亮 |
| `sleep` | 安静时段、长时间不活跃 | `idle` 第一帧 + 睡帽 |
| `type` | 流式生成、运行工具 | `running` + 小键盘 |
| `read` | 读取记忆、每日回顾 | `review` + 书册 |
| `listen` | 录音、语音输入、TTS | `waiting` + 耳机 |
| `remind` | 时间线事项到点 | `waving` + 铃铛 |
| `carry` | 新建时间线、保存记忆 | `running-right` + 信封 |
| `celebrate` | 大任务完成、连续使用里程碑 | `jumping` + 徽章 |
| `comfort` | 请求失败后恢复、用户深夜仍忙 | `waving` + 暖灯 |
| `pat-react` | 抚摸或长按宠物 | `waving`，不加业务道具 |

### 3.3 状态决策优先级

同一时刻只能有一个主动作，但可以叠加一个道具和一条气泡。优先级从高到低：

| 优先级 | 事件 | 示例 | 可被打断 |
| ---: | --- | --- | --- |
| 100 | 用户直接互动 | 点击、抚摸、喂零食 | 仅可被紧急错误打断 |
| 90 | 需要用户处理的错误 | 断网、发送失败、通知通道失效 | 否 |
| 80 | 到点/临期事项 | 会议、截止日期、服药等自定义规则 | 可被用户互动打断 |
| 70 | 当前任务状态 | 思考、生成、读写记忆、执行工具 | 只被更高优先级打断 |
| 60 | 作息提醒 | 午饭、喝水、久坐、睡觉 | 是 |
| 40 | 成就与回访 | 完成、久别重逢、每周回顾 | 是 |
| 10 | 环境动作 | 眨眼、看窗外、随机走动 | 是 |

被更高优先级打断的普通动作直接丢弃；到点提醒保留为待处理标记，不能因为宠物正在庆祝而消失。

### 3.4 频率与降噪

- 环境动作间隔随机为 45～120 秒，一次不超过 4 秒。
- 非关键气泡至少间隔 10 分钟；同一文案 24 小时内不重复。
- 喝水、拉伸等习惯规则默认每天最多 4 次，并支持「今天别再提醒」。
- 吃饭、睡觉等固定作息每天最多一次。
- 发送成功只在服务器最终消息已对账后庆祝，流式结束不重复庆祝。
- 页面在后台时暂停纯视觉动作；到点事件交给现有通知系统，回到前台后再补一个克制的状态。
- 安静时段默认 23:30～08:00：只显示静态睡眠状态，除用户明确标为重要的规则外不弹气泡、不播放声音。

## 4. 触发与动作库

下列动作都可以由规则引擎组合，不要求每项一张专属动画。

### 4.1 时间与日常节律

| 条件 | 主动作 | 道具 | 气泡示例 | 快捷操作 |
| --- | --- | --- | --- | --- |
| 当天第一次打开，05:00～10:30 | `wake-up` | 小太阳 | 「早上好，今天慢慢来。」 | 查看今天 |
| 08:00～10:00 且连续使用 50 分钟 | `stretch` | 拉伸垫 | 「先活动一下肩颈？」 | 10 分钟后提醒 |
| 用户设定的早餐时间 | `eat` | 吐司盘 | 「早餐别只靠咖啡顶着。」 | 已吃 / 稍后 |
| 11:30～13:30 首次触发 | `eat` | 便当盒 | 「到午饭时间啦。」 | 已吃 / 30 分钟后 |
| 14:00～16:30 连续使用 60 分钟 | `drink` | 水杯 | 「喝两口水，再继续。」 | 喝过了 |
| 15:00～17:00 且当天任务密集 | `comfort` | 茶杯 | 「今天事情不少，先喘口气。」 | 查看今天 |
| 18:00～20:00 首次触发 | `eat` | 汤碗 | 「别忘了吃晚饭。」 | 已吃 / 稍后 |
| 20:00 后打开每日回顾 | `read` | 记忆册 | 「一起把今天收好。」 | 开始回顾 |
| 22:30 后仍在连续对话 | `sleepy` | 暖灯 | 「可以开始收尾了。」 | 设为最后一轮 |
| 23:30 后仍活跃 | `sleepy` | 月亮 | 「已经很晚了，剩下的明天再接。」 | 明早提醒 / 今天静音 |
| 01:00 后仍活跃 | `sleep` | 睡帽 | 「我先把灯调暗。你也早点休息。」 | 开启勿扰 |
| 周五 17:00 后 | `celebrate` | 小纸花 | 「这一周辛苦了。」 | 查看本周回顾 |
| 周末当天第一次打开 | `waving` | 小花篮 | 「今天也可以不赶进度。」 | 隐藏气泡 |
| 每月第一天 | `review` | 月历 | 「要不要看看上个月留下了什么？」 | 打开记忆库 |
| 连续离开应用超过 6 小时后返回 | `waving` | 门牌 | 「欢迎回来，我把这里看好了。」 | 查看未完成项 |
| 连续使用达到用户设定时长 | `stretch` | 计时器 | 「专注一阵了，眼睛也歇一会儿。」 | 休息 5 分钟 |

早餐、午饭、晚饭和睡觉时间必须允许用户关闭或改时段。默认值只作为首次体验模板，不应假设每个人作息一致。

### 4.2 用户直接互动

| 交互 | 动作 | 反馈 |
| --- | --- | --- |
| 鼠标首次悬停 | `idle` 转头 | 看向指针，不弹气泡 |
| 单击宠物 | `waving` | 打开「现在」小卡，显示最近事项和状态 |
| 双击宠物 | `jumping` | 一个短反应；不触发业务操作 |
| 长按/按住 600ms | `pat-react` | 抚摸反应，可轻微震动移动端设备 |
| 从窗台左侧点击 | `running-left` | 宠物跑向点击位置 |
| 从窗台右侧点击 | `running-right` | 宠物跑向点击位置 |
| 点击道具 | 与道具对应 | 打开该提醒或状态说明 |
| 点击气泡「×」 | `idle` | 只收起本条，不等于关闭整类提醒 |
| 选择「今天别再提醒」 | `sleep` 一次 | 对当前规则设置当日静音 |
| 从衣柜换装 | `jumping` | 换装成功后预览一次，不显示庆祝 toast 和气泡两套反馈 |

### 4.3 聊天与工具状态

| 应用事件 | 主动作 | 道具 | 文案/行为 |
| --- | --- | --- | --- |
| 用户发送消息 | `running-right` | 信封 | 把问题「送」向内容区，不弹气泡 |
| 等待首 token | `waiting` | 沙漏 | 超过 4 秒才显示「我在想」 |
| 模型思考 | `review` | 思考便签 | 只表示处理中，不展示虚假的思考内容 |
| 正在流式生成 | `type` | 小键盘 | 与消息流同步开始、结束 |
| 运行一般工具 | `running` | 工具包 | 「正在处理」；工具完成后收起 |
| 读取长期记忆 | `read` | 记忆册 | 书页使用靛蓝标记 |
| 写入长期记忆 | `carry` | 玻璃记忆瓶 | 最终写入成功后瓶中多一颗种子 |
| 查询时间线 | `review` | 月历 | 不弹气泡 |
| 新建时间线事项 | `carry` | 月历卡 | 「已经放进时间线。」可打开事项 |
| 联网搜索 | `review` | 小望远镜 | 仅在工具真的启用时出现 |
| 语音输入录制中 | `listen` | 麦克风 | 使用呼吸灯，不让宠物大幅运动 |
| TTS 播放中 | `listen` | 耳机 | 点击宠物可停止播放 |
| 用户停止生成 | `waiting` 转 `idle` | 收起的键盘 | 「停在这里了。」不表现失败 |
| 回答成功完成 | `waving` | 无 | 普通回答只轻挥手，不每次跳跃 |
| 长任务完成（>30 秒） | `celebrate` | 小徽章 | 一次短庆祝 |
| 请求失败 | `failed` | 断开的插头 | 「这次没有发出去。」提供重试 |
| 断网 | `failed` | 离线云 | 「网络断开，内容还留在这里。」 |
| 重试成功 | `comfort` | 接好的插头 | 「接上了，继续吧。」 |

### 4.4 时间线、提醒和回顾

| 条件 | 动作 | 道具 | 气泡示例 | 快捷操作 |
| --- | --- | --- | --- | --- |
| 会议 15 分钟内开始 | `remind` | 小铃铛 + 日历 | 「15 分钟后开会。」 | 打开事项 / 稍后 5 分钟 |
| 普通提醒到点 | `remind` | 闹钟 | 使用事项标题 | 完成 / 稍后 |
| 待办到点 | `carry` | 勾选卡 | 「现在要处理：{title}」 | 完成 / 打开 |
| 截止日期在 3 天内 | `review` | 旗子 | 「{title} 还有 3 天。」 | 打开 / 明天提醒 |
| 截止日期在 1 小时内 | `remind` | 琥珀色旗子 | 「只剩 1 小时了。」 | 打开事项 |
| 出行在 24 小时内 | `carry` | 小行李箱 | 「明天要出发，东西收好了吗？」 | 查看事项 |
| 生日在 24 小时内 | `celebrate` | 小蛋糕 | 「明天是 {name} 的生日。」 | 打开 / 已准备 |
| 事项逾期不到 6 小时 | `remind` | 翻面的日历卡 | 「这件事还没勾掉。」 | 完成 / 改期 |
| 事项逾期超过补发窗口 | `review` | 收纳夹 | 不逐条打扰，合并到简报 |
| 待确认事项超过 14 天 | `review` | 问号卡 | 「有一件旧安排还没确认。」 | 确认 / 取消 |
| 每日简报有内容 | `read` | 今日清单 | 「今天有 {n} 件事。」 | 展开清单 |
| 每日简报为空 | `idle` | 无 | 不主动提示「今天没有安排」 |
| 用户点「稍后」 | `carry` | 回转箭头 | 「好，{minutes} 分钟后再叫你。」 | 撤销 |
| 用户完成事项 | `jumping` | 绿色勾 | 一次短反馈，随后回到 `idle` |
| 每日整理开始 | `read` | 相册 | 安静工作，不弹气泡 |
| 每日整理完成且有变化 | `celebrate` | 新叶子 | 「今天的记忆已经收好了。」 | 查看变化 |

现有 Bark 通知继续负责应用关闭时的可靠送达。宠物不是另起一套调度器，而是消费同一条提醒事件；用户在宠物气泡中点击「稍后」也必须更新现有 `snoozed_until`。

### 4.5 成就与关系反馈

| 条件 | 动作 | 表现 |
| --- | --- | --- |
| 第一次保存记忆 | `celebrate` | 解锁「记忆瓶」道具 |
| 第一次完成每日回顾 | `celebrate` | 解锁「相册」道具 |
| 完成 10 个时间线事项 | `jumping` | 获得一枚窗台徽章 |
| 一周内完成 3 次回顾 | `read` | 窗台长出一片新叶；不显示连续签到 |
| 与同一宠物相处 7/30/100 天 | `pat-react` | 解锁动作或配色，不影响功能 |
| 导入新的自定义宠物 | `waving` | 新宠物做自我介绍，可立即改名 |
| 用户生日或自定义纪念日 | `celebrate` | 使用蛋糕和纸花，默认只出现一次 |

## 5. 用户自定义规则

### 5.1 规则编辑器

设置页增加「拾伴」分区，主入口不展示 JSON。一个规则由四行自然语言积木组成：

```text
当    [每天] [23:30 至 02:00]
如果  [应用正在使用] 且 [今天尚未提醒]
就    [困倦动作] [月亮] 说「已经很晚了，明天再继续吧」
并可  [开启勿扰] [30 分钟后提醒]
最多  [每天 1 次]，与上次至少间隔 [4 小时]
```

条件类型：

- 时间点、时间段、星期、日期、每月/每年重复。
- 应用可见、持续使用时长、离开应用时长、当前页面。
- 聊天状态：空闲、等待、思考、生成、成功、失败。
- 时间线：事项类型、距离开始时间、状态、是否逾期。
- 记忆与回顾：开始、完成、有无变更。
- 计数：今天触发次数、连续专注时长、完成事项数量。
- 手动事件：用户点击一个「叫宠物提醒我」快捷动作。

可执行动作：

- 选择核心或扩展动作。
- 选择一个道具和一条气泡文案。
- 添加最多两个快捷按钮。
- 可选应用内声音、TTS、Bark 通知；后两项默认关闭。
- 设置今天静音、稍后提醒、完成、打开页面或打开事项。

### 5.2 内置模板

首次启用时只默认打开「午饭」「夜深休息」「时间线到点」三条，其余模板由用户选择：

- 喝水：工作日 10:00～18:00，每 90 分钟、每天最多 4 次。
- 番茄钟：专注 25/50 分钟后休息 5/10 分钟。
- 久坐：连续使用 60 分钟后提醒伸展。
- 护眼：连续阅读/对话 45 分钟后看远处。
- 药物：指定时间，要求用户明确设置，支持重要提醒。
- 学习：到点拿出书本并打开指定会话或知识库路径。
- 下班：工作日指定时间提醒收尾，不评判是否加班。
- 每周回顾：周五或周日指定时间打开每日回顾。
- 自定义纪念日：每年重复，使用蛋糕或花束。

### 5.3 规则冲突

- 同一分钟命中多条普通习惯规则时，只展示优先级最高的一条，其他规则顺延 10 分钟后重新判断。
- 同一事项的宠物提醒和 Bark 共享 `dedupe_key` 语义，但分别记录展示与送达，避免浏览器开着时阻止手机推送。
- 用户规则可以覆盖内置规则的时间和文案，但不能改变系统错误的真实性。
- TTS 只能在应用前台、非安静时段并且用户开启声音时执行。
- 每条规则必须有「现在预览」和「查看下次触发时间」，否则用户很难判断配置是否生效。

建议存储结构：

```json
{
  "id": "sleep-reminder",
  "name": "夜深提醒",
  "enabled": true,
  "priority": 60,
  "when": {
    "type": "local_time_range",
    "start": "23:30",
    "end": "02:00",
    "weekdays": [1, 2, 3, 4, 5, 6, 7]
  },
  "if": [
    { "type": "app_visible", "value": true },
    { "type": "active_minutes", "minimum": 20 }
  ],
  "then": {
    "animation": "sleepy",
    "fallbackAnimation": "waiting",
    "prop": "moon",
    "message": "已经很晚了，明天再继续吧。",
    "actions": ["quiet-mode", "remind-30m"]
  },
  "throttle": {
    "maxPerDay": 1,
    "cooldownMinutes": 240
  }
}
```

## 6. 自定义宠物

### 6.1 用户流程

提供三条难度不同的路径：

1. **换一只内置宠物**：直接选择、改名、选择性格与主色。
2. **导入宠物包**：上传 `zip`，本地校验通过后预览并安装；适合会自行生图和制作图集的用户。
3. **从一张图孵化**：上传照片或角色图，选择「像素邻接 / 软陶 / 纸片」等受支持风格，由图像模型先生成标准主形象，再生成动作行并自动装配。生成前明确提示图片将发送给所选图像模型。

自定义宠物流程应是：命名 → 主形象 → 先生成 `idle` 与 `running-right` 验证一致性 → 生成其余动作 → 联系表预览 → 安装。若左右不对称，`running-left` 必须单独生成，不能简单镜像。

### 6.2 宠物包结构

建议新增与 `skills/` 类似的可写 `pets/` 挂载目录：

```text
pets/
└── dew-drop/
    ├── pet.json
    ├── spritesheet.webp      # 必需，1536×1872
    ├── thumbnail.webp        # 可从 idle 第 1 帧自动生成
    ├── extras.webp           # 可选，扩展动作
    └── props/                # 可选，覆盖通用道具
        └── sleep-hat.webp
```

基础 manifest：

```json
{
  "schemaVersion": 1,
  "id": "dew-drop",
  "displayName": "露珠",
  "description": "一只安静收集记忆的小水滴。",
  "spritesheetPath": "spritesheet.webp",
  "thumbnailPath": "thumbnail.webp",
  "extrasPath": "extras.webp",
  "cell": { "width": 192, "height": 208 },
  "grid": { "columns": 8, "rows": 9 },
  "personality": "calm",
  "speechStyle": "short-warm",
  "anchors": {
    "head": [0.5, 0.22],
    "leftSide": [0.2, 0.58],
    "rightSide": [0.8, 0.58],
    "front": [0.5, 0.72]
  }
}
```

`anchors` 使用单格内 0～1 的归一化坐标，让睡帽、杯子、铃铛等通用道具能适配不同体型。没有锚点时，道具退回窗台固定位置，不应硬贴到宠物身上。

### 6.3 性格不是另一套功能

性格只改变随机权重和文案语气，不改变关键提醒是否送达：

| 性格 | 行为权重 | 台词风格 |
| --- | --- | --- |
| 安静 | 更多 `idle/read`，少跳跃 | 短句、少感叹号 |
| 活泼 | 更多走动和 `jumping` | 明快，但不连续催促 |
| 稳重 | 更多 `review/carry` | 直接说明事项与时间 |
| 调皮 | 偶尔藏道具、探头 | 只用于环境动作，不戏谑错误和截止日期 |

## 7. 需要生成的图片与提示词

下面的提示词按「一张主形象 + 每个动作一条横向帧带 + 单独道具」组织。不要让图像模型直接画完整 8×9 最终大图；它很难稳定保证网格。先逐行动作生成，再用确定性工具抽帧和拼图。

### 7.1 主形象提示词

将花括号内容替换为实际设定：

```text
Design a small digital companion sprite named {宠物名}: {物种、材质、主色、标志性特征、性格}.
Compact chibi proportions, chunky readable silhouette, thick dark 1–2 px pixel-style outline,
visible stepped pixel edges, limited palette, flat cel shading, simple expressive face, tiny limbs.
Front three-quarter standing pose, full body, centered, generous safe padding.
Preserve one unmistakable identity feature: {特征}.
No text, no logo, no scenery, no floor, no cast shadow, no glow, no loose particles,
no glossy app-icon look, no painterly texture, no realistic fur, no soft gradient.
Single clean sprite on a flat chroma-key background color {KEY_COLOR};
the character must not contain colors close to {KEY_COLOR}.
```

主形象一旦确认，就作为所有动作行的 canonical reference。后续提示词都必须附上这张图。

### 7.2 9 条核心动作行提示词

每次生成都在下面这段公共约束后追加对应动作描述：

```text
Use the attached canonical pet image as the strict identity reference.
Create one horizontal animation strip with exactly {N} complete, separated frames of the same pet.
Keep the exact head shape, face, markings, palette, outline weight, proportions, accessory side and silhouette.
Each pose stays fully inside its equal frame slot with generous padding and never overlaps another slot.
Flat chroma-key background {KEY_COLOR}. No visible grid, labels, numbers, text, scenery, floor or shadows.
No motion lines, speed streaks, dust, detached stars, loose sparkles, floating punctuation, glow or blur.
Pixel-art-adjacent digital pet sprite, limited palette, flat cel shading, crisp stepped edges.
The first and last poses must form a clean loop.
```

| 动作行 | N | 追加提示词 |
| --- | ---: | --- |
| `idle` | 6 | Calm low-distraction breathing and one tiny blink. Only subtle body bob or material sway. No waving, walking, talking, working, props or large gestures. |
| `running-right` | 8 | The pet travels clearly to the right through body and limb poses. No speed lines, dust, floor shadow or trail. Keep gait cyclic and readable. |
| `running-left` | 8 | The pet travels clearly to the left. Preserve asymmetric markings and accessory handedness correctly; do not merely flip readable or side-specific details. |
| `waving` | 4 | A small friendly wave using paw or limb pose only: rest, lift, clear wave, return. No wave marks, sparkles or floating symbols. |
| `jumping` | 5 | Anticipation, lift, peak, descent, settle, shown only through body position. No floor shadow, dust, impact burst or landing mark. |
| `failed` | 8 | A readable but gentle deflated/error reaction, then partial recovery. A tear touching the face or tiny attached smoke puff is allowed; no red X, detached effects or dramatic collapse. |
| `waiting` | 6 | Patient waiting loop with a glance, tiny shift and return. Distinct from idle but still quiet. No clock, punctuation or new prop. |
| `running` | 6 | Busy working/in-progress loop in place. This is not foot-running: no jogging, raised knees, long steps, pumping arms or directional travel. |
| `review` | 6 | Focused inspecting/thinking loop using lean, eyes, blink, head tilt or paw position. No paper, magnifying glass, code, UI or punctuation unless already part of the base identity. |

### 7.3 扩展动作提示词

扩展动作仍需附主形象，每条生成 6 帧。公共约束与核心动作相同，但可以使用动作必需的、贴合身体的道具。

| 动作 | 追加提示词 |
| --- | --- |
| `wake-up` | Wake from curled rest, open eyes, sit up, tiny morning stretch, settle. |
| `stretch` | Gentle full-body stretch with clear anticipation, extension and release; no floor effects. |
| `eat` | Open a tiny lunch box, take one bite, pleased reaction, close or settle; prop design stays identical in all frames. |
| `drink` | Hold one small cup, take a sip, lower it and settle; no liquid splash or detached droplets. |
| `sleepy` | Slow blink, small yawn pose, nod, recover; a fitted sleep cap may be used consistently. |
| `sleep` | Curl or sit into a comfortable sleeping loop with subtle breathing; no floating Z symbols. |
| `type` | Work at one tiny compact keyboard, focused taps and blink; keyboard shape and position remain consistent. |
| `read` | Open one small memory album, scan a page, turn one page, settle; no readable text. |
| `listen` | Wear simple headphones, attentive head tilt, tiny ear/paw response; no sound waves or notes. |
| `remind` | Lift and gently ring one small handheld bell through pose changes; no floating sound lines. |
| `carry` | Pick up and carry one sealed envelope a short distance in place, then present it; no readable marks. |
| `celebrate` | Compact delighted celebration with raised paws and one bounce; no detached confetti or sparkles. |
| `comfort` | Warm, calm reassuring gesture with softened posture, then return; no speech bubble inside the sprite. |
| `pat-react` | Look upward, lean into a gentle pat, happy blink and settle; no visible human hand. |

### 7.4 通用道具图片清单与提示词

道具建议一物一图，源图 512×512、真实透明背景，最终缩到最长边 48～72px。公共提示词：

```text
One isolated {道具描述} for a small digital-pet interface.
Pixel-art-adjacent, compact chunky silhouette, thick dark 1–2 px stepped outline,
limited palette, flat cel shading, front three-quarter view, readable at 48 px.
Centered with safe padding. Real alpha transparency.
No pet, no hand, no text, no logo, no scenery, no floor, no cast shadow,
no glow, no loose particles, no checkerboard background, no duplicate object.
```

| 文件名 | `{道具描述}` | 主要场景 |
| --- | --- | --- |
| `sun.webp` | a tiny warm morning sun token with simple short rays attached to the disk | 早安 |
| `moon.webp` | a soft indigo crescent moon token | 夜深 |
| `sleep-hat.webp` | a small floppy indigo sleeping cap with a mint tip | 睡觉 |
| `warm-lamp.webp` | a tiny amber bedside lamp with no visible glow aura | 收尾、安慰 |
| `toast.webp` | a small plate with toast and one egg, no steam | 早餐 |
| `lunch-box.webp` | a compact mint and orange bento box, closed lid beside it | 午饭 |
| `soup-bowl.webp` | a small dinner soup bowl and spoon, no steam | 晚饭 |
| `water-cup.webp` | a small transparent-blue reusable water cup with a solid lid | 喝水 |
| `tea-cup.webp` | a tiny ceramic tea cup in warm cream and indigo, no steam | 午后休息 |
| `stretch-mat.webp` | a short rolled mint exercise mat | 久坐、拉伸 |
| `timer.webp` | a compact round focus timer with simple hands and no numerals | 番茄钟 |
| `hourglass.webp` | a small indigo hourglass with two solid sand shapes | 等待 |
| `keyboard.webp` | a tiny compact keyboard with blank keys | 生成、工具 |
| `toolbox.webp` | a small closed toolbox in indigo and amber, no logo | 工具执行 |
| `memory-book.webp` | a small cloth-bound indigo album with one leaf emblem, no text | 读取记忆 |
| `memory-jar.webp` | a small glass jar containing three solid colored seed beads | 写入记忆 |
| `calendar.webp` | a small blank calendar card with two binding rings, no numbers | 时间线 |
| `bell.webp` | a tiny handheld brass bell | 到点提醒 |
| `deadline-flag.webp` | a compact amber desk flag on a short stand | 截止日期 |
| `suitcase.webp` | a small rounded travel suitcase with one blank tag | 出行 |
| `cake.webp` | a tiny single-tier birthday cake with one unlit candle | 生日、纪念日 |
| `check-card.webp` | a small mint task card with one simple check mark | 完成事项 |
| `question-card.webp` | a small muted-blue card with one simple question mark | 待确认 |
| `envelope.webp` | a small sealed cream envelope with indigo edge, no writing | 发送、保存 |
| `telescope.webp` | a tiny tabletop telescope in indigo and brass | 联网搜索 |
| `headphones.webp` | compact over-ear headphones in indigo and mint | TTS/ASR |
| `offline-cloud.webp` | a compact gray cloud token with a small attached broken-link notch | 断网 |
| `badge.webp` | a small mint rosette badge with a blank center | 成就 |
| `flower-basket.webp` | a tiny basket holding three simple morning flowers | 周末、纪念日 |

### 7.5 记忆窗台背景

窗台优先用 CSS 实现，以便明暗主题和响应式尺寸稳定。如果希望增加位图质感，只需生成 4 张无宠物背景，尺寸 448×224（2×）：

公共提示词：

```text
A quiet minimal digital-pet window-sill background for the Morning Memory app,
exactly 448x224, pixel-art-adjacent flat shapes, restrained detail, no character,
no object that looks interactive, no text, no logo, no border, no cast shadow,
clear empty center and lower sill area for a pet sprite. Seamless visual edges.
```

- `habitat-dawn.webp`：mist blue sky, a very pale warm horizon, restrained indigo and mint.
- `habitat-noon.webp`：near-white sky, soft cool blue, highest contrast behind the pet.
- `habitat-dusk.webp`：muted apricot horizon fading into dusty indigo, no visible sun disk.
- `habitat-night.webp`：low-saturation deep blue sky with two tiny fixed stars, no moon because moon is an interactive prop.

## 8. 技术落点

### 8.1 前端组件

建议拆为四层：

```text
WorkspaceFrame
└── PetCompanion
    ├── PetRenderer          # 图集、帧、位置、reduced-motion
    ├── PetStateResolver     # 优先级、打断、回退、冷却
    ├── PetRuleEngine        # 本地时间与用户规则
    └── PetPopover/Sheet     # 气泡、快捷操作、规则与装扮入口
```

前端统一消费 `PetEvent`，页面不直接命令宠物播放某一帧：

```ts
type PetEvent = {
  id: string;
  kind: "chat" | "tool" | "memory" | "timeline" | "routine" | "interaction";
  name: string;
  occurredAt: string;
  priority: number;
  payload?: Record<string, unknown>;
  dedupeKey?: string;
};
```

聊天页已经知道 `submitting -> streaming -> completed/failed/cancelled` 状态，可直接派发事件；工具活动也已有标准名称。这样宠物层不需要解析 DOM 或猜测消息是否完成。

### 8.2 后端与现有能力的衔接

- 时间提醒继续使用 `TimelineItem.remind_at`、`snoozed_until` 和现有补跑逻辑。
- 宠物规则若只选「应用内」，由浏览器运行；若选 Bark/TTS 等外部送达，则持久化到后端调度。
- `notify_catchup_hours` 仍是补发边界，宠物不能绕开它制造提醒风暴。
- 使用运行时配置中的时区作为规则权威时区；浏览器时区只做未配置时的回退。
- 宠物资源放在新 `PETS_PATH` 可写挂载，备份流程要包含该目录；数据库只存当前选择、规则和触发记录。
- 建议新增 `pet_profiles`、`pet_rules`、`pet_rule_runs`，其中运行记录只保留去重所需窗口，不记录无意义的每次眨眼。

### 8.3 API 草案

```http
GET    /api/pets
POST   /api/pets/import
GET    /api/pets/{id}
DELETE /api/pets/{id}
PUT    /api/pets/active

GET    /api/pet-rules
POST   /api/pet-rules
PATCH  /api/pet-rules/{id}
DELETE /api/pet-rules/{id}
POST   /api/pet-rules/{id}/preview
POST   /api/pet-rules/{id}/snooze
```

导入接口必须校验路径穿越、manifest schema、图集尺寸、透明通道、未使用格为空、总解压大小和文件类型。失败时返回具体到行/帧的错误，不要只说「宠物包无效」。

### 8.4 事件时序

```text
用户发送消息
  → chat:submitting      → running-right + 信封
  → chat:waiting         → waiting（超过 4 秒才显示）
  → tool:memory-read     → review/read + 记忆册
  → chat:streaming       → running/type + 键盘
  → chat:completed       → waving；长任务才 celebrate

时间线到点
  → 后端 sweep 命中并保留可靠通知
  → 前端在线时收到/轮询同一事项事件
  → remind + 对应道具 + 气泡
  → 用户点「稍后」
  → 更新 snoozed_until
  → 宠物确认一次，不重复创建另一条规则
```

Web Push 尚未实现前，浏览器在线提醒可以用对下一条 `remind_at` 的轻量查询加 `visibilitychange` 时校正；不能依赖一个长时间 `setTimeout`，电脑睡眠后会漏。

## 9. 可访问性、隐私与控制感

- 宠物容器默认 `aria-hidden`；只有包含可执行提醒的气泡进入克制的 `aria-live="polite"` 区域。
- 所有气泡按钮可键盘访问，Esc 收起，关闭后焦点回到宠物按钮。
- 减少动态效果时使用静态首帧；「完全隐藏宠物」必须是一键设置。
- 提供三档活跃度：安静、标准、活泼；实际控制环境动作和非关键气泡，不影响错误与已开启的提醒。
- 默认静音。声音、TTS、设备震动分别授权，不能打包成一个含糊的「增强体验」。
- 上传照片孵化宠物前，明确显示会使用哪个图像模型、图片是否离开本机，以及生成失败是否保留原图。
- 自定义台词不得自动送进模型；只有用户主动选择「让 AI 改写台词」时才发送。

## 10. 分阶段实现

### Phase A：有生命的核心版

- 记忆窗台、核心 9 动作、4 个时间窗台。
- 聊天等待/生成/完成/失败、记忆读写、时间线创建共 10～12 个真实状态。
- 午饭、夜深、连续使用和时间线到点 4 类提醒。
- 12 个通用道具、安静时段、降低动态效果、完全隐藏。
- 一个内置宠物；先验证三天使用后是否仍然舒服。

### Phase B：可调教版

- 规则编辑器、模板、预览、冷却、每日静音和冲突处理。
- 与现有 snooze、完成事项、Bark 通知接通。
- 导入标准宠物包、图集自动校验、换宠物和改名。
- 活跃度与四种性格。

### Phase C：真正的自定义宠物

- 从一张图孵化，按「主形象 → idle/running-right → 其余动作 → QA」生成。
- 扩展动作、道具锚点、装扮和宠物专属道具覆盖。
- 联系表、逐行动画预览、只重做失败行。
- 宠物包导出，让用户能备份和分享。

### Phase D：长期陪伴

- 非惩罚式相处里程碑、窗台收藏与季节变化。
- Web Push 到达时与浏览器内宠物状态同步。
- 可选的社区宠物包目录；安装前仍需本地校验。

## 11. 验收标准

- 路由切换时宠物不重置、不闪烁，侧栏和内容区几何尺寸不发生偏移。
- 同一时刻只有一个主动作；高优先级提醒不会被普通环境动作吞掉。
- 应用休眠再唤醒后不会一次播放一串过期动作，也不会漏掉仍在补发窗口内的事项。
- 发送、停止、失败、重试、工具执行和最终完成都与真实状态一致。
- 自定义规则能显示下次触发时间，并能即时预览和当日静音。
- 自定义宠物只有核心 9 动作时，所有功能仍有可理解的回退表现。
- 开启减少动态效果后没有循环动画；隐藏宠物后不残留气泡、声音或不可见焦点。
- 移动端软键盘、底部导航、200% 缩放下宠物不遮挡关键操作。
- 新增宠物包能被备份、恢复、导出和删除；删除明确说明影响且不会删除用户的时间线或规则。
- 连续使用三天后，默认配置下非关键主动气泡平均每天不超过 4 条。

