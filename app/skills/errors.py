from __future__ import annotations


class SkillError(Exception):
    """技能层的可预期失败。消息直接给模型或用户看，所以要写人话。"""


class SkillNotFound(SkillError):
    pass


class InvalidSkillPath(SkillError):
    pass


class InvalidSkillManifest(SkillError):
    """SKILL.md 缺失、frontmatter 不合法，或必填字段没写。"""


class SkillInstallError(SkillError):
    """下载、解压或落盘失败。"""
