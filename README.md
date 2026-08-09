# 朝花夕拾 · Personal Memory

<p align="center">
  <img src="frontend/public/morning-memory-wordmark.png" alt="朝花夕拾 · Personal Memory" width="720" />
</p>

<p align="center">
  <strong>一个会记住你的私人 AI 助手。</strong><br />
  把对话沉淀为可查看、可编辑、可回滚的长期记忆，也把待办与提醒带回日常生活。
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-FastAPI-009688?logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Next.js-15-000000?logo=next.js&logoColor=white" alt="Next.js" />
  <img src="https://img.shields.io/badge/PostgreSQL-runtime-4169E1?logo=postgresql&logoColor=white" alt="PostgreSQL" />
</p>

<p align="center">
  <a href="#功能">功能</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#配置边界">配置边界</a> ·
  <a href="#技能agent-skills">技能</a> ·
  <a href="docs/roadmap.md">开发路线</a>
</p>

> 当前项目仍在持续重构。本文只保留稳定的产品能力、使用方式与近期计划；实现细节以代码和相关设计文档为准。

## 功能

| 模块 | 当前能力 |
| --- | --- |
| 💬 聊天 | 流式对话、思考过程展示、重新生成、编辑后重发，以及会话级模型切换 |
| 🧠 记忆 | 对话中的实时读写、记忆查看与编辑、版本历史、回滚、每日回顾与整理 |
| 🗓️ 时间线 | 从对话提取待办和提醒，支持重复事项与 Bark 通知 |
| 🧩 模型服务 | 将“服务地址”和“模型档案”分离管理，支持 Anthropic、OpenAI 兼容服务，并可在设置页添加、测试和切换 |
| 🎙️ 本地能力 | 可选的本地 TTS/ASR，以及只读 Obsidian vault 知识库 |
| 🌐 联网搜索 | 在聊天输入框按需开启 Tavily 搜索；默认关闭，Key 只保留在后端环境变量中 |
| 🧰 Agent Skills | 从设置页安装、上传、查看、启停和删除技能；也支持直接把本地技能目录放入 `skills/` |
| 🔭 开发工具 | 可选 Phoenix/OpenTelemetry 观测，以及可重复运行的记忆整理评测 |

```mermaid
flowchart LR
    Chat["💬 对话<br/>原始记录"] --> Review["🌅 每日回顾<br/>会话摘要"]
    Review --> Memory["🧠 长期记忆<br/>可编辑 / 可回滚"]
    Memory -. 按需读取 .-> Chat
    Chat --> Timeline["🗓️ 时间线<br/>提醒与通知"]
    Models["🧩 模型服务<br/>Profile 选择"] -. 服务于 .-> Chat
```

## 快速开始

需要 Docker Desktop 或 Docker Compose，并准备至少一个模型服务的 API Key。

```bash
cp .env.example .env
# 编辑 .env：填写数据库配置和至少一个模型服务的 API Key
# 如果要使用联网搜索，再填写：TAVILY_API_KEY=tvly-...
docker compose up -d --build
```

首次启动会自动执行数据库迁移。启动后访问：

| 地址 | 用途 |
| --- | --- |
| <http://localhost:13000> | Web 界面 |
| <http://localhost:18000/health> | API 健康检查 |

模型服务、具体模型、助手规则、记忆整理、通知和语音偏好，请在设置页配置。

如果配置了 `TAVILY_API_KEY`，聊天输入框左下角的「+」菜单会出现「联网搜索」。它默认关闭，
打开后只对发送请求显式启用，关闭即可停止后续搜索；搜索 Key 不会返回给浏览器或模型。

常用命令：

```bash
docker compose logs -f api
docker compose exec api pytest -q
```

## 配置边界

配置遵循“启动依赖放环境，运行时偏好放界面”的原则，避免把所有内容都堆进 `.env`。

| 位置 | 只负责什么 |
| --- | --- |
| `.env` | 数据库连接、API Key、外部服务地址、访问保护 Key、宿主机挂载路径等启动必需项 |
| 设置页 | 模型服务与模型档案、默认模型、助手规则、记忆整理、通知、TTS/ASR 等运行时配置 |
| 数据库 | 持久化保存设置页中的配置、会话、消息、记忆和时间线数据 |

模型服务在设置页保存服务协议、地址、模型 ID 和凭据引用；真正的密钥值仍只放在 `.env`，不会写入数据库。

## 技能（Agent Skills）

技能是放在磁盘上的任务说明书。模型平时只看到技能的名称和一句话用途，判断任务相关后才读取
`SKILL.md` 正文；正文点名的参考资料再按需读取。技能里的脚本只会作为文本查看，不会在后端执行。

### 使用技能

在设置页进入「技能」可以：

- 从 `owner/repo`、GitHub 子目录、指定分支或 `.zip` 直链安装；
- 上传本地 `.zip`；
- 查看 `SKILL.md`、启用/停用技能，或从磁盘删除技能。

也可以直接把技能目录放进本地 `skills/`。最小结构如下：

```text
skills/
└── meeting-notes/
    └── SKILL.md
```

`SKILL.md` 必须以 YAML frontmatter 开头，且 `name` 要和目录名一致：

```markdown
---
name: meeting-notes
description: 整理会议记录并提炼行动项。需要处理会议笔记时使用。
version: "1.0"
---

# 会议记录整理

在这里写具体步骤和注意事项。
```

Docker 默认把宿主机 `./skills` 挂载到容器 `/skills`。如果技能放在其他目录，在 `.env` 设置
`SKILLS_HOST_DIR=/你的技能目录`；技能总开关可以在设置页控制。`skills/` 中的本地技能属于运行数据，
默认不会提交到 Git，完整约定见 [`skills/README.md`](skills/README.md) 和
[`docs/internals.md`](docs/internals.md#技能agent-skills)。

## 近期计划

- [ ] 增加独立 worker，执行记忆整理、通知、备份等后台任务
- [ ] 提高每日整理的可靠性，并支持必要时回查对话原文
- [ ] 完成数据库与记忆文件的自动备份、恢复文档和恢复演练
- [ ] 补充最小 CI、运行监控与告警

后台任务会优先复用现有 PostgreSQL，保持部署简单，暂不额外引入 Redis 等基础设施。更完整的阶段计划见[开发路线](docs/roadmap.md)。

## 文档

- [开发路线](docs/roadmap.md)：按优先级记录待办、验证方式和暂不实施的事项
- [后端架构](docs/architecture.md)
- [内部机制](docs/internals.md)：记忆、工具调用和后台流程的实现说明
- [前端与 API 契约](docs/frontend-api.md)：前后端接口和模型选择约定
- [技能目录说明](skills/README.md)：本地技能目录的基本约定
- [时间线设计](docs/timeline.md)：事项提取、重复规则和通知边界
- [可观测性](docs/observability.md)
- [备份与恢复](docs/backup.md) · [评测](docs/evaluation.md)：调试链路与记忆质量评估

## 项目状态

这是一个面向个人使用的持续迭代项目。配置项、API 和数据库结构可能随重构调整；稳定后会再补充更完整的部署与开发文档。
