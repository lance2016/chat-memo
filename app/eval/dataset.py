"""评测样本的形状与读写。

**这个模块定义的是整套评测里唯一的资产。** 评测代码是脚手架，随时可以重写；
数据集是花人力标出来的，重写一次要几个小时。所以样本格式要能独立于代码演进 ——
存成朴素 JSON，不用 pickle，不进数据库，可以 diff、可以手改、可以进 git。

一条样本 = 「这天的对话 + 整理前的记忆快照 + 期望产生什么变化」。

三条标注原则写在这里，因为读这个文件的人正要去标数据（详见 docs/evaluation.md 第四节）：

1. **标事实点，不标文本**。同一件事有一百种写法都对，逐字比对没有意义。
2. **必须有反例**。至少留几天 `no_op=true`（这天不该写任何记忆）——
   只测正例的评测会奖励一个疯狂写记忆的模型。
3. **快照要冻结**。「和旧记录冲突时改掉旧的」只有在起始状态确定时才可复现。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.memory.audit import parse_index
from app.memory.paths import INDEX_PATH

CASE_SUFFIX = ".json"

# 事实点的长度上限。不是真的怕长，是怕它变成一整段摘要 —— 裁判逐条判定时
# 对着一段话判「记住了没有」，那个判定没有意义。
MAX_FACT_CHARS = 60

VALID_ROLES = frozenset({"user", "assistant"})


@dataclass(frozen=True)
class EvalMessage:
    role: str  # user | assistant
    text: str


@dataclass(frozen=True)
class EvalConversation:
    title: str
    messages: list[EvalMessage]


@dataclass(frozen=True)
class Correction:
    """一条该被改掉的旧记录。

    `stale` 是整理前记忆里那段过期内容的原文片段 —— 判分时先确认它确实不在了，
    再看新说法有没有进去。只看新说法会漏掉最糟的那种失败：新旧并存。
    """

    stale: str
    becomes: str = ""


@dataclass(frozen=True)
class Expectation:
    """这次整理**应该**发生什么。全部用自然语言事实点表述。"""

    # 该被记住的事实。判分是逐条二值判定，不是整体打分
    facts: list[str] = field(default_factory=list)
    # 该被修正的旧记录
    corrections: list[Correction] = field(default_factory=list)
    # 明确不该出现在记忆里的东西（一次性问答、技术细节、编造的内容）
    forbidden: list[str] = field(default_factory=list)
    # 这天正确的行为是「什么都不做」。写记忆就是错
    no_op: bool = False


@dataclass(frozen=True)
class EvalCase:
    id: str
    date: str
    # 整理前的记忆快照：path -> content。必须包含 /memories/MEMORY.md
    memory_before: dict[str, str]
    conversations: list[EvalConversation]
    expect: Expectation
    # 为什么挑这天。给未来的自己看 —— 半年后不会记得这条样本想测什么
    note: str = ""

    def validate(self) -> list[str]:
        """标注常见错误的自查。返回问题列表，空 = 没问题。

        标数据是手工活，错的方式高度重复，所以把这几条固化下来。**一条标错的样本
        会安静地拉低分数好几轮，而人只会去怀疑模型** —— 这里每多挡住一种错误，
        就少一次那样的排查。
        """
        problems: list[str] = []
        if not self.id:
            problems.append("缺 id")
        if not self.conversations:
            problems.append("没有对话，这条样本没有输入")
        if self.expect.no_op and (self.expect.facts or self.expect.corrections):
            problems.append("no_op 样本不该同时期望写入事实或修正")
        if not self.expect.no_op and not (self.expect.facts or self.expect.corrections):
            problems.append("既不是 no_op，又没标任何期望 —— 这条样本判不了分")

        # 起点没有索引，模型第一件事就是建索引，那不是这条样本想测的东西。
        if INDEX_PATH not in self.memory_before:
            problems.append(f"memory_before 必须包含 {INDEX_PATH}（哪怕只有一个标题）")
        else:
            # 只查格式，不查条目全不全 —— 「索引漏了一条」是合法的样本设计
            # （见 evals/cases 里那条索引缺口样本），格式写错才是脏输入。
            problems += [
                f"memory_before 的索引第 {line} 行格式不对: {raw!r}"
                for line, raw in parse_index(self.memory_before[INDEX_PATH])[1]
            ]

        haystack = "\n".join(self.memory_before.values())
        for correction in self.expect.corrections:
            if correction.stale and correction.stale not in haystack:
                problems.append(
                    f"修正项的 stale 片段在整理前的记忆里找不到: {correction.stale!r}"
                )

        # 「标事实点不标文本」是三条原则之首，但把一整段摘要粘进一条 fact 完全
        # 通得过校验，然后裁判逐条判定时就会对着一整段话判「记住了没有」——
        # 那个判定没有意义，且看不出来是标注的问题。
        for fact in self.expect.facts:
            if "\n" in fact or len(fact) > MAX_FACT_CHARS:
                problems.append(
                    f"事实点太长或成段了（限 {MAX_FACT_CHARS} 字、单行），拆开写: {fact[:30]!r}…"
                )

        for conversation in self.conversations:
            for message in conversation.messages:
                if message.role not in VALID_ROLES:
                    problems.append(
                        f"消息的 role 只能是 {'/'.join(sorted(VALID_ROLES))}，"
                        f"收到 {message.role!r}"
                    )
        return problems


def load_case(path: Path) -> EvalCase:
    """读一条样本。

    强制 `id == 文件名`：报告按 `id` 索引结果，目录按文件名排序，两者不一致时
    报告里的样本名就指不回文件，而那正是出了问题要去看的时候。
    """
    case = _case_from_dict(json.loads(path.read_text(encoding="utf-8")))
    if case.id != path.stem:
        raise ValueError(f"样本 id 与文件名不一致: id={case.id!r} 文件={path.name}")
    return case


def load_cases(directory: Path) -> list[EvalCase]:
    """按文件名排序读一个目录下的全部样本。

    排序是为了让两次运行的 case 顺序一致 —— 报告要能逐行对照，
    顺序抖动会让 diff 变得没法看。
    """
    if not directory.exists():
        raise FileNotFoundError(f"数据集目录不存在: {directory}")
    return [load_case(p) for p in sorted(directory.glob(f"*{CASE_SUFFIX}"))]


def dump_case(case: EvalCase, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(case), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _case_from_dict(raw: dict[str, Any]) -> EvalCase:
    expect_raw = raw.get("expect") or {}
    return EvalCase(
        id=str(raw.get("id") or ""),
        date=str(raw.get("date") or ""),
        memory_before={str(k): str(v) for k, v in (raw.get("memory_before") or {}).items()},
        conversations=[
            EvalConversation(
                title=str(item.get("title") or ""),
                messages=[
                    EvalMessage(
                        # 不默认成 "user"：把拼错的 assistant 静默变成用户发言，
                        # 等于悄悄改了这条样本的输入，而没有任何迹象。
                        role=str(message.get("role") or ""),
                        text=str(message.get("text") or ""),
                    )
                    for message in item.get("messages") or []
                ],
            )
            for item in raw.get("conversations") or []
        ],
        expect=Expectation(
            facts=[str(f) for f in expect_raw.get("facts") or []],
            corrections=[
                Correction(
                    stale=str(item.get("stale") or ""),
                    becomes=str(item.get("becomes") or ""),
                )
                for item in expect_raw.get("corrections") or []
            ],
            forbidden=[str(f) for f in expect_raw.get("forbidden") or []],
            no_op=bool(expect_raw.get("no_op")),
        ),
        note=str(raw.get("note") or ""),
    )
