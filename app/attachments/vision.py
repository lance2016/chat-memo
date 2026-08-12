"""把一张图交给视觉模型，换回一段可以塞进对话的文字。

**这是给看不了图的聊天模型准备的兜底**，不是主要功能。判据是
`ModelTarget.supports_vision` —— 聊天模型自己能看图时，这个模块一次都不会被调用。

描述的质量决定了整条兜底路径的上限，所以提示词有两个硬要求：

1. **原文照抄，不要转述**。截图里的报错信息、代码、数字，用户后面十有八九要追问，
   转述过一遍就再也对不上了。
2. **说不确定就说不确定**。这段文字之后会被聊天模型当成事实使用，而它没有图可以复核。
   描述里一句含糊的猜测，到了下游就是一句笃定的错话。
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from app.llm.provider import LLMProvider

logger = logging.getLogger(__name__)

DESCRIBE_SYSTEM = """你是图像转文字的中间环节。你的输出会原样交给另一个看不见这张图的助手，
它要靠你的描述回答用户的问题。

要求：
1. 先用一两句说清这是什么（截图 / 照片 / 图表 / 手写…），以及整体在讲什么。
2. **把图中所有文字原样抄出来**，保持行序和层级。报错信息、代码、数字、单位一个字都不要改写，
   也不要翻译。表格按行列抄，不要压成一段话。
3. 有布局信息就交代清楚（第几行、左边还是右边、哪个是高亮的），下游助手要靠这些理解「第三行那个」。
4. 看不清或拿不准的地方，明确写「看不清」，不要猜。你的猜测到了下游会变成断言。
5. 只输出描述本身，不要开场白，不要问问题，不要提出建议。"""

DESCRIBE_PROMPT = "描述这张图片。"

# 描述会进每一轮的上下文，得有上限。1500 字够抄下一整屏报错了；
# 再长的图，靠 image_ask 带着具体问题回去看比堆描述有效。
DESCRIBE_MAX_TOKENS = 2000


def image_block(mime: str, data: bytes) -> dict[str, Any]:
    """内部标准格式的图片块（就是 Anthropic 那一种）。

    各协议的差异在 provider 里翻译，这里只产出标准格式 —— 和消息落库用的是同一种块，
    所以 hydrate 出来的东西可以直接进 messages。
    """
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": mime or "image/png",
            "data": base64.b64encode(data).decode("ascii"),
        },
    }


async def describe(
    provider: LLMProvider, *, mime: str, data: bytes, context: str = ""
) -> str:
    """让视觉模型写一段描述。失败返回空串，由调用方决定怎么退化。"""
    blocks: list[dict[str, Any]] = [image_block(mime, data)]
    prompt = DESCRIBE_PROMPT
    if context:
        prompt = f"{DESCRIBE_PROMPT}\n\n这张图出现在下面这段对话里，描述时注意相关的部分：\n{context}"
    blocks.append({"type": "text", "text": prompt})

    text = await provider.complete(
        system=DESCRIBE_SYSTEM,
        prompt=blocks,
        max_tokens=DESCRIBE_MAX_TOKENS,
        # 描述是「照抄 + 转述」，不是推理。开思考只是让用户多等。
        thinking=False,
    )
    return text.strip()


async def ask(
    provider: LLMProvider, *, mime: str, data: bytes, question: str, context: str = ""
) -> str:
    """带着一个具体问题重新看这张图。

    和 `describe` 的区别全在于**有问题可问** —— 这正是它相对预描述的全部价值：
    描述是盲写的，写的时候不知道用户会问什么。
    """
    blocks: list[dict[str, Any]] = [image_block(mime, data)]
    parts = [f"问题：{question}"]
    if context:
        parts.append(f"这张图出现在下面这段对话里：\n{context}")
    parts.append("只回答问题。图里没有依据就直说「图中看不出」，不要推测。")
    blocks.append({"type": "text", "text": "\n\n".join(parts)})

    text = await provider.complete(
        system=DESCRIBE_SYSTEM,
        prompt=blocks,
        max_tokens=DESCRIBE_MAX_TOKENS,
        thinking=False,
    )
    return text.strip()
