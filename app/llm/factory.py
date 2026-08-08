"""按协议挑 provider 实现。

**注册表的键是协议，不是厂商。** 这是加厂商成本的分水岭：

- 加一个 OpenAI 兼容的服务（硅基流动、OpenRouter、本地 vLLM）——
  在模型目录里加一行记录就行，**代码一行都不用改**
- 加一个新协议（比如 Gemini 原生）—— 写一个 provider 类，在下面注册一行，
  不用动 `Settings`，也不用动任何调用方
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.deepseek_provider import DeepSeekProvider
from app.llm.provider import LLMProvider
from app.llm.target import ModelTarget

_BY_PROTOCOL = {
    "anthropic": AnthropicProvider,
    # DeepSeek 的实现本来就是纯 OpenAI 兼容调用，所有走这个协议的服务共用它
    "openai_compatible": DeepSeekProvider,
}


def get_provider(
    settings: Settings | None = None,
    model_override: str = "",
    target: ModelTarget | None = None,
) -> LLMProvider:
    """造一个 provider。

    `target` 缺省时从 settings 推导 —— `ModelTarget.from_settings` 是整个代码库里
    唯一一处「anthropic 还是别的」分支。
    """
    settings = settings or get_settings()
    target = (target or ModelTarget.from_settings(settings)).with_model(model_override)
    try:
        implementation = _BY_PROTOCOL[target.protocol]
    except KeyError:
        raise ValueError(
            f"未知的模型协议 {target.protocol!r}，可选：{', '.join(_BY_PROTOCOL)}"
        ) from None
    return implementation(settings=settings, target=target)
