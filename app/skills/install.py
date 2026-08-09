"""下载并安装技能包。

支持的来源写法（`parse_source` 全部归一成一个 zip 地址 + 一个子目录）：

    anthropics/skills                      GitHub 仓库，默认分支
    anthropics/skills@main                 指定分支 / tag / commit
    anthropics/skills/document-skills/pdf  仓库里的某个子目录
    https://github.com/owner/repo/tree/main/path/to/skill
    https://example.com/whatever.zip       任意 zip 直链

一个包里可以有多个 `SKILL.md`（`anthropics/skills` 就是这样的合集仓库），
所有找到的都会装上，除非来源指定了子目录。

⚠️ **这里下载的是任意第三方内容，而技能正文会被模型当成操作指令执行。**
所以有两道边界：技术上的（解压时的路径、大小、条目数限制，见 `_extract`），
以及产品上的 —— 安装永远是用户显式发起的动作，模型自己没有安装工具。
"""

from __future__ import annotations

import io
import logging
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.skills.errors import SkillError, SkillInstallError
from app.skills.manifest import SkillManifest, parse_skill_md
from app.skills.store import MANIFEST_FILENAME

logger = logging.getLogger(__name__)

# 下载体积上限。合集仓库（anthropics/skills）打包后是几 MB 量级，
# 32MB 足够宽松；没有上限的话一条 URL 就能把磁盘写满。
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
# 解压后的总大小和条目数。zip bomb 的压缩比可以到 1000:1，
# 只限下载体积挡不住 —— 必须在写盘时按累计解压量掐断。
MAX_EXTRACTED_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 5_000
DOWNLOAD_TIMEOUT = 60

_GITHUB_TREE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)"
    r"(?:/(?:tree|blob)/(?P<ref>[^/]+)(?:/(?P<path>.+?))?)?/?$"
)
_SHORTHAND = re.compile(
    r"^(?:github:)?(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)"
    r"(?:/(?P<path>[\w./-]+?))?(?:@(?P<ref>[\w./-]+))?/?$"
)


@dataclass(frozen=True)
class Source:
    """归一化之后的来源：下载哪个 zip、取里面的哪个子目录。"""

    zip_url: str
    subdir: str = ""
    # 回显给用户的原始写法，也存进数据库，将来「更新」要靠它
    label: str = ""
    ref: str = ""


@dataclass(frozen=True)
class Installed:
    name: str
    description: str
    # 这次是覆盖了一个同名技能，还是全新安装
    replaced: bool


@dataclass(frozen=True)
class Skipped:
    """包里有 SKILL.md、但那份 manifest 不合法。装不了，但要说出来。"""

    path: str
    reason: str


@dataclass(frozen=True)
class InstallOutcome:
    installed: tuple[Installed, ...]
    skipped: tuple[Skipped, ...] = ()


def parse_source(raw: str) -> Source:
    """把用户输入的来源归一成 zip 地址 + 子目录。"""
    text = (raw or "").strip()
    if not text:
        raise SkillInstallError("请填写技能来源")

    if text.lower().endswith(".zip") and text.startswith(("http://", "https://")):
        return Source(zip_url=text, label=text)

    match = _GITHUB_TREE.match(text) or _SHORTHAND.match(text)
    if match is None:
        raise SkillInstallError(
            f"看不懂的来源 {raw!r}。支持 owner/repo[/子目录][@分支]、"
            "GitHub 网页地址，或一个 .zip 直链。"
        )

    parts = match.groupdict()
    owner, repo = parts["owner"], parts["repo"].removesuffix(".git")
    # 没写分支时用 HEAD：codeload 认这个，省掉一次查默认分支叫 main 还是 master 的请求
    ref = parts.get("ref") or "HEAD"
    return Source(
        zip_url=f"https://codeload.github.com/{owner}/{repo}/zip/{ref}",
        subdir=(parts.get("path") or "").strip("/"),
        label=text,
        ref=ref,
    )


async def download(source: Source, *, client: httpx.AsyncClient | None = None) -> bytes:
    """把 zip 下下来。边下边数字节，超限立刻断，不等 Content-Length。"""
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True)
    try:
        chunks: list[bytes] = []
        total = 0
        async with client.stream("GET", source.zip_url) as response:
            if response.status_code == 404:
                raise SkillInstallError(
                    f"{source.label or source.zip_url} 不存在（404）。"
                    "检查仓库名和分支是否写对了。"
                )
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise SkillInstallError(
                        f"下载超过 {MAX_DOWNLOAD_BYTES // 1024 // 1024}MB 上限，已中止"
                    )
                chunks.append(chunk)
        return b"".join(chunks)
    except httpx.HTTPStatusError as exc:
        raise SkillInstallError(
            f"下载失败（HTTP {exc.response.status_code}）：{source.zip_url}"
        ) from exc
    except httpx.HTTPError as exc:
        raise SkillInstallError(f"下载失败：{exc}") from exc
    finally:
        if owns_client:
            await client.aclose()


