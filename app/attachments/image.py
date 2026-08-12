"""从文件头认出图片类型和尺寸。

**为什么不装 Pillow**：这里要的只有「这堆字节是不是一张图、多大」，
而 Pillow 是几 MB 带原生扩展的解码库。真要解码用户上传的任意图片，
反而是把一个历史上出过不少 CVE 的解析器放进请求路径 —— 我们并不需要它。

**为什么必须嗅探而不能信 `content_type`**：那个头是浏览器（进而是用户）说了算的。
不校验的话，声称 `image/png` 的任意二进制会被原样 base64 发给模型，
在磁盘上也占着一个「图片」的位置。嗅探顺带拿到的宽高给前端排版用，是附带收益。
"""

from __future__ import annotations

from app.attachments.errors import InvalidAttachment

# 出去的 mime 只可能是这四个之一 —— 它会进 data URI 和 Anthropic 的
# `source.media_type`，两边都不接受随便什么字符串。
PNG = "image/png"
JPEG = "image/jpeg"
GIF = "image/gif"
WEBP = "image/webp"

SUPPORTED = (PNG, JPEG, GIF, WEBP)

# SOF0..SOF15 里真正带尺寸的那些。0xC4/0xC8/0xCC 混在这段区间里但不是 SOF。
_JPEG_SOF = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)


def sniff(data: bytes) -> tuple[str, int, int]:
    """返回 (mime, width, height)。认不出来就拒绝。

    尺寸解析失败但类型确认了的，宽高返回 0 而不是报错 —— 尺寸只影响前端排版，
    为它把一张能用的图挡在门外不划算。
    """
    if len(data) < 16:
        raise InvalidAttachment("文件太小，不是一张有效的图片")

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return (PNG, *_png_size(data))
    if data.startswith(b"\xff\xd8\xff"):
        return (JPEG, *_jpeg_size(data))
    if data.startswith((b"GIF87a", b"GIF89a")):
        return (GIF, *_gif_size(data))
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return (WEBP, *_webp_size(data))

    raise InvalidAttachment("只支持 PNG / JPEG / GIF / WebP 图片")


def _png_size(data: bytes) -> tuple[int, int]:
    # IHDR 必须是第一个块，宽高就在它开头
    if data[12:16] != b"IHDR":
        return (0, 0)
    return (
        int.from_bytes(data[16:20], "big"),
        int.from_bytes(data[20:24], "big"),
    )


def _jpeg_size(data: bytes) -> tuple[int, int]:
    i = 2
    end = len(data)
    while i + 9 < end:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        # 填充字节、SOI、RSTn 都没有长度字段
        if marker in (0xFF, 0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker == 0xD9 or marker == 0xDA:  # EOI / 进入压缩数据，后面没有 SOF 了
            break
        segment = int.from_bytes(data[i + 2 : i + 4], "big")
        if segment < 2:
            break
        if marker in _JPEG_SOF:
            return (
                int.from_bytes(data[i + 7 : i + 9], "big"),
                int.from_bytes(data[i + 5 : i + 7], "big"),
            )
        i += 2 + segment
    return (0, 0)


def _gif_size(data: bytes) -> tuple[int, int]:
    return (
        int.from_bytes(data[6:8], "little"),
        int.from_bytes(data[8:10], "little"),
    )


def _webp_size(data: bytes) -> tuple[int, int]:
    """WebP 有三种块格式，尺寸各存各的。认不出来就返回 0。"""
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        # 画布尺寸是 24 位小端，且存的是「减一」
        return (
            int.from_bytes(data[24:27], "little") + 1,
            int.from_bytes(data[27:30], "little") + 1,
        )
    if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        return (
            int.from_bytes(data[26:28], "little") & 0x3FFF,
            int.from_bytes(data[28:30], "little") & 0x3FFF,
        )
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    return (0, 0)
