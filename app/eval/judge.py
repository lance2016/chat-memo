"""第 2 层：模型裁判。

第 0 层（`memory/audit.py`）和第 1 层（`ConsolidationResult`）只回答「跑没跑坏」，
回答不了「记对了吗」—— 那需要读懂整理后的记忆到底说了什么。这个模块就是那把尺子。

三条设计取舍，都写在 docs/evaluation.md 第五节，落到代码里是这样：

1. **逐条二值判定，不整体打 1-5 分。** 「这条事实在不在」模型答得稳，「这份记忆
   值几分」两次能差一分。指标本身就带噪声，判据再模糊就没法归因了。
2. **裁判失败 ≠ 0 分。** 解析失败重试一次，两次都失败就 `failed=True`，让调用方
   把这条样本作废。把抽风算成 0 分，会让一次网络波动看起来像质量下降。
3. **长度偏好要显式对冲。** LLM 裁判普遍偏爱更长的输出，而记忆恰恰应该短 ——
   这个偏差和本项目的目标是反向的，所以 prompt 里明写「简洁不扣分」。

三个指标一次调用判完：它们读的是同一份材料（记忆快照 + 对话原文），拆成三次
调用要付三遍输入 token，而判据之间还互相参照（判「编造」要看事实召回的结论）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from app.eval.dataset import EvalCase
from app.llm.provider import LLMProvider

logger = logging.getLogger(__name__)

# 三态而不是二态。`coexist` 单列是因为它是最糟的失败：新旧说法并存时，
# 检索到哪一条全看运气，比压根没改（至少还是一致的）更难发现、危害更久。
CORRECTION_STATUSES = ("fixed", "coexist", "missed")
ERROR_KINDS = ("fabricated", "wrong_edit", "forbidden")


@dataclass(frozen=True)
class FactVerdict:
    fact: str
    recalled: bool
    # 记忆里的哪一句支撑了这个判定；判 false 时写为什么。
    # 强制要证据是为了压住模型「凭印象说有」的倾向 —— 要它抄一句原文，成本高于瞎猜。
    evidence: str


@dataclass(frozen=True)
class CorrectionVerdict:
    stale: str
    status: str  # fixed | coexist | missed
    evidence: str


@dataclass(frozen=True)
class ErrorFinding:
    text: str  # 记忆里有问题的那段
    kind: str  # fabricated | wrong_edit | forbidden
    evidence: str
    # 出在哪个记忆文件。人工复核时不用全文搜；模型没给就留空
    path: str = ""


@dataclass(frozen=True)
class JudgeVerdict:
    facts: tuple[FactVerdict, ...] = ()
    corrections: tuple[CorrectionVerdict, ...] = ()
    errors: tuple[ErrorFinding, ...] = ()
    # 裁判自己没跑成功（连续解析失败）。调用方看到它要作废这条样本的指标，
    # 而不是记成 0 —— 见模块 docstring 第 2 条。
    failed: bool = False
    detail: str = ""

    @property
    def recall(self) -> float | None:
        """没有标注事实时返回 None，不返回 0。

        `no_op` 样本天然没有 facts，返回 0 会把它算进平均分里往下拉，
        看起来像质量下降，实际上什么都没发生。
        """
        if not self.facts:
            return None
        return sum(1 for f in self.facts if f.recalled) / len(self.facts)

    @property
    def correction_rate(self) -> float | None:
        """只有 `fixed` 算对。`coexist` 和 `missed` 都是没做到，不给部分分。"""
        if not self.corrections:
            return None
        return sum(1 for c in self.corrections if c.status == "fixed") / len(
            self.corrections
        )

    @property
    def error_count(self) -> int:
        return len(self.errors)


JUDGE_SYSTEM = """你在评测一个个人助手的「每日记忆整理」结果，判断它有没有把该记的
事记对。

**你在判断事实是否被记住，不是在评价文笔或组织方式。** 措辞好不好、分节合不合理、
标题起得漂不漂亮，一律不在评判范围内。同一件事有一百种写法，只要意思对就算对。

**记忆越简洁越好，简洁不扣分。** 一句话记完的事不需要写成三句。不要因为某份记忆
写得短就倾向判它漏记了 —— 短是这个系统追求的目标，长反而是问题。反过来，长篇但
没说到点子上的内容不能算记住了。