def install_zip(
    payload: bytes, root: str | Path, *, subdir: str = "", overwrite: bool = True
) -> InstallOutcome:
    """解压 zip 并把里面的技能装到 ``root``。同步函数，调用方走 to_thread。

    先整包解到临时目录再挑技能，而不是边解边装：一个包里可能有五个技能，
    其中第三个的 manifest 是坏的 —— 边解边装会留下「装了一半」的状态，
    而这种状态在磁盘即事实来源的设计下没法回滚。
    """
    destination = Path(root)
    destination.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="skill-install-") as tmp:
        staged = Path(tmp) / "unpacked"
        _extract(payload, staged)
        # 解析一次再用：macOS 的 /var 是指向 /private/var 的软链，混用解析过和没解析过
        # 的路径会让后面的 relative_to / is_relative_to 全部判错
        staged = staged.resolve()
        candidates = _find_skills(staged, subdir)
        if not candidates:
            where = f"的 {subdir} 下" if subdir else "里"
            raise SkillInstallError(
                f"这个包{where}没有找到 SKILL.md。"
                "一个技能必须是含 SKILL.md 的目录。"
            )

        # **先全部校验完再动磁盘**：这样一个坏 manifest 永远不会留下装了一半的状态，
        # 而那种状态在「磁盘即事实来源」的设计下没法回滚。
        #
        # 坏的**跳过**而不是整批拒绝。合集仓库一装十几个（anthropics/skills 有 18 个），
        # 让其中一个不合规的把其余全挡在门外，实际结果是这个功能装不上任何东西。
        # 跳过的会连原因一起报出来，人看得见就能去提 issue 或手改。
        parsed: list[tuple[Path, SkillManifest]] = []
        skipped: list[Skipped] = []
        for directory in candidates:
            where = directory.relative_to(staged).as_posix()
            try:
                # 不传 expected_name：**frontmatter 里的 name 才是技能的身份**，
                # 压缩包里那层目录叫什么是偶然的（官方合集里就有 template/ 装着
                # name: template-skill）。落盘时用 manifest.name 建目录，
                # 装完之后目录名和 name 一定一致，store 那边的校验才立得住。
                manifest = parse_skill_md(
                    (directory / MANIFEST_FILENAME).read_text(encoding="utf-8")
                )
            except (SkillError, OSError, UnicodeDecodeError) as exc:
                skipped.append(Skipped(path=where, reason=str(exc)))
                continue
            parsed.append((directory, manifest))

        if not parsed:
            reasons = "；".join(f"{s.path}：{s.reason}" for s in skipped[:3])
            raise SkillInstallError(f"包里没有一个合法的技能。{reasons}")

        results: list[Installed] = []
        for directory, manifest in parsed:
            target = destination / manifest.name
            existed = target.exists()
            if existed and not overwrite:
                raise SkillInstallError(
                    f"已经装过 {manifest.name} 了。要覆盖请先删除，或勾选覆盖安装。"
                )
            if existed:
                shutil.rmtree(target)
            shutil.copytree(directory, target, symlinks=False)
            logger.info("↧ 安装技能 %s（%s）", manifest.name, "覆盖" if existed else "新增")
            results.append(
                Installed(
                    name=manifest.name,
                    description=manifest.description,
                    replaced=existed,
                )
            )
        return InstallOutcome(installed=tuple(results), skipped=tuple(skipped))


def _extract(payload: bytes, destination: Path) -> None:
    """解压，同时挡掉 zip slip、软链、zip bomb 和条目数爆炸。

    `zipfile.extractall` 会把 `../` 规范化掉但**不会拒绝**，而且照样跟随
    压缩包里声明的软链 —— 所以这里逐条自己写，不用 extractall。
    """
    destination.mkdir(parents=True, exist_ok=True)
    base = destination.resolve()

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise SkillInstallError(f"下载到的不是一个有效的 zip：{exc}") from exc

    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise SkillInstallError(
                f"压缩包有 {len(infos)} 个条目，超过 {MAX_ENTRIES} 的上限"
            )

        written = 0
        for info in infos:
            if _is_symlink(info):
                # 软链可以指向 /etc/passwd，而后续所有路径校验都在字符串层面 ——
                # 直接跳过是唯一安全的处理
                logger.warning("跳过压缩包里的符号链接：%s", info.filename)
                continue

            target = (destination / info.filename).resolve()
            if not target.is_relative_to(base):
                raise SkillInstallError(
                    f"压缩包里有越界路径 {info.filename!r}，已拒绝安装"
                )
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            written += info.file_size
            if written > MAX_EXTRACTED_BYTES:
                raise SkillInstallError(
                    f"解压后超过 {MAX_EXTRACTED_BYTES // 1024 // 1024}MB 上限，已中止"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink, length=64 * 1024)


def _find_skills(staged: Path, subdir: str) -> list[Path]:
    """在解压结果里找技能目录。

    GitHub 的 zip 会多包一层 `repo-branch/`，所以子目录要在那一层里面找 ——
    但直链 zip 没有这一层。两种都试，找不到再报错。
    """
    roots = [staged]
    children = [p for p in staged.iterdir() if p.is_dir()]
    if len(children) == 1:
        roots.insert(0, children[0])

    if subdir:
        for root in roots:
            candidate = (root / subdir).resolve()
            if candidate.is_relative_to(root.resolve()) and candidate.is_dir():
                return _scan(candidate)
        raise SkillInstallError(f"包里没有 {subdir} 这个目录")

    for root in roots:
        found = _scan(root)
        if found:
            return found
    return []


def _scan(root: Path) -> list[Path]:
    """root 本身是技能，或者它下面的目录是技能。不做无限递归。"""
    if (root / MANIFEST_FILENAME).is_file():
        return [root]
    found = [
        child
        for child in sorted(root.rglob(MANIFEST_FILENAME))
        if child.is_file() and not child.parent.name.startswith(".")
    ]
    return [child.parent for child in found]


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    # 高 16 位是 Unix 权限位，0xA000 是 S_IFLNK
    return (info.external_attr >> 16) & 0xF000 == 0xA000
