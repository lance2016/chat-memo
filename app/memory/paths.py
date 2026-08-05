import posixpath

from app.memory.errors import InvalidMemoryPath

MEMORY_ROOT = "/memories"
INDEX_PATH = f"{MEMORY_ROOT}/MEMORY.md"

MAX_PATH_LENGTH = 512
MAX_DEPTH = 8


def validate_path(raw: object) -> str:
    """把模型给的 path 规范化，并确保它落在 /memories 内。

    path 是模型输出，即使记忆不落真实磁盘也必须校验 —— 一旦将来换成文件系统后端，
    没有校验就是目录穿越。
    """
    if not isinstance(raw, str):
        raise InvalidMemoryPath(f"path 必须是字符串，收到 {type(raw).__name__}")

    path = raw.strip()
    if not path:
        raise InvalidMemoryPath("path 不能为空")
    if len(path) > MAX_PATH_LENGTH:
        raise InvalidMemoryPath(f"path 超过 {MAX_PATH_LENGTH} 字符")
    if "\x00" in path:
        raise InvalidMemoryPath("path 不能包含空字节")
    if "\\" in path:
        raise InvalidMemoryPath("path 只能用 / 作为分隔符")
    if not path.startswith("/"):
        raise InvalidMemoryPath(f"path 必须是绝对路径，以 {MEMORY_ROOT}/ 开头")

    # normpath 会把 ".." 消解掉：/memories/../etc → /etc，随后被前缀检查拒绝。
    normalized = posixpath.normpath(path)
    if normalized != MEMORY_ROOT and not normalized.startswith(MEMORY_ROOT + "/"):
        raise InvalidMemoryPath(f"path 必须位于 {MEMORY_ROOT}/ 之下，收到 {raw!r}")

    depth = normalized.count("/")
    if depth > MAX_DEPTH:
        raise InvalidMemoryPath(f"path 层级过深（最多 {MAX_DEPTH} 层）")

    return normalized
