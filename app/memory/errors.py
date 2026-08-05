class MemoryToolError(Exception):
    """记忆操作失败。

    消息会作为 ``is_error`` 的 tool_result 回给模型，所以文本要写得能让模型自己纠正。
    """


class InvalidMemoryPath(MemoryToolError):
    pass


class MemoryNotFound(MemoryToolError):
    pass
