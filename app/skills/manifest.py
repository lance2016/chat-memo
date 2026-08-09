"""解析 `SKILL.md`：YAML frontmatter + Markdown 正文。

格式跟 Anthropic 的 Agent Skills 对齐，这样社区里现成的技能包可以直接装：

    ---
    name: pdf-processing
    description: 从 PDF 提取文本和表格。需要处理 PDF 文件时使用。
    ---

    # PDF 处理
    ...

**description 是这套机制里最贵的一个字段**，因为它是唯一常驻 system prompt 的内容。
它同时要回答两个问题：这技能干什么、什么时候该用它。写成「PDF 工具」模型永远不会
想起来用；写成三百字说明则每轮都在烧 token。

所以这里有两条线，而不是一条：

- `DESCRIPTION_BUDGET`（500 字）是**建议**，超了只标一条提醒。
  原来这里是硬上限，实测直接把 `anthropics/skills` 整个官方合集挡在门外
  （`claude-api` 那条 1068 字）—— 而**这个预算是我们自己的纪律，不是格式的规定**，
  拿它去否决别人写的技能，结果只是这个功能装不上任何真实技能。
- `MAX_DESCRIPTION_CHARS`（2000 字）才是硬上限。到这个量级已经不是「写长了」，
  是把正文写进了 frontmatter，那份内容每轮都进上下文，必须拒绝。

纪律没有放弃，只是从「拒绝安装」改成「在界面上说出来」—— 见 store.SkillEntry.warning。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from app.skills.errors import InvalidSkillManifest
from app.skills.paths import normalize_name

# 超过就在界面上提醒，但照装不误
DESCRIPTION_BUDGET = 500
# 超过就拒绝：到这个量级是把正文写进了 frontmatter
MAX_DESCRIPTION_CHARS = 2_000
# 正文的建议预算。同样只提醒不拒绝 —— 正文要模型主动 skill_read 才付出代价，
# 而实测官方的 claude-api 正文就有 7 万字符。拿它拒绝安装，代价是这个功能
# 装不上真实技能；提醒一句，代价只是界面上多一行字。
BODY_BUDGET = 20_000

FRONTMATTER_FENCE = "---"


@dataclass(frozen=True)
class SkillManifest:
    """一个技能的元数据和正文。"""

    name: str
    description: str
    body: str
    version: str = ""
    license: str = ""
    # 技能声明它需要哪些工具。这套后端没有沙箱，只作为展示信息，不做授权判断
    allowed_tools: tuple[str, ...] = ()
    # frontmatter 里其余没被识别的键，原样保留，界面上可以显示
    extra: dict[str, Any] | None = None


def parse_skill_md(text: str, *, expected_name: str | None = None) -> SkillManifest:
    """把 SKILL.md 的内容解析成 manifest，不合法就抛 InvalidSkillManifest。

    ``expected_name`` 传目录名时会额外校验两者一致 —— frontmatter 写 `foo`、
    目录叫 `bar` 的话，system prompt 里出现的是 `foo`，而 `skill_read("foo")`
    会找不到目录。这种错配是静默的，必须在安装时就拦下来。
    """
    front, body = _split_frontmatter(text)
    data = _load_yaml(front)

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise InvalidSkillManifest("SKILL.md 的 frontmatter 缺少 name")
    name = normalize_name(name)

    if expected_name is not None and name != expected_name:
        raise InvalidSkillManifest(
            f"SKILL.md 里写的 name 是 {name!r}，但目录叫 {expected_name!r}，两者必须一致"
        )

    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        raise InvalidSkillManifest(
            f"技能 {name} 的 frontmatter 缺少 description。"
            "这是模型判断「什么时候该用它」的唯一依据，不能省。"
        )
    description = " ".join(description.split())
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise InvalidSkillManifest(
            f"技能 {name} 的 description 有 {len(description)} 字，"
            f"上限 {MAX_DESCRIPTION_CHARS} —— 它每轮对话都进 system prompt，"
            "这个长度说明正文被写进了 frontmatter。"
        )

    known = {"name", "description", "version", "license", "allowed-tools", "allowed_tools"}
    return SkillManifest(
        name=name,
        description=description,
        body=body,
        version=_text(data.get("version")),
        license=_text(data.get("license")),
        allowed_tools=_string_list(
            data.get("allowed-tools", data.get("allowed_tools"))
        ),
        extra={k: v for k, v in data.items() if k not in known} or None,
    )


def _split_frontmatter(text: str) -> tuple[str, str]:
    """切出 frontmatter 和正文。

    只认文件开头的 `---`，且必须能找到闭合的那一行 —— 找不到就当作没有
    frontmatter 而不是把整个文件当 YAML 解析，否则一份普通 Markdown 里
    随便一条水平分隔线都能让报错信息变得完全不知所云。
    """
    stripped = text.lstrip("﻿")
    lines = stripped.splitlines()
    if not lines or lines[0].strip() != FRONTMATTER_FENCE:
        raise InvalidSkillManifest(
            "SKILL.md 必须以 --- 开头的 YAML frontmatter 起始，"
            "里面至少要有 name 和 description。"
        )

    for index in range(1, len(lines)):
        if lines[index].strip() == FRONTMATTER_FENCE:
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1:]).strip()
    raise InvalidSkillManifest("SKILL.md 的 frontmatter 没有闭合的 --- 行")


def _load_yaml(front: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(front) or {}
    except yaml.YAMLError as exc:
        raise InvalidSkillManifest(f"frontmatter 不是合法的 YAML：{exc}") from exc
    if not isinstance(data, dict):
        raise InvalidSkillManifest("frontmatter 必须是键值对")
    return data


def _text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _string_list(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    raise InvalidSkillManifest("allowed-tools 必须是列表或逗号分隔的字符串")
