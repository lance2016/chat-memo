from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    # JSONB 在 Postgres 上用原生类型，在 SQLite（测试）上退化为 JSON。

    # 不是「忘了加 ClassVar」。
    type_annotation_map = {  # noqa: RUF012
        dict[str, Any]: JSON().with_variant(JSONB(), "postgresql")
    }


def _now_column() -> Mapped[dt.datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelService(Base):
    """一个模型服务连接，密钥只保存为环境变量引用，不落库。"""

    __tablename__ = "model_services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    # anthropic | openai_compatible
    protocol: Mapped[str] = mapped_column(String(32))
    base_url: Mapped[str] = mapped_column(String(500), default="")
    # 例如 DEEPSEEK_API_KEY。这里永远不保存真正的 secret。
    credential_ref: Mapped[str] = mapped_column(String(120), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=dict
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    profiles: Mapped[list[ModelProfile]] = relationship(
        back_populates="service", cascade="all, delete-orphan"
    )


class ModelProfile(Base):
    """一个服务上的具体模型，以及它对产品能力的声明。"""

    __tablename__ = "model_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service_id: Mapped[int] = mapped_column(
        ForeignKey("model_services.id", ondelete="CASCADE"), index=True
    )
    slug: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    model_id: Mapped[str] = mapped_column(String(240))
    display_name: Mapped[str] = mapped_column(String(160))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    capabilities: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=dict
    )
    options: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=dict
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    service: Mapped[ModelService] = relationship(back_populates="profiles")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    archived_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 该会话是否思考。NULL = 跟随全局默认，前端切换时才写具体值。
    thinking: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # NULL = 尚未固定，第一轮会记录当时的默认模型。
    model_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_profiles.id", ondelete="SET NULL"), nullable=True, index=True
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.id",
    )


