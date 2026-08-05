import pytest

from app.memory.errors import InvalidMemoryPath
from app.memory.paths import validate_path


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/memories/a.md", "/memories/a.md"),
        ("  /memories/a.md  ", "/memories/a.md"),
        ("/memories/profile/./identity.md", "/memories/profile/identity.md"),
        ("/memories//profile//x.md", "/memories/profile/x.md"),
        ("/memories/profile/sub/../identity.md", "/memories/profile/identity.md"),
        ("/memories/", "/memories"),
    ],
)
def test_accepts_and_normalizes(raw: str, expected: str) -> None:
    assert validate_path(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "/memories/../etc/passwd",
        "/memories/../../etc/passwd",
        "/memories/a/../../secrets.md",
        "/etc/passwd",
        "/memoriesX/a.md",  # 前缀相似但不是同一目录
        "memories/a.md",  # 非绝对路径
        "../a.md",
        "",
        "   ",
        "/memories/a\\b.md",  # 反斜杠分隔符
        "/memories/a\x00.md",  # 空字节
        "/memories/" + "x/" * 10 + "deep.md",  # 层级过深
        "/memories/" + "a" * 600 + ".md",  # 过长
    ],
)
def test_rejects_escapes_and_junk(raw: str) -> None:
    with pytest.raises(InvalidMemoryPath):
        validate_path(raw)


@pytest.mark.parametrize("raw", [None, 123, ["/memories/a.md"], {"path": "x"}])
def test_rejects_non_string(raw: object) -> None:
    with pytest.raises(InvalidMemoryPath):
        validate_path(raw)
