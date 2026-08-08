from app.config import Settings, get_settings
from app.llm.catalog import ModelTarget
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.deepseek_provider import DeepSeekProvider
from app.llm.provider import LLMProvider

_PROVIDERS = {"anthropic": AnthropicProvider, "deepseek": DeepSeekProvider}


def get_provider(
    settings: Settings | None = None,
    model_override: str = "",
    target: ModelTarget | None = None,
) -> LLMProvider:
    settings = settings or get_settings()
    if target is not None:
        settings = target.apply(settings)
    if model_override:
        # 复制一份配置，别改全局单例 —— 它是 lru_cache 出来的共享对象
        field = "model" if settings.provider == "anthropic" else "deepseek_model"
        settings = settings.model_copy(update={field: model_override})
    try:
        provider_class = _PROVIDERS[settings.provider]
        if target is not None and target.protocol == "anthropic":
            return provider_class(settings=settings, base_url=target.base_url)
        return provider_class(settings=settings)
    except KeyError:
        raise ValueError(
            f"未知的 provider {settings.provider!r}，可选：{', '.join(_PROVIDERS)}"
        ) from None
