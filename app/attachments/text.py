"""认出 txt / md，并把字节解码成能直接进上下文的文本。

**为什么不能照抄 `image.py` 的那套嗅探**：文本没有文件头。
「这堆字节是不是文本」只能由内容本身回答 —— 能整块按 UTF-8 解出来、
且不含控制字符，才算。

所以这里是两个条件同时成立才放行，各管各的：

- **扩展名**决定 mime（`text/plain` 还是 `text/markdown`），也表明用户的意图。
  它只负责收窄范围，**不负责判定** —— 一个改名成 `.md` 的可执行文件
  不该因为名字就被当成文本发给模型。
- **内容**才是准入判据。二进制在 `decode` 这一步会失败。

⚠️ 解码出来的文本和磁盘上那份字节**不完全一样**（BOM 去掉了、CRLF 归一了）。
磁盘存的永远是原始上传，规范化只作用在发给模型的那一份 —— 和
`hydrate.py` 里「存的和发出去的故意不一致」是同一条规矩。
"""

from __future__ import annotations

from app.attachments.errors import InvalidAttachment

PLAIN = "text/plain"
MARKDOWN = "text/markdown"

SUPPORTED = (PLAIN, MARKDOWN)

_MARKDOWN_SUFFIXES = (".md", ".markdown")
_PLAIN_SUFFIXES = (".txt",)

# 大小写不敏感：Windows 上导出的 README.MD 是同一种东西
SUPPORTED_SUFFIXES = _MARKDOWN_SUFFIXES + _PLAIN_SUFFIXES

# 换行、回车、制表之外的 C0 控制字符。出现任何一个就说明这不是给人读的文本 ——
# UTF-8 解码本身拦不住它们（`\x00` 是合法的码位）。
_ALLOWED_CONTROL = frozenset("\n\r\t")
_CONTROL = frozenset(chr(code) for code in range(0x20)) - _ALLOWED_CONTROL


def suffix_of(filename: str) -> str:
    """取小写扩展名。没有点就返回空串。"""
    name = filename.strip().lower()
    dot = name.rfind(".")
    return name[dot:] if dot > 0 else ""


def looks_like_text(filename: str) -> bool:
    """按扩展名判断该走文本这条路。**不看内容** —— 内容由 `decode` 说了算。

    这是上传时的分流判据：走文本路就用文本的错误消息和文本的体积上限，
    而不是让用户收到一句「只支持 PNG / JPEG / GIF / WebP 图片」。
    """
    return suffix_of(filename) in SUPPORTED_SUFFIXES


def mime_for(filename: str) -> str:
    return MARKDOWN if suffix_of(filename) in _MARKDOWN_SUFFIXES else PLAIN


def decode(data: bytes, *, filename: str) -> tuple[str, str]:
    """返回 (mime, 规范化后的文本)。不是文本就拒绝。

    只接 UTF-8。GBK / UTF-16 会在这里被挡下来，理由是猜编码猜错的代价
    （一整篇乱码进上下文，模型照着乱码答）比让用户自己转一次大得多。
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidAttachment(
            f"{filename or '这个文件'} 不是 UTF-8 文本（第 {exc.start} 字节解不出来）"
        ) from exc

    # BOM 不去掉会变成正文的第一个字符，跟着进模型上下文
    text = text.lstrip("﻿")
    if _CONTROL.intersection(text):
        raise InvalidAttachment(
            f"{filename or '这个文件'} 里有控制字符，看起来不是纯文本"
        )
    if not text.strip():
        raise InvalidAttachment("文件内容为空")

    # CRLF 归一：留着的话字符预算要为每一行多算一个字符，模型侧也没有任何收益
    return mime_for(filename), text.replace("\r\n", "\n").replace("\r", "\n")
