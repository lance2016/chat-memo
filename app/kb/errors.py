class KbToolError(Exception):
    """kb 工具执行失败。转成 is_error 的结果回给模型，不中断对话。"""


class InvalidKbPath(KbToolError):
    """路径不合法或越出 vault 范围。"""
