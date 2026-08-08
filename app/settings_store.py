"""可写运行时设置：数据库覆盖层。

分层解析，和会话级思考开关是同一套模型：

    会话覆盖   conversations.thinking
        ↓ 为空落到
    数据库设置 app_settings 表          ← 设置页可改，改完立刻生效
        ↓ 为空落到
    代码默认  Settings                  ← .env 只负责密钥和基础设施

**不做缓存。** 单人使用，每次请求多一条小查询可以忽略，
换来的是「改了立刻生效」这个确定性 —— 缓存失效的排查成本远高于那点查询开销。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import AppSetting
from app.notify.channels import KNOWN_CHANNELS


class SettingError(ValueError):
    """配置值不合法。调用方翻译成 400。"""


@dataclass(frozen=True)
class Field:
    """一个可写配置项的元数据。"""

    key: str
    label: str
    # str | text | int | bool | enum。text 和 str 的校验完全一样，
    # 只是告诉前端渲染成多行文本框而不是单行输入。
    kind: str
    choices: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    allow_empty: bool = False
    # 只在特定 provider 下有意义，前端可据此分组显示
    provider: str = ""
    # 界面分区。空 = 模型与整理（历史默认区），"tts" = 语音
    group: str = ""


# 白名单。**不在这里的字段一律拒绝写入**，包括密钥、数据库连接、CORS、日志。
# 判据是三类：密钥、基础设施、以及改错会把自己锁在门外的东西
# （改坏 cors_origins 或 api_key，设置页自己就打不开了）。
WRITABLE: tuple[Field, ...] = (
    Field("owner_name", "助手怎么称呼你", "str", minimum=1, maximum=32,
          group="prompt"),
    # 每轮都进 system prompt，所以要有上限。4000 字大约 4000 token，
    # 首轮之后命中缓存，成本可以接受；再长就该写成记忆文件让模型按需 view 了。
    Field("custom_instructions", "自定义指令", "text", allow_empty=True,
          maximum=4000, group="prompt"),
    Field("provider", "模型厂商", "enum", choices=("anthropic", "deepseek")),
    # 可观测性的两个开关。运行时生效（apply_tracing 会重新 instrument），
    # 所以可以放这儿 —— 其余 obs_* 是启动期读的，不进白名单，由状态卡如实报告。
    Field("backup_auto", "每天自动备份", "bool"),
    Field("backup_keep", "保留备份份数", "int", minimum=1, maximum=365),
    Field("obs_tracing", "记录模型调用链路", "bool", group="debug"),
    Field("obs_capture_content", "链路里保存对话正文", "bool", group="debug"),
    # 由模型目录管理，保留在 Settings 里只是为了让旧的配置解析链路能够读取。
    # group 非空，所以不会作为普通数字输入暴露在设置页。
    Field("chat_model_profile_id", "聊天模型档案", "int", minimum=1,
          group="model-routing"),
    Field("consolidate_model_profile_id", "整理模型档案", "int", minimum=1,
          group="model-routing"),
    Field("deepseek_model", "DeepSeek 模型", "str", provider="deepseek"),
    Field("deepseek_max_tokens", "输出上限", "int", minimum=256, maximum=128000,
          provider="deepseek"),
    Field("deepseek_thinking", "默认思考", "bool", provider="deepseek"),
    Field("model", "Claude 模型", "str", provider="anthropic"),
    Field("max_tokens", "输出上限", "int", minimum=256, maximum=128000,
          provider="anthropic"),
    Field("effort", "推理强度", "enum",
          choices=("low", "medium", "high", "xhigh", "max"), provider="anthropic"),
    Field("consolidate_model", "整理专用模型", "str", allow_empty=True),
    # 配了 SILICONFLOW_API_KEY 或旧的 ZHIPU_API_KEY 时生效。
    Field("title_model", "标题专用模型", "str", allow_empty=True),
    Field("consolidate_auto", "自动每日整理", "bool"),
    Field("consolidate_hour", "自动整理时间（点）", "int", minimum=0, maximum=23),
    Field("max_tool_iterations", "单轮最大工具次数", "int", minimum=1, maximum=30),
    Field("history_max_chars", "历史上下文上限", "int", minimum=8_000,
          maximum=500_000),
    Field("notify_enabled", "主动通知", "bool", group="notify"),
    Field("notify_channels", "启用的通道", "str", allow_empty=True, maximum=120,
          group="notify"),
    Field("notify_briefing", "每日简报", "bool", group="notify"),
    Field("notify_briefing_hour", "简报时间（点）", "int", minimum=0, maximum=23,
          group="notify"),
    Field("notify_all_day_hour", "全天事项提醒时间（点）", "int", minimum=0, maximum=23,
          group="notify"),
    Field("notify_default_lead_minutes", "默认提前量（分钟）", "int", minimum=0,
          maximum=10080, group="notify"),
    Field("notify_catchup_hours", "补发窗口（小时）", "int", minimum=1, maximum=168,
          group="notify"),
    Field("notify_smart_copy", "让模型写提醒文案", "bool", group="notify"),
    Field("notify_timeout", "通知请求超时（秒）", "int", minimum=1, maximum=120,
          group="notify"),
    Field("notify_public_base_url", "通知跳转地址", "str", allow_empty=True,
          maximum=200, group="notify"),
    Field("bark_server", "Bark 服务器", "str", allow_empty=True, maximum=200,
          group="notify"),
    Field("bark_key", "Bark 设备 key", "str", allow_empty=True, maximum=120,
          group="notify"),
    Field("bark_sound", "Bark 提示音", "str", allow_empty=True, maximum=60,
          group="notify"),
    Field("bark_icon", "Bark 图标地址", "str", allow_empty=True, maximum=200,
          group="notify"),
    Field("tts_mode", "语音播放", "enum", choices=("off", "manual", "auto"),
          group="tts"),
    Field("tts_model", "语音模型", "str", group="tts"),
    Field("tts_voice", "音色", "str", allow_empty=True, group="tts"),
    Field("tts_lang_code", "语种", "str", group="tts"),
    Field("tts_instruct", "语气指令", "str", allow_empty=True, maximum=200,
          group="tts"),
    Field("tts_format", "音频格式", "enum", choices=("mp3", "wav", "flac", "opus"),
          group="tts"),
    Field("tts_stream", "流式合成", "bool", group="tts"),
    Field("tts_speed_percent", "语速（%）", "int", minimum=50, maximum=200,
          group="tts"),
    Field("tts_max_chars", "单次朗读字数上限", "int", minimum=50, maximum=5000,
          group="tts"),
    Field("tts_timeout", "合成超时（秒）", "int", minimum=5, maximum=600, group="tts"),
    Field("tts_warmup", "启动时预热语音模型", "bool", group="tts"),
    Field("asr_model", "识别模型", "str", group="asr"),
    Field("asr_language", "识别语言", "enum",
          choices=("Chinese", "English", "Auto"), group="asr"),
    Field("asr_max_tokens", "识别长度上限", "int", minimum=64, maximum=2048,
          group="asr"),
    Field("asr_timeout", "识别超时（秒）", "int", minimum=5, maximum=600,
          group="asr"),
    Field("debug_prompts", "记录发给模型的请求", "bool", group="debug"),
)

WRITABLE_BY_KEY = {f.key: f for f in WRITABLE}

# 明确告诉前端这些只能改环境变量，别在界面上给入口。
# 这里仅保留密钥、连接地址、挂载路径和安全边界；用户运行时参数应进入上面的 WRITABLE。
ENV_ONLY = (
    "database_url",
    "anthropic_api_key",
    "deepseek_api_key",
    "deepseek_base_url",
    "siliconflow_api_key",
    "siliconflow_base_url",
    "siliconflow_title_model",
    "zhipu_api_key",
    "zhipu_base_url",
    "api_key",
    "cors_origins",
    "log_level",
    "log_color",
    "log_access",
    "log_format",
    # 地址算基础设施：容器内外写法不同，改错了设置页只会看到「连不上」
    "tts_base_url",
    "tts_model_cache",
    "asr_max_bytes",
)


async def load_overrides(session: AsyncSession) -> dict[str, Any]:
    """读出数据库里的覆盖值，忽略已经不在白名单里的历史遗留 key。"""
    rows = (await session.execute(select(AppSetting))).scalars()
    return {row.key: row.value for row in rows if row.key in WRITABLE_BY_KEY}


async def resolve_settings(
    session: AsyncSession, base: Settings | None = None
) -> Settings:
    """把数据库覆盖叠加到代码/环境基础配置之上，返回一个新的 Settings。

    ``model_copy`` 而不是就地改 —— base 是 lru_cache 出来的全局单例，
    改它会污染所有请求。
    """
    base = base or get_settings()
    overrides = await load_overrides(session)
    return base.model_copy(update=overrides) if overrides else base


def validate(key: str, value: Any, settings: Settings) -> Any:
    """校验单个配置项。不合法时抛 SettingError，消息直接给用户看。"""
    field = WRITABLE_BY_KEY.get(key)
    if field is None:
        raise SettingError(f"{key} 不可通过接口修改")

    if field.kind == "bool":
        if not isinstance(value, bool):
            raise SettingError(f"{field.label}必须是 true 或 false")
        return value

    if field.kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingError(f"{field.label}必须是整数")
        if field.minimum is not None and value < field.minimum:
            raise SettingError(f"{field.label}不能小于 {field.minimum}")
        if field.maximum is not None and value > field.maximum:
            raise SettingError(f"{field.label}不能大于 {field.maximum}")
        return value

    if not isinstance(value, str):
        raise SettingError(f"{field.label}必须是字符串")
    value = value.strip()

    if field.kind == "enum":
        if value not in field.choices:
            raise SettingError(
                f"{field.label}只能是 {'、'.join(field.choices)} 之一"
            )
        if key == "provider" and not _has_key(value, settings):
            # 切到没配 key 的 provider，之后每条消息都会 401，
            # 而且设置页自己也会显示错误状态 —— 直接拦在这里。
            raise SettingError(f"未配置 {value.upper()}_API_KEY，无法切换到该 provider")
        return value

    if key == "notify_channels":
        # 打错一个通道名就等于「这个通道静默不工作」，而通知的失败是看不见的 ——
        # 没收到提醒和没有提醒长得一模一样。拦在写入时。
        unknown = {
            name.strip() for name in value.split(",") if name.strip()
        } - set(KNOWN_CHANNELS)
        if unknown:
            raise SettingError(
                f"未知的通知通道：{'、'.join(sorted(unknown))}。"
                f"目前支持 {'、'.join(KNOWN_CHANNELS)}"
            )

    if not value and not field.allow_empty:
        raise SettingError(f"{field.label}不能为空")
    if field.minimum is not None and len(value) < field.minimum:
        raise SettingError(f"{field.label}至少 {field.minimum} 个字符")
    if field.maximum is not None and len(value) > field.maximum:
        raise SettingError(f"{field.label}最多 {field.maximum} 个字符")
    return value


async def apply(
    session: AsyncSession, updates: dict[str, Any], settings: Settings
) -> None:
    """写入覆盖值。``None`` 表示删掉该覆盖，恢复代码/环境基础默认。"""
    for key, value in updates.items():
        if key not in WRITABLE_BY_KEY:
            raise SettingError(f"{key} 不可通过接口修改")

        if value is None:
            await session.execute(sa_delete(AppSetting).where(AppSetting.key == key))
            continue

        checked = validate(key, value, settings)
        existing = await session.get(AppSetting, key)
        if existing is None:
            session.add(AppSetting(key=key, value=checked))
        else:
            existing.value = checked


def describe(settings: Settings, overrides: dict[str, Any]) -> dict[str, Any]:
    """给设置页的完整描述：当前值 + 每项来自哪层 + 可选项。

    带上 ``sources`` 才能在界面上标出「这项我改过」并提供「恢复默认」；
    带上 ``options`` 前端就不用硬编码模型清单和能力判断。
    """
    return {
        "values": {f.key: getattr(settings, f.key) for f in WRITABLE},
        # .env 不再是运行时配置的主要入口；兼容旧部署的显式环境值仍准确标记为环境覆盖。
        "sources": {
            f.key: (
                "db"
                if f.key in overrides
                else "env"
                if f.key in settings.model_fields_set
                else "default"
            )
            for f in WRITABLE
        },
        "fields": [
            {
                "key": f.key,
                "label": f.label,
                "kind": f.kind,
                "choices": list(f.choices),
                "minimum": f.minimum,
                "maximum": f.maximum,
                "provider": f.provider,
                "group": f.group,
            }
            for f in WRITABLE
        ],
        "providers": [
            {
                "value": name,
                "available": _has_key(name, settings),
                "reason": "" if _has_key(name, settings)
                else f"未配置 {name.upper()}_API_KEY",
            }
            for name in ("deepseek", "anthropic")
        ],
        "env_only": list(ENV_ONLY),
        "env_status": env_status(settings),
    }


@dataclass(frozen=True)
class EnvField:
    """一个只能改 `.env` 的配置项，以及它能不能给人看。

    分类的唯一判据是**值本身敏不敏感**：
    密钥和数据库连接串只报「配没配」，其余直接显示当前值。
    原来这块只把变量名平铺出来，看不出哪些配了 —— 而「标题模型为什么没走硅基流动」
    这类问题，答案十有八九就是某个 key 没配。
    """

    key: str
    label: str
    # secret = 只报配没配；plain = 可以显示值
    kind: str = "plain"
    # 没配置时的后果。只在真有后果时写
    absent_note: str = ""


ENV_FIELDS: tuple[EnvField, ...] = (
    EnvField("anthropic_api_key", "Anthropic 密钥", "secret",
             "未配置：Anthropic 的模型不可用"),
    EnvField("deepseek_api_key", "DeepSeek 密钥", "secret",
             "未配置：DeepSeek 的模型不可用"),
    EnvField("siliconflow_api_key", "硅基流动密钥", "secret",
             "未配置：标题生成退回聊天模型，更慢更贵"),
    EnvField("zhipu_api_key", "智谱密钥", "secret",
             "未配置：仅在没有硅基流动时才会用到"),
    # ⚠️ 空值 = 所有 /api 请求完全不校验（见 security.require_api_key）。
    # 单机 localhost 无所谓，暴露到局域网之前必须配 —— roadmap 的暴露面 checklist 第一条。
    EnvField("api_key", "接口访问 Key", "secret",
             "未配置：所有 /api 请求不做校验，暴露到局域网前必须设置"),
    # 连接串里带密码，不能显示值
    EnvField("database_url", "数据库连接", "secret"),
    EnvField("deepseek_base_url", "DeepSeek 地址"),
    EnvField("siliconflow_base_url", "硅基流动地址"),
    EnvField("siliconflow_title_model", "标题模型"),
    EnvField("zhipu_base_url", "智谱地址"),
    EnvField("tts_base_url", "语音服务地址"),
    EnvField("tts_model_cache", "语音模型缓存"),
    EnvField("asr_max_bytes", "录音大小上限"),
    EnvField("cors_origins", "允许的来源"),
    EnvField("log_level", "日志级别"),
    EnvField("log_format", "日志格式"),
    EnvField("log_color", "日志上色"),
    EnvField("log_access", "访问日志"),
)


def is_configured(value: object) -> bool:
    """算不算配了。

    `.env.example` 里的占位符（`sk-...`）不算 —— 照抄模板却没填的人最多，
    把它判成「已配置」会让人对着一个 401 找半天。
    """
    text = str(value or "").strip()
    return bool(text) and not text.endswith("...")


def env_status(settings: Settings) -> list[dict[str, Any]]:
    """只能改 `.env` 的那些项，现在是什么状态。

    **密钥只报布尔值，值绝不出现在响应里。**
    """
    rows: list[dict[str, Any]] = []
    for field in ENV_FIELDS:
        value = getattr(settings, field.key, "")
        configured = is_configured(value)
        rows.append({
            "key": field.key,
            "env": field.key.upper(),
            "label": field.label,
            "kind": field.kind,
            "configured": configured,
            # secret 一律给空串；plain 才回显值
            "value": "" if field.kind == "secret" else str(value),
            "note": "" if configured else field.absent_note,
        })
    return rows


def _has_key(provider: str, settings: Settings) -> bool:
    return is_configured(
        settings.anthropic_api_key
        if provider == "anthropic"
        else settings.deepseek_api_key
    )
