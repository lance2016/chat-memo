"""标题生成：优先走硅基流动的免费 Qwen3-8B，并明确关掉推理。

标题是「一句话概括用户想干什么」，不需要推理。但主 provider 的 `complete()`
默认开着思考（DeepSeek 会显式启用，Anthropic 使用 adaptive），
实测为一个 16 字的标题烧掉 127~542 个思考 token、2.4~21.5 秒 —— 而标题质量
并没有更好。标题虽然和正文并行，若能在正文前完成就可以随当前流推给前端；
慢请求会转到后台。关掉推理既能让标题通常及时出现在当前流里，也能减少后台
任务占用和免费接口的 token 消耗。

所以标题默认单独走一条 OpenAI 兼容链路。模型目录指定标题档案后，标题会复用该档案的
协议和模型；未指定时优先使用硅基流动 Qwen3-8B，同时继续兼容旧的智谱 GLM Flash
配置；都没配时调用方退回聊天 provider。
"""

from __future__ import annotations

from openai import AsyncOpenAI

from app.config import Settings, get_settings
from app.llm.provider import LLMProvider
from app.llm.target import ModelTarget
from app.obs import record_llm_input, record_llm_output, trace


class TitleClient:
    def __init__(
        self,
        settings: Settings | None = None,
        client: AsyncOpenAI | None = None,
        provider: LLMProvider | None = None,
        target: ModelTarget | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.provider = provider
        self.target = target
        if self.provider is not None and self.target is not None:
            self.backend = self.target.service_slug or self.target.protocol
            self.model = self.target.model_id
            self.client = None
            return
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
        if self.provider is not None:
            return await self.provider.complete(
                system=system,
                prompt=prompt,
                max_tokens=max_tokens,
                thinking=False,
            )
        request = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "extra_body": (
                {"enable_thinking": False}
                if self.backend == "siliconflow"
                else {"thinking": {"type": "disabled"}}
            ),
        }
        with trace(
            "llm",
            f"openai/{self.model}",
            provider=self.backend,
            model=self.model,
            purpose="title",
        ):
            record_llm_input(request, model=self.model)
            response = await self.client.chat.completions.create(**request)
            choice = response.choices[0]
            response_usage = getattr(response, "usage", None)
            usage = (
                response_usage.model_dump(exclude_none=True)
                if response_usage is not None
                else {}
            )
            record_llm_output(
                {
                    "content": choice.message.content or "",
                    "finish_reason": choice.finish_reason,
                },
                usage=usage,
                stop_reason=choice.finish_reason or "",
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
