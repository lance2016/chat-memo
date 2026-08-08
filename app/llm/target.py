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
ProtocolName = Literal["anthropic", "openai_compatible"]

DEFAULT_CAPABILITIES: dict[str, bool] = {
    "streaming": True,
    "tool_calling": True,
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
    # 该模型默认要不要思考。单次请求仍可覆盖
    thinking_default: bool = False
    # Anthropic 的推理强度；其他协议留空表示不传
    effort: str = ""

    def __post_init__(self) -> None:
        if self.capabilities is None:
            object.__setattr__(self, "capabilities", dict(DEFAULT_CAPABILITIES))

    @property
    def supports_tools(self) -> bool:
        return bool(self.capabilities.get("tool_calling", False))

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
                capabilities={**DEFAULT_CAPABILITIES, "thinking": True, "json_mode": True},
                max_tokens=settings.max_tokens,
                thinking_default=True,
                effort=settings.effort,
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
                    "thinking": settings.deepseek_thinking,
                },
                max_tokens=settings.deepseek_max_tokens,
                thinking_default=settings.deepseek_thinking,
            )
        return target.with_model(model_override)
