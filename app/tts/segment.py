"""把还在生成中的回复切成一句一句，供边写边读。

**为什么切句在后端**：朗读用的文本要先经过 :func:`plain_text` 清洗（去代码块、
去 Markdown 符号），前端手上只有原始 Markdown。让前端自己切，它切的位置和服务端
清洗后的文本对不上，两套规则迟早跑偏。所以前端只管把「到目前为止的全文」丢过来，
切哪儿、清洗成什么样，都是这里说了算。

**游标是清洗后文本的字符偏移**，前端原样存回来即可，不用理解它的含义。

**流式中途的 Markdown 是残缺的**：代码围栏可能只开了一半。见 :func:`stable_prefix` ——
没闭合的那一段一律不切，宁可晚一点出声，也不能把 ``` 念出来。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.tts.client import plain_text

# 硬边界：到这儿一定是一句话说完了。
HARD_END = "。！？!?；;\n"
# 软边界：只有第一句才用。第一句越早出声，用户感知的等待越短，
# 断在逗号上稍微碎一点也值得；后面的句子有的是时间，走硬边界更连贯。
SOFT_END = "，,、：:"

# 短于这个长度不切，让它并进下一句 —— "好。" 单独合成一次，
# 开销全花在调用本身上，听感也碎。
MIN_CHARS = 8
# 第一句例外：早一秒出声就少一秒干等，碎一点认了。
FIRST_MIN_CHARS = 4
# 在逗号这种软边界上断开的门槛。只有第一句会走到这里，所以不分两档。
SOFT_MIN_CHARS = 8
# 长到这个份上还没遇到标点（模型在写长列表、英文长句），硬切，不能一直等。
MAX_CHARS = 120


@dataclass
class Segment:
    """切出来的一句。``text`` 为空表示还不够切，游标不动。"""

    text: str
    cursor: int


def stable_prefix(markdown: str) -> str:
    """砍掉尾部还没写完的部分，返回可以安全清洗的那一截。

    目前只处理代码围栏：``` 出现奇数次说明有个块正开着，里面的内容既可能是代码
    （不该念）也可能还没写完，一律截掉。等模型把它闭合，下一次调用自然会带上。
    """
    if markdown.count("```") % 2 == 1:
        return markdown[: markdown.rfind("```")]
    return markdown


def next_segment(
    markdown: str, cursor: int, *, flush: bool = False, max_chars: int = 0
) -> Segment:
    """从 ``cursor`` 往后切出下一句完整的话。

    :param markdown: 到目前为止**累计的全文**（原始 Markdown），不是增量。
    :param cursor: 上次返回的游标；第一次传 0。
    :param flush: 流结束时传 True，把剩下的尾巴全部吐出来（不管够不够长）。
    :param max_chars: 朗读总长上限，0 表示不限。到顶之后一律返回空。

    切不出来时返回空文本、游标原样 —— 调用方什么也不用做，等下一批增量再问。
    """
    text = plain_text(markdown if flush else stable_prefix(markdown))
    # 代码块闭合后清洗结果会变短，游标可能落到界外
    cursor = min(cursor, len(text))
    if max_chars and cursor >= max_chars:
        return Segment("", cursor)

    rest = text[cursor:]
    if max_chars:
        rest = rest[: max_chars - cursor]

    stripped = rest.lstrip()
    lead = len(rest) - len(stripped)
    if not stripped:
        # 全是空白：吃掉它，否则游标会卡在这里反复空转
        return Segment("", cursor + len(rest) if flush else cursor)

    if flush:
        return Segment(stripped.strip(), cursor + len(rest))

    first = cursor == 0
    cut = _find_cut(stripped, first=first)
    if cut is None:
        return Segment("", cursor)

    return Segment(stripped[:cut].strip(), cursor + lead + cut)


def _find_cut(text: str, *, first: bool) -> int | None:
    """返回切点（切完的长度），切不动返回 None。"""
    minimum = FIRST_MIN_CHARS if first else MIN_CHARS

    for i, char in enumerate(text):
        if char in HARD_END and i + 1 >= minimum:
            return i + 1

    if first:
        for i, char in enumerate(text):
            if char in SOFT_END and i + 1 >= SOFT_MIN_CHARS:
                return i + 1

    if len(text) >= MAX_CHARS:
        # 硬切也尽量挑个软边界，别切在词中间
        window = text[:MAX_CHARS]
        soft = max(window.rfind(c) for c in SOFT_END + " ")
        return soft + 1 if soft >= MIN_CHARS else MAX_CHARS

    return None
