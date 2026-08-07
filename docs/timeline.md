# 时间线模块

> 状态：MVP 已实现  
> 首次实现：2026-08-07  
> 范围：对话提取、结构化存储、API、今天/最近/月历视图、人工校正。

## 产品定位

时间线把对话里散落的未来事项变成可操作的结构化记录，包括待办、会议、提醒、生日、
旅行、截止日期和重要时间节点。它不替代长期记忆，也不替代每日回顾中的关注事项：

- `Memory` 保存长期背景事实。
- `OpenLoop` 保存没有明确日期、之后可能仍需关注的问题、决定或后续动作。
- `TimelineItem` 保存有明确日期或时间、适合按日程查看的事项。

## 第一版能力

- 模型工具：`timeline_list`、`timeline_create`、`timeline_update`。
- 明确安排直接记为 `confirmed`；“可能、也许、暂定”记为 `pending`。
- 创建前可查询近期事项，改期、完成和取消应更新原记录，避免重复。
- `/timeline` 提供“今天”“最近 30 天”“月视图”。
- 支持手动创建、确认模型提取结果、完成、重新打开和删除。
- 模型创建的事项保留来源会话，可从时间线跳回原对话。
- 移动端增加五项底部主导航，时间线不再只能从桌面侧栏进入。

## 数据模型

`timeline_items` 的关键字段：

| 字段 | 说明 |
|---|---|
| `kind` | `todo/event/reminder/birthday/travel/deadline/note` |
| `status` | `pending/confirmed/completed/cancelled` |
| `starts_at`, `ends_at` | 带时区的时间；开始时间必填 |
| `all_day`, `timezone` | 全天事项与原始 IANA 时区 |
| `location`, `recurrence` | 地点；第一版支持 `none/yearly` |
| `actor` | `chat` 或 `manual` |
| `source_conversation_id` | 模型提取时的来源会话 |

来源 ID 不设外键：删除原会话不会连带删除用户已经确认的日程。

## 提取边界

模型只应记录未来且具有时间依据的事项。过去事件、泛泛愿望、没有日期的想法不进入时间线；
后者如果之后仍可能需要关注，可以由每日整理进入 OpenLoop。两个区域互斥：一旦有明确日期、
时间、提醒或截止期限，就应进入时间线，不再重复进入 OpenLoop。相对日期以每轮注入的当前时间和 `TZ`
为准，工具时间必须使用包含 UTC offset 的 ISO 8601。

## API

```http
GET    /api/timeline?from=<ISO>&to=<ISO>&status=pending,confirmed
POST   /api/timeline
PATCH  /api/timeline/{id}
DELETE /api/timeline/{id}
```

创建示例：

```json
{
  "title": "和产品团队开会",
  "kind": "event",
  "status": "confirmed",
  "starts_at": "2026-08-10T15:00:00+08:00",
  "ends_at": "2026-08-10T16:00:00+08:00",
  "timezone": "Asia/Shanghai",
  "location": "线上"
}
```

## 后续阶段

1. 编辑完整字段、拖拽改期和更丰富的重复规则。
2. 待确认事项的批量处理与重复检测强化。
3. 通知提醒、ICS 导入导出和外部日历同步。
4. 每日回顾展示当天已完成事项，并让 TimelineItem 与 OpenLoop 显式互转。
5. 在真实使用数据基础上评估自然语言搜索和智能冲突提醒。

外部日历同步和系统通知不属于第一版，避免在提取准确性尚未验证前扩大副作用。
