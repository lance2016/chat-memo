"""`image_ask` 工具：带着一个具体问题回去看那张图。

**这是补充路径，不是主路径。** 默认情况下图早就被转成描述放进上下文了
（见 hydrate.py），模型每轮都看得见。这个工具解决的是描述的固有缺陷：
描述是**盲写**的 —— 生成时不知道用户会问什么，所以问到没写进去的细节
（某个按钮的颜色、边角上的一个小图标）时，模型手里没有依据，而它并不知道
自己缺信息，于是会编。

有了这个工具，模型可以带着「用户问的到底是什么」回去重看。
**上下文一并带过去**是关键：用户说「第三行那个数字」，视觉模型必须知道
这句话才可能答对 —— 单看一个孤零零的问句它不知道「第三行」指什么。

⚠️ 聊天模型本身能看图时，这个工具不会被注册（见 app/agent.py 的 TOOLKITS）——
那种情况下图就在上下文里，再调一次工具纯属浪费。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.attachments import store, vision
from app.attachments.errors import AttachmentError
from app.config import Settings
from app.llm.factory import get_provider
from app.llm.target import ModelTarget

logger = logging.getLogger(__name__)

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "attachment_id": {
            "type": "integer",
            "description": "要看的图片编号，取自对话里 [图片 #7 …] 这种标注中的数字",
        },
        "question": {
            "type": "string",
            "description": "具体要看什么。越具体越好，例如「右下角那个按钮上写的是什么」",
        },
        "context": {
            "type": "string",
            "description": "可选，用户原话里和这个问题相关的部分。指代（「第三行」「左边那个」）必须带上，否则看图的模型不知道指的是什么",
        },
    },
    "required": ["attachment_id", "question"],
}

_DESCRIPTION = (
    "带着一个具体问题重新查看对话里的某张图片。"
    "上下文里已有的图片描述不足以回答用户时用它 —— 描述是概括写的，"
    "细节（颜色、位置、边角上的小字）可能没写进去。"
    "**不要用它重复已经知道的信息**；也不要用它替代直接回答。"
)

IMAGE_TOOL_NAMES = frozenset({"image_ask"})

IMAGE_TOOLS_ANTHROPIC: list[dict[str, Any]] = [
    {"name": "image_ask", "description": _DESCRIPTION, "input_schema": _SCHEMA}
]
IMAGE_TOOLS_OPENAI: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "image_ask",
            "description": _DESCRIPTION,
            "parameters": _SCHEMA,
        },
    }
]


class ImageToolExecutor:
    """把 image_ask 派发到视觉模型。

    失败一律转成 ``is_error`` 的文本回给模型，不抛异常 —— 同 kb / memory 的纪律。
    """

    names = IMAGE_TOOL_NAMES

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        # 可空只为了「人看的工具目录」：那里只取 schema，不会执行，
        # 也没有某一轮的 target 可给。真执行时为空是配置漏了，如实报错。
        vision_target: ModelTarget | None,
        conversation_id: int | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.vision_target = vision_target
        self.conversation_id = conversation_id

    @property
    def anthropic_definitions(self) -> list[dict[str, Any]]:
        return IMAGE_TOOLS_ANTHROPIC

    @property
    def openai_definitions(self) -> list[dict[str, Any]]:
        return IMAGE_TOOLS_OPENAI

    async def execute(self, name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
        if name != "image_ask":
            return f"未知工具 {name!r}", True
        if self.vision_target is None:
            return "没有可用的视觉模型，无法看图", True

        raw_id = tool_input.get("attachment_id")
        if not isinstance(raw_id, int):
            return "attachment_id 必须是整数，取自对话里 [图片 #7 …] 标注中的数字", True
        question = tool_input.get("question")
        if not isinstance(question, str) or not question.strip():
            return "question 不能为空，要写清楚具体想看什么", True

        try:
            row = await store.get_row(self.session, raw_id)
            # 会话隔离：模型只能看这轮对话里的图，不能拿 id 去翻别的会话。
            # id 是连续整数，猜得到 —— 不挡这一下等于给了个越权读取的口子。
            if (
                self.conversation_id is not None
                and row.conversation_id is not None
                and row.conversation_id != self.conversation_id
            ):
                return f"附件 #{raw_id} 不属于当前对话", True
            data = store.read_blob(self.settings, row.sha256)
        except AttachmentError as exc:
            return str(exc), True

        try:
            provider = get_provider(self.settings, target=self.vision_target)
            answer = await vision.ask(
                provider,
                mime=row.mime,
                data=data,
                question=question.strip(),
                context=str(tool_input.get("context") or ""),
            )
        except Exception as exc:
            logger.exception("image_ask 执行失败")
            return f"看图失败：{exc}", True

        if not answer:
            return "视觉模型没有返回内容，可以换个更具体的问法", True
        logger.info("👁 image_ask #%s：%s", raw_id, question.strip()[:40])
        return answer, False
