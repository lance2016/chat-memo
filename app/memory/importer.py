"""把常见的外部记忆导出整理成记忆文件。

导入故意是保守的：所有文件都落在 ``/memories/imports`` 下，默认不覆盖已有文件，
这样导入不同平台的数据时不会悄悄改坏现有记忆。更复杂的平台导出通常也能先以
JSON/JSONL 导入，再在记忆页里人工整理成自己的目录结构。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from app.memory.paths import MEMORY_ROOT, validate_path
from app.memory.store import MAX_CONTENT_BYTES

MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_IMPORT_ITEMS = 200
IMPORT_ROOT = f"{MEMORY_ROOT}/imports"

_CONTENT_KEYS = ("content", "text", "memory", "note", "body", "value", "description")
_TITLE_KEYS = ("title", "name", "subject", "topic", "label")
_PATH_KEYS = ("path", "file", "filename", "file_name")


class MemoryImportError(ValueError):
    """导入文件格式或内容不符合预期。"""


@dataclass(frozen=True)
class ImportEntry:
    title: str
    content: str
    source_path: str = ""


@dataclass(frozen=True)
class PreparedImport:
    format: str
    entries: tuple[ImportEntry, ...]
    warnings: tuple[str, ...] = ()


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise MemoryImportError("文件不是可读取的 UTF-8、UTF-16 或 GB18030 文本")


def _clean_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value).strip()


def _first_text(item: dict[str, Any]) -> str:
    for key in _CONTENT_KEYS:
        if key in item:
            text = _clean_text(item[key])
            if text:
                return text
    # 不认识字段结构时不丢数据，保留原始 JSON，用户可以在记忆页继续整理。
    return json.dumps(item, ensure_ascii=False, indent=2)


def _first_value(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _json_items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return [value]

    for key in ("memories", "items", "entries", "notes", "records", "data"):
        nested = value.get(key)
        if isinstance(nested, list):
            return nested

    # 很多导出是 {"偏好": "...", "工作": "..."} 这种映射。
    if value and all(isinstance(item, str) for item in value.values()):
        return [{"title": key, "content": item} for key, item in value.items()]
    return [value]


def _parse_json(text: str, jsonl: bool) -> tuple[list[ImportEntry], list[str]]:
    raw_items: list[Any]
    if jsonl:
        raw_items = []
        for line_no, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                raw_items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise MemoryImportError(f"JSONL 第 {line_no} 行无法解析：{exc.msg}") from exc
    else:
        try:
            raw_items = _json_items(json.loads(text))
        except json.JSONDecodeError as exc:
            raise MemoryImportError(f"JSON 无法解析：{exc.msg}") from exc

    if not raw_items:
        raise MemoryImportError("导出文件里没有可导入的内容")
    if len(raw_items) > MAX_IMPORT_ITEMS:
        raise MemoryImportError(f"一次最多导入 {MAX_IMPORT_ITEMS} 条记忆，请拆分文件")

    entries: list[ImportEntry] = []
    skipped = 0
    for index, raw in enumerate(raw_items, 1):
        if isinstance(raw, dict):
            content = _first_text(raw)
            title = _first_value(raw, _TITLE_KEYS)
            source_path = _first_value(raw, _PATH_KEYS)
        else:
            content = _clean_text(raw)
            title = ""
            source_path = ""
        if not content:
            skipped += 1
            continue
        entries.append(ImportEntry(title=title or f"记忆 {index}", content=content, source_path=source_path))

    warnings = [f"已跳过 {skipped} 条空记录"] if skipped else []
    if not entries:
        raise MemoryImportError("导出文件里没有非空记忆")
    return entries, warnings


def prepare_import(filename: str, data: bytes) -> PreparedImport:
    if len(data) > MAX_IMPORT_BYTES:
        raise MemoryImportError(f"导入文件不能超过 {MAX_IMPORT_BYTES // (1024 * 1024)} MB")
    text = _decode(data).strip()
    if not text:
        raise MemoryImportError("导入文件为空")

    suffix = PurePosixPath(filename or "memory.txt").suffix.lower()
    looks_json = suffix in {".json", ".jsonl", ".ndjson"} or text[:1] in "[{"
    if looks_json:
        entries, warnings = _parse_json(text, jsonl=suffix in {".jsonl", ".ndjson"})
        return PreparedImport("jsonl" if suffix in {".jsonl", ".ndjson"} else "json", tuple(entries), tuple(warnings))

    if suffix not in {".md", ".markdown", ".txt", ".text", ""}:
        raise MemoryImportError("暂时支持 Markdown、TXT、JSON 和 JSONL 文件")
    if len(text.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise MemoryImportError(f"单份记忆不能超过 {MAX_CONTENT_BYTES // 1000} KB，请拆成多个文件")
    return PreparedImport("markdown" if suffix in {".md", ".markdown"} else "text", (ImportEntry(title=PurePosixPath(filename or "memory").stem, content=text),))


def _slug(value: str, fallback: str) -> str:
    value = re.sub(r"[^\w\-\u4e00-\u9fff]+", "-", value, flags=re.UNICODE).strip("-_")
    return (value or fallback)[:96]


def _candidate_path(source_stem: str, entry: ImportEntry, index: int, many: bool) -> str:
    stem = _slug(source_stem, "memory")
    if entry.source_path:
        source_name = PurePosixPath(entry.source_path.replace("\\", "/")).name
        source_name = _slug(PurePosixPath(source_name).stem, f"memory-{index}")
    else:
        source_name = _slug(entry.title, f"memory-{index}")
    if not many and not entry.source_path:
        return validate_path(f"{IMPORT_ROOT}/{stem}.md")
    return validate_path(f"{IMPORT_ROOT}/{stem}/{index:03d}-{source_name}.md")


def build_paths(filename: str, entries: tuple[ImportEntry, ...]) -> list[tuple[str, ImportEntry]]:
    source_stem = PurePosixPath(filename or "memory").stem
    many = len(entries) > 1
    paths = [(_candidate_path(source_stem, entry, index, many), entry) for index, entry in enumerate(entries, 1)]
    # 相同标题不能让后一条静默覆盖前一条。
    seen: dict[str, int] = {}
    unique: list[tuple[str, ImportEntry]] = []
    for path, entry in paths:
        count = seen.get(path, 0) + 1
        seen[path] = count
        if count > 1:
            path = validate_path(path.removesuffix(".md") + f"-{count}.md")
        unique.append((path, entry))
    return unique