class Message(Base):
    """一轮完整的 Anthropic 消息。

    ``content`` 存 content block 数组原文（含 thinking / tool_use / tool_result），
    多轮回传时必须原样送回 —— 只抽 text 会破坏 thinking 签名并触发 400。
    """

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    usage: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )
    # 记录这条消息实际使用的模型；旧消息为空是正常的。
    model_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_profiles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 只含 text 块的纯文本，写入时冗余出来专供搜索。
    # 正文埋在 JSONB 数组里没法直接建索引；抽出来才能挂 GIN 三元组索引，
    # 也顺带把 thinking / tool_use 这些噪音排除在搜索结果之外。
    search_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class ConversationSummary(Base):
    __tablename__ = "conversation_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    summary: Mapped[str] = mapped_column(Text)
    # 面向每日回顾的那一份：这轮对话推进了什么、卡在哪、做了什么决定。
    # summary 是给记忆用的（只留事实、跳过技术细节），对「今天干了什么」太干瘪，
    # 所以摘要那一次调用同时产出两份。老数据没有这一列，保持 NULL。
    recap: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 这轮里用户说的、值得原样留存的一句话。只有摘要这一步看得到原始对话，
    # 回顾那步拿到的已经是转述了 —— 引语必须在这里抽，过了这道就没有原文了。
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 摘要覆盖到的最后一条消息，增量生成时作为水位线。
    up_to_message_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Memory(Base):
    """一条长期记忆，逻辑上是记忆文件树里的一个文件。"""

    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MemoryVersion(Base):
    """每次记忆变更的不可变快照，用于审计和回滚。

    删除后原 memory 行消失，所以这里不设外键，只留 path 和 memory_id 值。
    """

    __tablename__ = "memory_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    memory_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    path: Mapped[str] = mapped_column(String(512), index=True)
    content: Mapped[str] = mapped_column(Text)
    operation: Mapped[str] = mapped_column(String(16))  # created|modified|deleted
    actor: Mapped[str] = mapped_column(String(16))  # chat|consolidation|manual
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class MemoryRead(Base):
    """模型每次读记忆的埋点。

    写入侧已经有 ``memory_versions`` 了，这里只记**读取**——没有它就无法回答
    「我攒的这些记忆到底有没有被用上」，而那是判断记忆质量的核心指标。

    ``found=False`` 表示模型想读一个不存在的路径，说明索引和实际内容对不上，
    是索引质量的信号。
    """

    __tablename__ = "memory_reads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String(512), index=True)
    actor: Mapped[str] = mapped_column(String(16))  # chat|consolidation|ingest|manual
    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    found: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class KbRead(Base):
    """模型每次访问知识库（Obsidian vault）的埋点。

    知识库目前只读，这张表是将来「要不要开写权限」的决策依据：
    用得多不多、搜了什么、有没有搜到（found=False 的 search 是内容缺口信号）。
    """

    __tablename__ = "kb_reads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    command: Mapped[str] = mapped_column(String(16))  # search|read|list|backlinks
    # search 存 query，其余命令存 vault 相对路径
    target: Mapped[str] = mapped_column(String(512), index=True)
    found: Mapped[bool] = mapped_column(Boolean, default=True)
    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class AppSetting(Base):
    """可在设置页修改的运行时配置，覆盖 .env 里的同名默认值。

    做成 key-value 而不是固定列：加一个配置项不需要迁移。
    只有白名单里的 key 能写（见 app/settings_store.py），密钥和基础设施配置
    永远只存在于 .env —— 改坏 cors_origins 或 api_key 会把自己锁在门外。
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DailyDigest(Base):
    """这一天是什么，每日回顾页的主角。

    一天只有一行：重跑整理是覆盖而不是追加 —— 这是「这一天是什么」的当前答案，
    不是历史流水。想看过程去翻 conversation_summaries 和 memory_versions。

    headline/highlights 回答「做了什么」，title/observation/quote/echoes 回答
    「这是哪一天」。后面四个是这张表存在的理由 —— 只回答前者的话，它就只是一份
    更短的会话列表，没人会翻回来看。
    """

    __tablename__ = "daily_digests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[dt.date] = mapped_column(Date, unique=True, index=True)
    headline: Mapped[str] = mapped_column(Text)
    highlights: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=list
    )
    # 给这天起的名字，像给照片归档起标题。翻年度列表时一眼认出是哪天，比 headline 更短。
    title: Mapped[str] = mapped_column(Text, default="")
    # 一句只有看过全天对话的人才写得出的观察 —— 跨会话的模式、反复出现的主题、状态。
    observation: Mapped[str] = mapped_column(Text, default="")
    # 当天对话里用户自己说的一句原话，原样保留不改写。
    quote: Mapped[str] = mapped_column(Text, default="")
    # 和过去的连线：[{"text": "...", "kind": "recurring|followup|anniversary"}]
    echoes: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=list
    )
    # 记下是谁写的，换模型后回看质量差异时有据可依。
    model: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OpenLoop(Base):
    """无明确日期、之后可能仍需关注的问题、决定或后续动作。

    单独一张表而不是塞进 digest，因为它必须跨天存活。明确日期的安排属于
    TimelineItem，不在这里重复。source_conversation_id 不设外键：会话删了，
    这条关注事项本身还是有效的（同 MemoryVersion 的取舍）。
    """

    __tablename__ = "open_loops"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    opened_on: Mapped[dt.date] = mapped_column(Date, index=True)
    closed_on: Mapped[dt.date | None] = mapped_column(Date, nullable=True, index=True)
    closed_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|closed|dropped
    source_conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor: Mapped[str] = mapped_column(String(16), default="consolidation")  # consolidation|manual
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TimelineItem(Base):
    """从对话或手工录入的结构化时间事项。

    OpenLoop 表示「无明确日期但可能仍需关注」；这里要求 starts_at，专门支撑今天、最近和
    日历视图。来源消息不设外键，避免删除会话时把已经确认的日程一并删掉。
    """

    __tablename__ = "timeline_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(240))
    details: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(24), default="todo", index=True)
    status: Mapped[str] = mapped_column(String(24), default="confirmed", index=True)
    starts_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    # 用户原话里表示时间的那几个字（「明早九点」）。starts_at 是解析结果，这里是依据 ——
    # 时间不对时能一眼看出是用户说得含糊，还是模型解析错了。
    said: Mapped[str] = mapped_column(String(120), default="")
    ends_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    location: Mapped[str] = mapped_column(String(240), default="")
    recurrence: Mapped[str] = mapped_column(String(24), default="none")
    actor: Mapped[str] = mapped_column(String(16), default="manual")
    source_conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    source_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ---- 主动提醒 ----
    # 这一条要不要提醒。关掉但保留事项本身 —— 删掉才是「这件事不存在了」。
    notify: Mapped[bool] = mapped_column(Boolean, default=True)
    # 提前多少分钟。NULL = 按 kind 取默认值（app/notify/schedule.py）。
    lead_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 派生自 starts_at 和 lead_minutes，但**存下来**才能让 ticker 一条 SQL 扫完待发提醒，
    # 而不是把整张表拉回 Python 里算。NULL = 不提醒（已完成、已取消或 notify 关掉了）。
    remind_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # 用户在通知里点了「稍后」。到这个时间之前不再提醒。
    snoozed_until: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Notification(Base):
    """一条已生成的主动通知，以及它的送达结果。

    幂等**靠 dedupe_key 的唯一约束**，不靠 item 上的 notified_at。ticker 是补跑式的
    （查「该发而没发的」而不是精确定时触发），所以进程重启、笔记本睡醒之后一定会
    重新扫到同一批；抢占这个 key 是唯一能防重的地方。

    保留失败记录而不是删掉：通道挂了要能从这张表看出来是「没生成」还是「生成了没送出去」。
    timeline_item_id 不设外键 —— 事项删了，这条送达记录本身还是排障证据。
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # item:<id>:<触发时刻> / briefing:<日期> / test:<时间戳>
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(24))  # item|briefing|test
    title: Mapped[str] = mapped_column(String(240))
    body: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(500), default="")
    timeline_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 实际送达的通道，逗号分隔。空 = 一个都没成功。
    channels: Mapped[str] = mapped_column(String(120), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    # 送达尝试次数。通道临时抽风时，下一次 tick 要能重试 —— 只认 dedupe_key
    # 会让一条提醒因为对面挂了一分钟就永久丢失。到上限还没成功才放弃。
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    delivered_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


Index("ix_memory_versions_path_created", MemoryVersion.path, MemoryVersion.created_at)
Index("ix_memory_reads_path_created", MemoryRead.path, MemoryRead.created_at)
Index("ix_kb_reads_target_created", KbRead.target, KbRead.created_at)
