"""附件在磁盘上的位置。

和 `app/skills/paths.py`、`app/kb/paths.py` 防的是同一类事故（路径穿越 + 符号链接逃逸），
但这里的攻击面**故意被设计掉了**：磁盘路径完全由 sha256 摘要推出，
**用户给的文件名一个字符都不参与路径构造**。文件名只作为一列数据存在数据库里，
渲染时给人看。所以这个模块不需要 `normalize_rel_path` 那一整套。

仍然留着校验的两个理由：

1. 摘要不总是来自本地计算 —— 读取时它来自数据库行，而数据库不是可信输入
   （恢复了一份被改过的备份、手工执行过 UPDATE）。形状校验挡住的正是
   `../../etc/passwd` 被写进 `sha256` 列这种情形。
2. 目录本身是可写挂载，里面可能被手工放进符号链接。realpath 遏制是第二道闸。
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from app.attachments.errors import InvalidAttachmentPath

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
# 一个目录下堆几万个文件在多数文件系统上都不好过，按前两位分桶摊开。
# 和 git 的 objects 目录同一个套路。
SHARD_LENGTH = 2


def normalize_digest(raw: object) -> str:
    """摘要必须是 64 位小写十六进制。纯字符串操作，不碰文件系统。"""
    if not isinstance(raw, str):
        raise InvalidAttachmentPath(f"摘要必须是字符串，收到 {type(raw).__name__}")
    digest = raw.strip().lower()
    if not SHA256_PATTERN.fullmatch(digest):
        raise InvalidAttachmentPath(f"摘要 {raw!r} 不是合法的 sha256")
    return digest


def blob_path(root: str | Path, digest: str) -> Path:
    """摘要 → 真实文件路径，并做 realpath 遏制。

    `root` 不存在时不报错：上传那条路径会自己建目录，而读取路径上的
    「文件不存在」由调用方按业务语义处理（404），不该在这里变成路径错误。
    """
    digest = normalize_digest(digest)
    base = Path(root).resolve()
    target = (base / digest[:SHARD_LENGTH] / digest).resolve()
    if not target.is_relative_to(base):
        raise InvalidAttachmentPath("附件路径经符号链接解析后越出了附件目录")
    return target


def safe_filename(raw: object, fallback: str = "image") -> str:
    """文件名只用来显示和下载，不进路径 —— 但仍要挡住能伤到别处的字符。

    去掉目录分隔符和控制字符：这个值会出现在给模型看的文本里，也会出现在
    `Content-Disposition` 头上，两处都不该被换行或引号截断。
    """
    if not isinstance(raw, str):
        return fallback
    name = raw.strip().replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '"\\')
    name = name.strip().strip(".")
    return name[:120] or fallback


def content_disposition(filename: str, fallback: str = "attachment") -> str:
    """给下载响应组一个 `Content-Disposition`。

    ⚠️ **HTTP 头只能是 latin-1**，而附件名经常是中文（「截图 2026年7月19日.png」）。
    直接把它塞进 `filename="..."` 的后果不是乱码，是 starlette 在
    `init_headers` 里抛 `UnicodeEncodeError` —— 整个下载接口 500，
    表现为界面上那张图变成一个警告图标，而日志里是一条看不出和文件名有关的栈。

    所以按 RFC 6266 给两份：ASCII 那份保证任何客户端都能解析，
    `filename*` 那份带真正的名字，现代浏览器优先用它。
    """
    name = safe_filename(filename, fallback)
    # 非 ASCII 字符换成 `_`：这一份只是兜底，可读性交给 filename*
    ascii_name = name.encode("ascii", "replace").decode("ascii").replace("?", "_")
    quoted = quote(name, safe="")
    return f'inline; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'