逐条判定，不要给整体评分。每一条都必须给出证据：判「记住了」就抄出记忆里支撑它
的那句原文；判「没记住」就说明你查了哪里、为什么认为不在。

只输出一个 JSON 对象，不要有任何其他文字，不要用 ``` 包裹。"""

JUDGE_PROMPT = """下面是一次记忆整理的输入和输出。请判定三件事。

只输出这个形状的 JSON：

{{
  "facts": [{{"id": 0, "recalled": true, "evidence": "……"}}],
  "corrections": [{{"id": 0, "status": "fixed", "evidence": "……"}}],
  "errors": [{{"text": "……", "kind": "fabricated", "path": "……", "evidence": "……"}}]
}}

**facts** —— 对「应该被记住的事实」里的**每一条**都给一个判定，一条都不能少，
`id` 用下面给出的编号。`recalled` 只看意思有没有体现在整理后的记忆里：换了说法、
拆成两句、并进别的段落，都算记住了；只有记忆里找不到这个意思才算没记住。
`evidence` 判 true 时抄记忆里的原句，判 false 时写你的理由。

**corrections** —— 对「应该被修正的旧记录」里的**每一条**都给一个判定，一条都不能
少。先看旧说法还在不在，再看新说法进去没有，三选一：

- `fixed` —— 旧说法不在了，新说法进去了
- `coexist` —— 新旧并存。这是最糟的失败，宁可判它也不要放过
- `missed` —— 压根没改，还是旧说法

**errors** —— 整理后的记忆里有问题的内容，没有就返回空数组。三类：

- `fabricated` —— 记忆里有，但下面的对话原文里根本没有的内容。**必须对照对话原文
  确认**，不要凭常识判断合不合理
- `wrong_edit` —— 整理前记忆里原本正确的记录，被改错了或改得意思变了
- `forbidden` —— 命中了下面「明确不该出现的东西」清单

`text` 抄记忆里有问题的那一段，`path` 写它在哪个记忆文件里（上面 `###` 后面那个路径），
`evidence` 说明它错在哪、和哪段原文对不上。
**宁可漏报也不要误报**：说不准的不要列进来，一条误报会让整轮评测的结论失真。

---

## 整理前的记忆

{memory_before}

## 整理后的记忆（判定对象）

{memory_after}

## 这天的对话原文（判断「编造」的唯一依据）

{transcript}

## 应该被记住的事实

{facts}

## 应该被修正的旧记录

{corrections}

## 明确不该出现的东西

