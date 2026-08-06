"""标题生成：走智谱的免费 Flash 模型，并且明确关掉推理。

标题是「一句话概括用户想干什么」，不需要推理。但主 provider 的 `complete()`
默认开着思考（DeepSeek 是不传就默认开，Anthropic 更是写死了 adaptive），
实测为一个 16 字的标题烧掉 127~542 个思考 token、2.4~21.5 秒 —— 而标题质量
并没有更好。更糟的是标题在 `done` 之前被 await，这段耗时会原样变成用户的
等待：正文早说完了，流还开着、会话锁还占着、输入框还是禁用的。

所以标题单独走一条路：智谱开放平台上的 GLM Flash（免费档就够用）+ 关闭推理。
没配 `ZHIPU_API_KEY` 时 `get_title_client()` 返回 None，调用方退回聊天
provider，那条路同样会关掉思考。
"""

from __future__ import annotations

from openai import AsyncOpenAI

from app.config import Settings, get_settings


class TitleClient:
    def __init__(
        self, settings: Settings | None = None, client: AsyncOpenAI | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client or AsyncOpenAI(
            api_key=self.settings.zhipu_api_key,
            base_url=self.settings.zhipu_base_url,
        )

    async def complete(self, *, system: str, prompt: str, max_tokens: int) -> str:
        response = await self.client.chat.completions.create(
            model=self.settings.title_model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            # 智谱的推理开关不是 OpenAI 标准字段，得走 extra_body 透传，而且 GLM
            # 默认是开着的 —— 不显式关掉，标题就会白白等一段思考。
            extra_body={"thinking": {"type": "disabled"}},
        )
        return (response.choices[0].message.content or "").strip()


def get_title_client(settings: Settings | None = None) -> TitleClient | None:
    """没配 key（或没配模型）就返回 None —— 调用方退回聊天 provider。"""
    settings = settings or get_settings()
    if not settings.zhipu_api_key or not settings.title_model:
        return None
    return TitleClient(settings)
