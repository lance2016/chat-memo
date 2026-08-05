"""备份：全量快照 + 可读的记忆文件树。

两者用途不同，都要：

- ``pg_dump`` 能**完整恢复**（对话、记忆、版本历史、使用率埋点），用 ``pg_restore`` 还原
- 记忆导出成 .md 文件树是**给人看的**：能 grep、能用编辑器打开、能 git 管理

记忆平时只以数据库行的形式存在，磁盘上没有任何 .md 文件；这里是唯一把它们
落成真实文件的地方，所以路径校验要重新做一遍。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import Memory
from app.memory.paths import MEMORY_ROOT, validate_path

logger = logging.getLogger(__name__)

BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/backups"))
DUMP_TIMEOUT = 120


@dataclass
class BackupResult:
    dump_file: str
    dump_bytes: int
    memory_files: int
    memory_dir: str
    created_at: str
    detail: str = ""


async def run_backup(
    session: AsyncSession, settings: Settings | None = None
) -> BackupResult:
    settings = settings or get_settings()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")

    memory_dir = BACKUP_DIR / "memories"
    count = await export_memories(session, memory_dir)

    dump_path = BACKUP_DIR / f"chat-{stamp}.dump"
    detail = await _pg_dump(settings.database_url, dump_path)
    size = dump_path.stat().st_size if dump_path.exists() else 0

    logger.info(
        "💾 备份完成：%s (%s) · %d 条记忆导出到 %s",
        dump_path.name,
        _human(size),
        count,
        memory_dir,
    )
    return BackupResult(
        dump_file=dump_path.name,
        dump_bytes=size,
        memory_files=count,
        memory_dir=str(memory_dir),
        created_at=stamp,
        detail=detail,
    )


async def export_memories(session: AsyncSession, dest: Path) -> int:
    """把 memories 表导出成真实的 .md 文件树。

    每次全量重写：先清空再写，这样数据库里删掉的记忆不会在导出目录里阴魂不散。
    """
    memories = list((await session.execute(select(Memory))).scalars())

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()

    written = 0
    for memory in memories:
        target = _safe_target(memory.path, root)
        if target is None:
            logger.warning("跳过异常路径：%s", memory.path)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(memory.content, encoding="utf-8")
        written += 1
    return written


def _safe_target(memory_path: str, root: Path) -> Path | None:
    """把记忆路径映射到导出目录下的真实文件路径。

    路径在写入时已经校验过一次，但这里是**真的往文件系统写**，
    所以重新校验 + 解析后再确认落在 root 内 —— 双保险，代价可以忽略。
    """
    try:
        normalized = validate_path(memory_path)
    except Exception:
        return None

    relative = normalized[len(MEMORY_ROOT) :].lstrip("/")
    if not relative:
        return None

    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        return None
    return target


async def _pg_dump(database_url: str, dest: Path) -> str:
    """调 pg_dump 生成自定义格式快照（可被 pg_restore 选择性还原）。"""
    parsed = urlparse(database_url.replace("+asyncpg", ""))
    if not parsed.hostname:
        return "DATABASE_URL 无法解析，跳过 dump"

    cmd = [
        "pg_dump",
        "-h", parsed.hostname,
        "-p", str(parsed.port or 5432),
        "-U", parsed.username or "postgres",
        "-d", (parsed.path or "/").lstrip("/"),
        "-F", "c",  # custom 格式，比纯 SQL 小且支持选择性还原
        "-f", str(dest),
    ]
    env = {**os.environ, "PGPASSWORD": parsed.password or ""}

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd, env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=DUMP_TIMEOUT)
    except FileNotFoundError:
        return "未安装 pg_dump（镜像里需要 postgresql-client），仅导出了记忆文件"
    except TimeoutError:
        return f"pg_dump 超过 {DUMP_TIMEOUT}s 未完成"

    if process.returncode != 0:
        message = stderr.decode(errors="replace").strip()[:200]
        logger.error("pg_dump 失败: %s", message)
        return f"pg_dump 失败：{message}"
    return ""


def _human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size //= 1024
    return f"{size} TB"
