from __future__ import annotations

import logging
from typing import Any

from app.memory.errors import MemoryToolError
from app.memory.store import MemoryStore

logger = logging.getLogger(__name__)

# Anthropic 原生记忆工具：schema 内置于模型，只声明 type 和 name，不能自带 input_schema。
MEMORY_TOOL: dict[str, Any] = {"type": "memory_20250818", "name": "memory"}

MEMORY_TOOL_DESCRIPTION = """读写关于用户的长期记忆。记忆是 /memories 下的一棵文件树。

各 command 需要的参数：
- view(path[, view_range])：读文件内容或列目录。view_range 是 [起始行, 结束行]，-1 表示到末尾。
- create(path, file_text)：新建或整体覆盖一个文件。
- str_replace(path, old_str, new_str)：把 old_str 替换成 new_str。old_str 必须在文件中唯一出现。
- insert(path, insert_line, insert_text)：在第 insert_line 行之后插入，0 表示插到文件开头。
- delete(path)：删除文件或整个目录。
- rename(old_path, new_path)：重命名/移动文件或目录。

所有路径必须以 /memories/ 开头。修改前先 view，不要凭空猜测文件里已有什么。"""

# 给 OpenAI 兼容接口（DeepSeek 等）用的手写 schema。
# 六个 command 的参数不同，这里用扁平可选字段 + 描述说明，比 oneOf 兼容性好。
MEMORY_TOOL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "enum": ["view", "create", "str_replace", "insert", "delete", "rename"],
            "description": "要执行的操作",
        },
        "path": {
            "type": "string",
            "description": "目标路径，以 /memories/ 开头。rename 之外的命令都用它",
        },
        "file_text": {"type": "string", "description": "create：文件的完整内容"},
        "old_str": {"type": "string", "description": "str_replace：被替换的原文，须唯一"},
        "new_str": {"type": "string", "description": "str_replace：替换成的新文本"},
        "insert_line": {"type": "integer", "description": "insert：插入到第几行之后，0 为开头"},
        "insert_text": {"type": "string", "description": "insert：插入的文本"},
        "view_range": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "view：可选的 [起始行, 结束行]",
        },
        "old_path": {"type": "string", "description": "rename：原路径"},
        "new_path": {"type": "string", "description": "rename：新路径"},
    },
    "required": ["command"],
}

MEMORY_FUNCTION_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "memory",
        "description": MEMORY_TOOL_DESCRIPTION,
        "parameters": MEMORY_TOOL_PARAMETERS,
    },
}


class MemoryToolExecutor:
    """把模型发出的 memory 工具调用派发到 MemoryStore。

    所有失败都转成 ``is_error`` 的结果文本回给模型，让它自己纠正，而不是中断这轮对话。
    """

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    @property
    def anthropic_definitions(self) -> list[dict[str, Any]]:
        """Claude 用原生记忆工具 —— 模型对它训练过，比手写 schema 表现更好。"""
        return [MEMORY_TOOL]

    @property
    def openai_definitions(self) -> list[dict[str, Any]]:
        """OpenAI 兼容接口没有原生记忆工具，用手写的 function schema。"""
        return [MEMORY_FUNCTION_TOOL]

    async def execute(self, name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
        if name != "memory":
            return f"未知工具 {name!r}", True

        command = tool_input.get("command")
        try:
            match command:
                case "view":
                    return (
                        await self.store.view(
                            _req(tool_input, "path"), tool_input.get("view_range")
                        ),
                        False,
                    )
                case "create":
                    return (
                        await self.store.create(
                            _req(tool_input, "path"), _req(tool_input, "file_text")
                        ),
                        False,
                    )
                case "str_replace":
                    return (
                        await self.store.str_replace(
                            _req(tool_input, "path"),
                            _req(tool_input, "old_str"),
                            tool_input.get("new_str", ""),
                        ),
                        False,
                    )
                case "insert":
                    return (
                        await self.store.insert(
                            _req(tool_input, "path"),
                            _int(tool_input, "insert_line"),
                            _req(tool_input, "insert_text"),
                        ),
                        False,
                    )
                case "delete":
                    return await self.store.delete(_req(tool_input, "path")), False
                case "rename":
                    return (
                        await self.store.rename(
                            _req(tool_input, "old_path"), _req(tool_input, "new_path")
                        ),
                        False,
                    )
                case _:
                    return f"不支持的 command {command!r}", True
        except MemoryToolError as exc:
            return str(exc), True
        except Exception as exc:
            logger.exception("记忆工具执行失败: command=%s", command)
            return f"记忆操作内部错误：{exc}", True


def _req(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise MemoryToolError(f"缺少必填参数 {key}")
    return payload[key]


def _int(payload: dict[str, Any], key: str) -> int:
    value = _req(payload, key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise MemoryToolError(f"{key} 必须是整数，收到 {value!r}")
    return value
