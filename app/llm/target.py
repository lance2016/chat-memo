"""一次模型调用的完整目标。

**这个模块是「调哪个模型」的唯一载体**，也是整个 llm 层唯一允许出现
「anthropic 还是别的」这种分支的地方（就在 `from_settings` 里）。

为什么单独一个模块、且不 import 任何 db 东西：provider 需要的只是
「地址、密钥、模型名、调用参数」，它不该知道模型目录存在、更不该拖进 SQLAlchemy。
`catalog.py` 负责从数据库把这些查出来组装成 `ModelTarget`，provider 只认这个 dataclass。
两边都不用知道对方。

职责边界（拆错了就会长回上帝对象）：

| 谁提供 | 内容 | 判据 |
|---|---|---|
| `ModelTarget` | 地址、密钥、模型 ID、max_tokens、思考默认、effort | **换个模型就会变的** |
| `Settings` | 工具轮次上限、是否记录请求快照 | 换模型也不变的全局行为 |
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from app.config import Settings

# 协议决定用哪个 provider 实现。加一个新协议 = 加一个实现 + 在 factory 注册，
# 不需要动 Settings，也不需要动任何调用方。
ProtocolName = Literal["anthropic", "openai_compatible", "openai_responses"]

# These are provider request values, not UI labels.  Keep the supported subset
# on the resolved target so request validation never has to guess from a model
# name.  Individual catalog profiles may narrow this list in ``options``.
THINKING_EFFORTS_BY_PROTOCOL: dict[str, tuple[str, ...]] = {
    "anthropic": ("low", "medium", "high", "xhigh", "max"),
    "openai_responses": ("low", "medium", "high", "xhigh", "max"),
    # Chat Completions-compatible services have several incompatible dialects.
    # They still get the boolean switch, but no depth UI unless a future native
    # protocol implementation can enforce it.
    "openai_compatible": (),
}

# DeepSeek Chat Completions is an intentional service-specific exception to the
# generic OpenAI-compatible rule above.  Its public API documents exactly these
# request values and defaults to ``high``.  Do not put them on the protocol-wide
# list: unrelated compatible services may reject ``reasoning_effort`` outright.
DEEPSEEK_THINKING_EFFORTS = ("low", "high", "max")


def thinking_efforts_for(protocol: str, service_slug: str = "") -> tuple[str, ...]:
    """Return effort values our concrete provider can safely send."""
    if protocol == "openai_compatible" and service_slug == "deepseek":
        return DEEPSEEK_THINKING_EFFORTS
    return THINKING_EFFORTS_BY_PROTOCOL.get(protocol, ())

DEFAULT_CAPABILITIES: dict[str, bool] = {
    "streaming": True,
    "tool_calling": True,
    "text_generation": True,
    "thinking": False,
    "vision": False,
    "json_mode": False,
}


@dataclass(frozen=True)
class ModelTarget:
    """一次请求最终解析出的模型目标。"""

    protocol: ProtocolName
    model_id: str
    display_name: str
    base_url: str = ""
    api_key: str = ""
    service_slug: str = ""
    service_name: str = ""
    profile_id: int | None = None
    capabilities: dict[str, bool] = None  # type: ignore[assignment]
    # ---- 调用参数：换模型就会变，所以跟着 target 走而不是留在 Settings ----
    max_tokens: int = 8192
    # 模型的输入 + 输出总上下文窗口。第三方兼容服务通常不会在接口中返回，
    # 未配置时保持 None，调用方不能把 max_tokens（输出上限）误当成上下文容量。
    context_window_tokens: int | None = None
    # 该模型默认要不要思考。单次请求仍可覆盖
    thinking_default: bool = False
    # Provider request value.  Empty means the concrete service has no safe,
    # standardized effort parameter.
    effort: str = ""
    # 当前模型真正接受的思考强度。空元组表示只支持开关、不支持调档。
    thinking_efforts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.capabilities is None:
            object.__setattr__(self, "capabilities", dict(DEFAULT_CAPABILITIES))

    @property
    def supports_tools(self) -> bool:
        return bool(self.capabilities.get("tool_calling", False))

    @property
    def supports_vision(self) -> bool:
        """能不能直接吃 image block。

        **带图那一轮的全部分支都问这一个属性**，不问厂商名。所以把聊天模型换成
        Claude 之后，原生视觉是自动生效的 —— 没有任何一处代码需要改。
        """
        return bool(self.capabilities.get("vision", False))

    @property
    def supports_thinking(self) -> bool:
        return bool(self.capabilities.get("thinking", False))

    def with_model(self, model_id: str) -> ModelTarget:
        """换一个模型 ID，其余（地址、密钥、参数）不变。

        给「同一个服务上临时换个便宜模型」用，比如标题生成和评测的 `--model`。
        """
        if not model_id or model_id == self.model_id:
            return self
        return replace(self, model_id=model_id, display_name=model_id, profile_id=None)

    @classmethod
    def from_settings(
        cls, settings: Settings, model_override: str = ""
    ) -> ModelTarget:
        """从旧的 `provider` + 厂商字段推出一个目标。

        **这是整个代码库里唯一一处 `provider == "anthropic"` 分支。**
        在模型目录（`catalog.py`）接管全部配置之前，它是老部署和测试的兼容入口；
        之后删掉它，其余代码一行都不用改 —— 这正是把分支收敛到一处的意义。
        """
        if settings.provider == "anthropic":
            target = cls(
                protocol="anthropic",
                model_id=settings.model,
                display_name=f"Claude · {settings.model}",
                api_key=settings.anthropic_api_key,
                service_slug="anthropic",
                service_name="Anthropic",
                capabilities={
                    **DEFAULT_CAPABILITIES,
                    "thinking": True,
                    "json_mode": True,
                    # 在售的 Claude 全系都能看图，没有需要用户自己勾的余地
                    "vision": True,
                },
                max_tokens=settings.max_tokens,
                thinking_default=True,
                effort=settings.effort,
                thinking_efforts=THINKING_EFFORTS_BY_PROTOCOL["anthropic"],
            )
        elif settings.provider in {"openai", "openai_responses"} or (
            # 仅当 provider 仍是代码默认值时，OPENAI_BASE_URL 才自动接管。
            # 用户在设置页/环境变量明确选回 deepseek 后，不能被这个临时环境变量
            # 悄悄覆盖。
            settings.provider == "deepseek"
            and settings.openai_base_url
            and "provider" not in settings.model_fields_set
        ):
            target = cls(
                protocol="openai_responses",
                model_id=settings.openai_model,
                display_name=f"OpenAI Responses · {settings.openai_model}",
                base_url=settings.openai_base_url,
                # OpenAI SDK 要求 key 非空；本地代理默认只使用它的占位值。
                api_key=settings.openai_api_key or "not-needed",
                service_slug="openai-codex",
                service_name="OpenAI via Codex",
                capabilities={
                    **DEFAULT_CAPABILITIES,
                    "thinking": True,
                    "vision": True,
                    "json_mode": True,
                },
                max_tokens=settings.openai_max_tokens,
                thinking_default=settings.openai_thinking,
                effort=settings.openai_effort,
                thinking_efforts=THINKING_EFFORTS_BY_PROTOCOL["openai_responses"],
            )
        else:
            target = cls(
                protocol="openai_compatible",
                model_id=settings.deepseek_model,
                display_name=f"DeepSeek · {settings.deepseek_model}",
                base_url=settings.deepseek_base_url,
                # 本地 OpenAI 兼容服务可能不需要鉴权，但 SDK 要求非空值
                api_key=settings.deepseek_api_key or "not-needed",
                service_slug="deepseek",
                service_name="DeepSeek",
                capabilities={
                    **DEFAULT_CAPABILITIES,
                    # ⚠️ 能力是「这个模型会不会思考」，**不是**「这次要不要思考」。
                    # 后者是下面的 `thinking_default`（以及单次请求的覆盖）。
                    # 这两件事一度共用 `settings.deepseek_thinking` 这一个值，
                    # 于是「用户默认关思考」被记成了「这个模型不会思考」，
                    # 而 provider 要靠能力判断「发不发那个关思考的方言参数」。
                    "thinking": True,
                },
                max_tokens=settings.deepseek_max_tokens,
                thinking_default=settings.deepseek_thinking,
                effort="high",
                thinking_efforts=DEEPSEEK_THINKING_EFFORTS,
            )
        return target.with_model(model_override)
