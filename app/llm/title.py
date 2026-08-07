"""标题生成：优先走硅基流动的免费 Qwen3-8B，并明确关掉推理。

标题是「一句话概括用户想干什么」，不需要推理。但主 provider 的 `complete()`
默认开着思考（DeepSeek 是不传就默认开，Anthropic 更是写死了 adaptive），
实测为一个 16 字的标题烧掉 127~542 个思考 token、2.4~21.5 秒 —— 而标题质量
并没有更好。更糟的是标题在 `done` 之前被 await，这段耗时会原样变成用户的
等待：正文早说完了，流还开着、会话锁还占着、输入框还是禁用的。

所以标题单独走一条 OpenAI 兼容链路。优先使用硅基流动 Qwen3-8B，同时继续
兼容旧的智谱 GLM Flash 配置；都没配时调用方退回聊天 provider。
"""

from __future__ import annotations

from openai import AsyncOpenAI

from app.config import Settings, get_settings


class TitleClient:
    def __init__(
        self, settings: Settings | None = None, client: AsyncOpenAI | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self.backend = (
            "siliconflow" if self.settings.siliconflow_api_key else "zhipu"
        )
        self.model = (
            self.settings.siliconflow_title_model
            if self.backend == "siliconflow"
            else self.settings.title_model
        )
        self.client = client or AsyncOpenAI(
            api_key=(
                self.settings.siliconflow_api_key
                if self.backend == "siliconflow"
                else self.settings.zhipu_api_key
            ),
            base_url=(
                self.settings.siliconflow_base_url
                if self.backend == "siliconflow"
                else self.settings.zhipu_base_url
            ),
        )

    @property
    def route(self) -> str:
        return f"{self.backend}/{self.model}"

    async def complete(self, *, system: str, prompt: str, max_tokens: int) -> str:
        response = await self.client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            extra_body=(
                {"enable_thinking": False}
                if self.backend == "siliconflow"
                else {"thinking": {"type": "disabled"}}
            ),
        )
        return (response.choices[0].message.content or "").strip()


def get_title_client(settings: Settings | None = None) -> TitleClient | None:
    """没配 key（或没配模型）就返回 None —— 调用方退回聊天 provider。"""
    settings = settings or get_settings()
    siliconflow_ready = bool(
        settings.siliconflow_api_key and settings.siliconflow_title_model
    )
    zhipu_ready = bool(settings.zhipu_api_key and settings.title_model)
    if not siliconflow_ready and not zhipu_ready:
        return None
    return TitleClient(settings)