{forbidden}"""


class Judge:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def judge(
        self, case: EvalCase, memory_after: dict[str, str], transcript: str
    ) -> JudgeVerdict:
        """判一条样本的三个指标。一次调用判完，返回结构化判定。"""
        expect = case.expect
        if not (expect.facts or expect.corrections or expect.forbidden):
            # 没有任何标注可对照，问模型也问不出东西来。`no_op` 样本走这条路，
            # 它该走的是 `judge_no_op` —— 那是纯代码能算的，不该花 token。
            return JudgeVerdict(detail="没有可判定的标注，跳过裁判")

        prompt = JUDGE_PROMPT.format(
            memory_before=_render_memory(case.memory_before),
            memory_after=_render_memory(memory_after),
            transcript=transcript.strip() or "（无对话原文）",
            facts=_numbered(expect.facts) or "（无）",
            corrections=_numbered(
                [
                    f"旧说法：{c.stale}" + (f" → 应改成：{c.becomes}" if c.becomes else "")
                    for c in expect.corrections
                ]
            )
            or "（无）",
            forbidden="\n".join(f"- {item}" for item in expect.forbidden) or "（无）",
        )

        # 重试一次：模型偶尔会返回半个 JSON，或漏判几条。评一条样本只跑一次，
        # 为一次抽风把这条样本的指标作废不值得，多一次调用的成本可以忽略。
        for attempt in (1, 2):
            raw = await self.provider.complete(
                system=JUDGE_SYSTEM, prompt=prompt, max_tokens=4000
            )
            data = _parse_json_object(raw)
            if data is not None:
                verdict = _build_verdict(case, data)
                if verdict is not None:
                    return verdict
            logger.warning(
                "裁判输出不可用（第 %d 次）: case=%s raw=%r", attempt, case.id, raw[:200]
            )
        # 不抛异常：一条样本判不了不该中断整轮评测，但它的指标必须作废而不是记 0。
        return JudgeVerdict(failed=True, detail="裁判连续两次没给出可用的 JSON，详见日志")


def judge_no_op(case: EvalCase, changed_paths: list[str]) -> bool | None:
    """no_op 样本判定：没写任何记忆才算对。不是 no_op 样本返回 None。

    这件事是纯代码的：diff 空不空是确定的事实，请模型判只会引入噪声，还要花钱。
    """
    if not case.expect.no_op:
        return None
    return not changed_paths


def _build_verdict(case: EvalCase, data: dict[str, Any]) -> JudgeVerdict | None:
    """把模型的原始 JSON 对齐到样本标注上。对不齐返回 None，交给调用方重试。

    用 id 而不是文本回填 fact/stale：模型很容易「顺手把事实点复述一遍」，
    文本一改就对不上标注，指标会静默变错。id 对不上则宁可重试。

    漏判也算对不齐 —— 少判一条事实和判它「没记住」在数字上无法区分，
    默默补成 false 就是把裁判的失误算成被测系统的失分。
    """
    facts = _align(data.get("facts"), len(case.expect.facts))
    corrections = _align(data.get("corrections"), len(case.expect.corrections))
    if facts is None or corrections is None:
        return None

    return JudgeVerdict(
        facts=tuple(
            FactVerdict(
                fact=case.expect.facts[i],
                recalled=bool(item.get("recalled")),
                evidence=str(item.get("evidence") or "").strip(),
            )
            for i, item in sorted(facts.items())
        ),
        corrections=tuple(
            CorrectionVerdict(
                stale=case.expect.corrections[i].stale,
                # 认不出的状态一律当 missed：能确认「改好了」才给分，
                # 模型答了个没见过的词说明它没按判据走，不该按对处理。
                status=_status(item.get("status")),
                evidence=str(item.get("evidence") or "").strip(),
            )
            for i, item in sorted(corrections.items())
        ),
        errors=_findings(data.get("errors")),
    )


def _align(raw: object, expected: int) -> dict[int, dict[str, Any]] | None:
    """按 id 把判定项归位，要求恰好覆盖 0..expected-1。"""
    if expected == 0:
        return {}
    if not isinstance(raw, list):
        return None
    aligned: dict[int, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        index = item.get("id")
        if not isinstance(index, int) or isinstance(index, bool):
            continue
        if 0 <= index < expected:
            aligned[index] = item
    return aligned if len(aligned) == expected else None


def _status(raw: object) -> str:
    value = str(raw or "").strip().lower()
    return value if value in CORRECTION_STATUSES else "missed"


def _findings(raw: object) -> tuple[ErrorFinding, ...]:
    """错误项没有标注可对齐，只能按形状过滤。

    kind 认不出来的归 `fabricated` 而不是丢掉：模型说这段有问题这件事本身有价值，
    丢掉等于把一条真实的错误漏报了，而分类错只影响归因时看哪一栏。
    """
    if not isinstance(raw, list):
        return ()
    findings: list[ErrorFinding] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        kind = str(item.get("kind") or "").strip().lower()
        findings.append(
            ErrorFinding(
                text=text,
                kind=kind if kind in ERROR_KINDS else "fabricated",
                evidence=str(item.get("evidence") or "").strip(),
                path=str(item.get("path") or "").strip(),
            )
        )
    return tuple(findings)


def _numbered(items: list[str]) -> str:
    """带编号列出来。判定要按 id 回填，编号必须和列表下标严格一致。"""
    return "\n".join(f"[{i}] {item}" for i, item in enumerate(items))


def _render_memory(memory: dict[str, str]) -> str:
    """记忆快照拍平成带路径的文本。路径要保留 —— 判「编造」时要能指到哪个文件。"""
    if not memory:
        return "（空）"
    return "\n\n".join(
        f"### {path}\n{content}" for path, content in sorted(memory.items())
    )


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """从模型输出里抠出 JSON 对象。

    提示词里写了「不要用 ``` 包裹」，但模型时不时还是会包，或者在前面加一句
    「好的，这是……」。截取第一个 `{` 到最后一个 `}` 能覆盖这两种情况。
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
