import pytest

from app.skills.errors import InvalidSkillManifest, InvalidSkillPath
from app.skills.manifest import DESCRIPTION_BUDGET, MAX_DESCRIPTION_CHARS, parse_skill_md

VALID = """---
name: pdf-processing
description: 从 PDF 提取文本和表格。需要处理 PDF 文件时使用。
version: "1.2"
license: MIT
allowed-tools: [bash, read]
---

# PDF 处理

先看文件有没有文本层。
"""


def test_parses_frontmatter_and_body():
    manifest = parse_skill_md(VALID, expected_name="pdf-processing")

    assert manifest.name == "pdf-processing"
    assert manifest.description.startswith("从 PDF 提取")
    assert manifest.version == "1.2"
    assert manifest.license == "MIT"
    assert manifest.allowed_tools == ("bash", "read")
    assert manifest.body.startswith("# PDF 处理")
    # frontmatter 不能留在正文里，否则模型会把它当成内容读
    assert "description:" not in manifest.body


def test_directory_name_must_match_frontmatter():
    """错配是静默故障：提示词里出现的是 frontmatter 的名字，工具找的是目录名。"""
    with pytest.raises(InvalidSkillManifest, match="目录叫"):
        parse_skill_md(VALID, expected_name="pdf")


def test_description_is_required():
    text = "---\nname: foo\n---\n\n正文"
    with pytest.raises(InvalidSkillManifest, match="description"):
        parse_skill_md(text)


def test_over_budget_description_still_installs():
    """500 字是我们自己的预算，不是格式规定 —— 拿它否决别人的技能只会一个都装不上。

    实测 anthropics/skills 官方合集里的 claude-api 就是 1068 字。
    纪律改成在界面上提醒（见 store._budget_warning），不再拒绝安装。
    """
    text = f"---\nname: foo\ndescription: {'长' * (DESCRIPTION_BUDGET + 100)}\n---\n"

    assert len(parse_skill_md(text).description) == DESCRIPTION_BUDGET + 100


def test_description_length_has_a_hard_cap():
    """到 2000 字就不是「写长了」，是把正文写进了 frontmatter。"""
    text = f"---\nname: foo\ndescription: {'长' * (MAX_DESCRIPTION_CHARS + 1)}\n---\n"
    with pytest.raises(InvalidSkillManifest, match="上限"):
        parse_skill_md(text)


def test_missing_frontmatter_is_rejected():
    with pytest.raises(InvalidSkillManifest, match="frontmatter"):
        parse_skill_md("# 只是一份普通 Markdown\n\n没有 frontmatter。")


def test_unclosed_frontmatter_is_rejected():
    with pytest.raises(InvalidSkillManifest, match="闭合"):
        parse_skill_md("---\nname: foo\ndescription: bar\n\n正文")


def test_invalid_name_is_rejected():
    text = "---\nname: Not Valid\ndescription: 说明\n---\n"
    with pytest.raises(InvalidSkillPath):
        parse_skill_md(text)


def test_description_whitespace_is_collapsed():
    """YAML 折行会带进换行符，而这段要塞进 system prompt 的一行里。"""
    text = "---\nname: foo\ndescription: >\n  第一行\n  第二行\n---\n"
    assert parse_skill_md(text).description == "第一行 第二行"
