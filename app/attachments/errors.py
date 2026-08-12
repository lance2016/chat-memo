from __future__ import annotations


class AttachmentError(Exception):
    """附件层的可预期失败。消息直接给用户或模型看，所以要写人话。"""


class AttachmentNotFound(AttachmentError):
    pass


class InvalidAttachment(AttachmentError):
    """类型不在白名单、超过大小上限，或内容根本不是一张图。"""


class InvalidAttachmentPath(AttachmentError):
    """摘要形状不合法，或落到目录外面去了。"""
