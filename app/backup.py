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
from dataclasses import dataclass, field
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

# 文件名就是记录。`chat-20260808-041500.dump`
DUMP_PREFIX = "chat-"
DUMP_SUFFIX = ".dump"
_STAMP = "%Y%m%d-%H%M%S"


def dump_files(directory: Path | None = None) -> list[Path]:
    """已有的 dump，新的在前。"""
    directory = directory or BACKUP_DIR
    if not directory.exists():
        return []
    return sorted(
        directory.glob(f"{DUMP_PREFIX}*{DUMP_SUFFIX}"), key=lambda p: p.name, reverse=True
    )


def dump_day(path: Path) -> dt.date | None:
    """从文件名解析出这份 dump 是哪天的。解析不了返回 None。"""
    stem = path.name[len(DUMP_PREFIX) : -len(DUMP_SUFFIX)]
    try:
        return dt.datetime.strptime(stem, _STAMP).date()
    except ValueError:
        return None


def is_due(today: dt.date | None = None, directory: Path | None = None) -> bool:
    """今天该备份吗。

    **判据是「今天有没有备份过」，不是「到点了没有」** —— 和 notify 的补跑式扫描
    同一条教训（见 `app/notify/sweep.py`）：进程一重启计时器就从头开始，笔记本
    凌晨多半在睡眠，定时触发在这台机器上必然漏，查询式则是睡醒就补。

    而且**不需要新建一张表**：dump 的文件名里带日期，备份目录本身就是那份记录。
    少一张表、少一次迁移，也不会出现「表说备份过了但文件被删了」的假象。
    """
    today = today or dt.date.today()
    return not any(dump_day(path) == today for path in dump_files(directory))


def prune(keep: int, directory: Path | None = None) -> list[str]:
    """只留最近 `keep` 份，返回删掉的文件名。

    不轮换的话磁盘会被慢慢吃满，而磁盘满的第一个症状通常是**别的东西先坏**。
    """
    directory = directory or BACKUP_DIR
    doomed = dump_files(directory)[max(keep, 1) :]
    removed = []
    for path in doomed:
        try:
            path.unlink()
            removed.append(path.name)
        except OSError:
            logger.warning("旧备份删不掉：%s", path.name, exc_info=True)
    return removed


@dataclass
class BackupResult:
    dump_file: str
    dump_bytes: int
    memory_files: int
    memory_dir: str
    created_at: str
    # 本次新复制过去的附件（已经在备份里的不重复算）
    attachment_files: int = 0
    attachment_bytes: int = 0
    detail: str = ""
    # 本次轮换掉的旧备份
    pruned: list[str] = field(default_factory=list)


async def run_backup(
    session: AsyncSession, settings: Settings | None = None
) -> BackupResult:
    settings = settings or get_settings()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime(_STAMP)

    memory_dir = BACKUP_DIR / "memories"
    count = await export_memories(session, memory_dir)

    # 附件的元数据行在 pg_dump 里，但正文在磁盘上 —— 不复制这一步，
    # 恢复出来的库里每条带图的消息都会指向一个不存在的文件。
    attachment_files, attachment_bytes = copy_attachments(
        settings, BACKUP_DIR / "attachments"
    )

    dump_path = BACKUP_DIR / f"{DUMP_PREFIX}{stamp}{DUMP_SUFFIX}"
    detail = await _pg_dump(settings.database_url, dump_path)
    size = dump_path.stat().st_size if dump_path.exists() else 0

    # dump 失败时不要轮换：那会在「今天没备份成功」的情况下顺手删掉旧的好备份。
    pruned = prune(settings.backup_keep) if size else []

    logger.info(
        "💾 备份完成：%s (%s) · %d 条记忆导出到 %s",
        dump_path.name,
        _human(size),
        count,
        memory_dir,
    )
    return BackupResult(
        pruned=pruned,
        dump_file=dump_path.name,
        dump_bytes=size,
        memory_files=count,
        memory_dir=str(memory_dir),
        attachment_files=attachment_files,
        attachment_bytes=attachment_bytes,
        created_at=stamp,
        detail=detail,
    )


def copy_attachments(settings: Settings, dest: Path) -> tuple[int, int]:
    """把附件正文增量复制到备份目录。返回 (新增文件数, 新增字节数)。

    ⚠️ **不能照抄 `export_memories` 的「清空再全量重写」**：那对二进制附件意味着
    每天把所有图片重新复制一遍，而备份是每天跑的。内容寻址下文件名就是内容摘要，
    所以「已经存在就跳过」是天然正确的增量策略，不需要比对时间戳或大小。

    同理，这里**不删**备份里多出来的文件：源目录也从不删文件（见 store.py），
    真删了也说明是人工干预，备份不该跟着丢。
    """
    source = Path(settings.attachments_path)
    if not source.is_dir():
        return (0, 0)

    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    files = 0
    total = 0
    for path in sorted(source.rglob("*")):
        # 写到一半留下的临时文件不备份 —— 它的内容和文件名对不上
        if not path.is_file() or path.suffix == ".partial":
            continue
        try:
            relative = path.relative_to(source)
        except ValueError:
            continue
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            logger.warning("跳过越界的附件路径：%s", path)
            continue
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        files += 1
        total += target.stat().st_size
    return (files, total)


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
