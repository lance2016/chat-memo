"""每日回顾：digest 生成与悬而未决的闭环。

重点不在 happy path，而在降级：摘要格式没对上时记忆链路必须照常工作，
回顾出错时不能把记忆整理的结果也带走。
"""

import datetime as dt
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import ConversationSummary, DailyDigest, MemoryVersion, OpenLoop
from app.jobs.consolidate import Consolidator, _parse_json_object
from app.jobs.prompts import DIGEST_PROMPT, SUMMARY_SYSTEM
from app.llm.anthropic_provider import AnthropicProvider
from app.review.router import list_open_loops
from tests.fakes import FakeAnthropic, text_turn, tool_turn
from tests.test_consolidate import seed_conversation

TODAY = dt.date.today()


def provider_with(turns: list) -> AnthropicProvider:
    return AnthropicProvider(
        settings=Settings(anthropic_api_key="test"), client=FakeAnthropic(turns)
    )


def summary_turn(
    memory: str,
    recap: str,
    open_loops: list[str] | None = None,
    quote: str = "",
):
    return text_turn(
        json.dumps(
            {
                "memory": memory,
                "recap": recap,
                "quote": quote,
                "open_loops": open_loops or [],
            },
            ensure_ascii=False,
        )
    )


def digest_turn(
    headline: str,
    highlights: list[str],
    closed: list[dict] | None = None,
    new_loops: list[str] | None = None,
    *,
    wrap: str = "{payload}",
    **extra: object,
):
    payload = json.dumps(
        {
            "headline": headline,
            "highlights": highlights,
            "closed_loops": closed or [],
            "new_loops": new_loops or [],
            **extra,
        },
        ensure_ascii=False,
    )
    return text_turn(wrap.format(payload=payload))


# --- JSON 解析 ---------------------------------------------------------------


def test_parse_json_object_accepts_bare_object() -> None:
    assert _parse_json_object('{"a": 1}') == {"a": 1}


def test_parse_json_object_strips_fences_and_prose() -> None:
    """提示词说了别包 ```，模型照包不误。"""
    assert _parse_json_object('好的，这是结果：\n```json\n{"a": 1}\n```\n') == {"a": 1}


def test_parse_json_object_returns_none_for_plain_text() -> None:
    assert _parse_json_object("用户说他用 uv 管理依赖") is None
    assert _parse_json_object("{ 这不是 json }") is None


def test_parse_json_object_rejects_non_object() -> None:
    """数组和标量不是我们要的形状，别让它们混过去。"""
    assert _parse_json_object("[1, 2, 3]") is None


# --- 摘要的两份产出 -----------------------------------------------------------


async def test_structured_summary_stores_both_parts(session: AsyncSession) -> None:
    await seed_conversation(session, "把语音输入接通了", "好的")
    provider = provider_with(
        [
            summary_turn("用户在做语音输入", "接通了本地 ASR，端到端跑通"),
            text_turn("整理完成"),
            digest_turn("把语音输入接通了", ["接通本地 ASR 链路"]),
        ]
    )

    result = await Consolidator(session, provider).run(TODAY)

    summary = (await session.execute(select(ConversationSummary))).scalar_one()
    assert summary.summary == "用户在做语音输入"
    assert summary.recap == "接通了本地 ASR，端到端跑通"
    assert result.headline == "把语音输入接通了"

    digest = (await session.execute(select(DailyDigest))).scalar_one()
    assert digest.day == TODAY
    assert digest.highlights == ["接通本地 ASR 链路"]


async def test_plain_text_summary_degrades_to_memory(session: AsyncSession) -> None:
    """格式没对上时，整段原文当 memory 用 —— 丢内容比丢格式糟得多。"""
    await seed_conversation(session, "我现在用 uv 管理依赖", "记住了")
    provider = provider_with(
        [
            text_turn("用户用 uv 管理 Python 依赖"),  # 老格式，不是 JSON
            tool_turn(
                "memory",
                {
                    "command": "create",
                    "path": "/memories/profile/preferences.md",
                    "file_text": "- 用 uv",
                },
            ),
            text_turn("已整理"),
        ]
    )

    result = await Consolidator(session, provider).run(TODAY)

    assert result.memory_writes == 1
    summary = (await session.execute(select(ConversationSummary))).scalar_one()
    assert summary.summary == "用户用 uv 管理 Python 依赖"
    assert summary.recap is None
    # 没有 recap 就没有回顾，也不该为此报错
    assert result.headline == ""
    assert not result.digest_failed
    assert (await session.execute(select(DailyDigest))).first() is None


async def test_recap_without_memory_still_produces_digest(session: AsyncSession) -> None:
    """纯技术活不值得进记忆，但恰恰是回顾最该记的。"""
    await seed_conversation(session, "帮我调这个正则", "改好了")
    provider = provider_with(
        [
            summary_turn("", "修好了手机号正则的贪婪匹配"),
            digest_turn("修掉了正则的贪婪匹配", ["修正手机号正则"]),
        ]
    )

    result = await Consolidator(session, provider).run(TODAY)

    assert not result.skipped
    assert result.memory_writes == 0
    assert result.headline == "修掉了正则的贪婪匹配"


# --- 回顾失败不牵连记忆 --------------------------------------------------------


async def test_digest_failure_keeps_memory_result(session: AsyncSession) -> None:
    await seed_conversation(session, "我现在用 uv", "记住了")
    provider = provider_with(
        [
            summary_turn("用户用 uv 管理依赖", "把依赖迁到了 uv"),
            tool_turn(
                "memory",
                {
                    "command": "create",
                    "path": "/memories/profile/preferences.md",
                    "file_text": "- 用 uv",
                },
            ),
            text_turn("已整理"),
            text_turn("抱歉我不太确定"),  # 回顾输出不是 JSON
            text_turn("还是不太确定"),    # 重试一次也不行
        ]
    )

    result = await Consolidator(session, provider).run(TODAY)

    assert result.digest_failed
    assert result.memory_writes == 1  # 记忆是主线，不受牵连
    assert result.headline == ""
    versions = list((await session.execute(select(MemoryVersion))).scalars())
    assert len(versions) == 1


async def test_digest_without_headline_is_a_failure(session: AsyncSession) -> None:
    """空 headline 的 digest 比没有更糟 —— 页面主角是它。"""
    await seed_conversation(session, "聊点什么", "好")
    provider = provider_with(
        [summary_turn("", "讨论了一些事"), digest_turn("", ["某条收获"])]
    )

    result = await Consolidator(session, provider).run(TODAY)

    assert result.digest_failed
    assert (await session.execute(select(DailyDigest))).first() is None


async def test_no_conversations_writes_no_digest(session: AsyncSession) -> None:
    result = await Consolidator(session, provider_with([])).run(dt.date(2020, 1, 1))
    assert result.skipped
    assert result.headline == ""
    assert (await session.execute(select(DailyDigest))).first() is None


# --- 悬而未决 ----------------------------------------------------------------


def test_attention_prompts_exclude_timeline_items() -> None:
    """有日期的安排只进时间线，不能在两个入口重复制造压力。"""
    assert "都属于 timeline，不要放进 open_loops" in SUMMARY_SYSTEM
    assert "所有应进入\ntimeline 的有日期事项" in DIGEST_PROMPT
    assert "已转入时间线" in DIGEST_PROMPT


async def test_new_loops_are_recorded(session: AsyncSession) -> None:
    await seed_conversation(session, "我待会儿要把索引校验加上", "好")
    provider = provider_with(
        [
            summary_turn("", "讨论了索引校验", ["把索引机械校验加上"]),
            digest_turn(
                "定了索引校验的做法", ["确定索引校验方案"],
                new_loops=["把索引机械校验加上"],
            ),
        ]
    )

    result = await Consolidator(session, provider).run(TODAY)

    assert result.new_loops == 1
    loop = (await session.execute(select(OpenLoop))).scalar_one()
    assert loop.text == "把索引机械校验加上"
    assert loop.status == "open"
    assert loop.opened_on == TODAY
    assert loop.actor == "consolidation"


async def test_closed_loop_is_settled(session: AsyncSession) -> None:
    yesterday = TODAY - dt.timedelta(days=1)
    session.add(OpenLoop(text="把索引校验加上", opened_on=yesterday, status="open"))
    await session.commit()
    pending = (await session.execute(select(OpenLoop))).scalar_one()

    await seed_conversation(session, "索引校验写完了", "好")
    provider = provider_with(
        [
            summary_turn("", "把索引校验实现了"),
            digest_turn(
                "补上了记忆索引的机械校验", ["实现索引校验"],
                closed=[{"id": pending.id, "note": "今天实现了"}],
            ),
        ]
    )

    result = await Consolidator(session, provider).run(TODAY)

    assert result.closed_loops == 1
    await session.refresh(pending)
    assert pending.status == "closed"
    assert pending.closed_on == TODAY
    assert pending.closed_note == "今天实现了"


async def test_unknown_closed_id_is_ignored(session: AsyncSession) -> None:
    """模型偶尔会编 id。忽略比错标安全 —— 错标是不可见的数据损坏。"""
    await seed_conversation(session, "随便聊聊", "好")
    provider = provider_with(
        [
            summary_turn("", "聊了点东西"),
            digest_turn("聊了点东西", ["某条"], closed=[{"id": 9999, "note": "x"}]),
        ]
    )

    result = await Consolidator(session, provider).run(TODAY)

    assert result.closed_loops == 0
    assert not result.digest_failed


async def test_duplicate_loop_text_is_not_readded(session: AsyncSession) -> None:
    yesterday = TODAY - dt.timedelta(days=1)
    session.add(OpenLoop(text="把索引校验加上", opened_on=yesterday, status="open"))
    await session.commit()

    await seed_conversation(session, "索引校验还没做", "嗯")
    provider = provider_with(
        [
            summary_turn("", "又提了一次索引校验"),
            digest_turn("又想起索引校验没做", ["索引校验仍未开工"],
                        new_loops=["把索引校验加上"]),
        ]
    )

    result = await Consolidator(session, provider).run(TODAY)

    assert result.new_loops == 0
    loops = list((await session.execute(select(OpenLoop))).scalars())
    assert len(loops) == 1


# --- 回顾页拿到的那一份列表 -----------------------------------------------------


async def test_open_loops_for_a_day_are_time_scoped(session: AsyncSession) -> None:
    """翻看旧日期时，不该看到那天之后才产生的待办。"""
    day = dt.date(2026, 8, 5)
    session.add_all(
        [
            OpenLoop(text="更早挂着的", opened_on=day - dt.timedelta(days=3)),
            OpenLoop(text="当天新增的", opened_on=day),
            OpenLoop(
                text="当天闭环的",
                opened_on=day - dt.timedelta(days=1),
                closed_on=day,
                status="closed",
            ),
            OpenLoop(
                text="之后才闭的",
                opened_on=day - dt.timedelta(days=2),
                closed_on=day + dt.timedelta(days=2),
                status="closed",
            ),
            OpenLoop(text="之后才出现的", opened_on=day + dt.timedelta(days=1)),
        ]
    )
    await session.commit()

    rows = await list_open_loops(day=day, session=session)
    texts = {row.text for row in rows}

    assert texts == {"更早挂着的", "当天新增的", "当天闭环的", "之后才闭的"}


async def test_open_loops_without_day_returns_only_open(session: AsyncSession) -> None:
    session.add_all(
        [
            OpenLoop(text="还挂着", opened_on=TODAY),
            OpenLoop(text="做完了", opened_on=TODAY, closed_on=TODAY, status="closed"),
            OpenLoop(text="不做了", opened_on=TODAY, closed_on=TODAY, status="dropped"),
        ]
    )
    await session.commit()

    rows = await list_open_loops(session=session)

    assert [row.text for row in rows] == ["还挂着"]


async def test_rerunning_overwrites_the_digest(session: AsyncSession) -> None:
    """一天一行：重跑是覆盖，不堆历史。"""
    await seed_conversation(session, "第一件事", "好")
    first = provider_with(
        [summary_turn("", "做了第一件事"), digest_turn("第一版标题", ["A"])]
    )
    await Consolidator(session, first).run(TODAY)

    await seed_conversation(session, "第二件事", "好")
    second = provider_with(
        [summary_turn("", "做了第二件事"), digest_turn("第二版标题", ["B"])]
    )
    await Consolidator(session, second).run(TODAY)

    digest = (await session.execute(select(DailyDigest))).scalar_one()
    assert digest.headline == "第二版标题"
    assert digest.highlights == ["B"]


# --- 叙事字段：这是「哪一天」，不是「做了什么」 --------------------------------


async def test_narrative_fields_are_stored(session: AsyncSession) -> None:
    await seed_conversation(session, "台风夜想给她煮碗虾滑", "好")
    provider = provider_with([
        summary_turn("", "写了台风夜的故事", quote="想回家给她煮一碗"),
        digest_turn(
            "写了两个关于深夜和陪伴的故事",
            ["写了台风夜煮虾滑的短篇"],
            title="台风夜煮虾滑的那天",
            observation="你今天两次让我写关于深夜和陪伴的故事。",
            quote="想回家给她煮一碗",
            echoes=[{"kind": "recurring", "text": "这周第 2 次写夜里的故事"}],
        ),
    ])

    result = await Consolidator(session, provider).run(TODAY)

    digest = (await session.execute(select(DailyDigest))).scalar_one()
    assert digest.title == "台风夜煮虾滑的那天"
    assert digest.observation.startswith("你今天两次")
    assert digest.quote == "想回家给她煮一碗"
    assert digest.echoes == [{"kind": "recurring", "text": "这周第 2 次写夜里的故事"}]
    assert result.title == "台风夜煮虾滑的那天"


async def test_quote_is_stored_on_the_summary(session: AsyncSession) -> None:
    """引语必须在摘要那步抽 —— 只有那一步看得到原始对话。"""
    await seed_conversation(session, "我觉得这个功能挺没意思的", "嗯")
    provider = provider_with([
        summary_turn("", "聊了回顾功能", quote="我觉得这个功能挺没意思的"),
        digest_turn("聊了回顾功能", ["讨论回顾的意义"]),
    ])

    await Consolidator(session, provider).run(TODAY)

    summary = (await session.execute(select(ConversationSummary))).scalar_one()
    assert summary.quote == "我觉得这个功能挺没意思的"


async def test_invented_quote_is_replaced_by_the_real_one(session: AsyncSession) -> None:
    """润色过的引语就不再是他说的话了。丢掉改写版，退回候选里的原文。"""
    await seed_conversation(session, "我觉得这个功能挺没意思的", "嗯")
    provider = provider_with([
        summary_turn("", "聊了回顾功能", quote="我觉得这个功能挺没意思的"),
        digest_turn("聊了回顾功能", ["讨论回顾"], quote="我认为这个功能缺乏意义"),
    ])

    await Consolidator(session, provider).run(TODAY)

    digest = (await session.execute(select(DailyDigest))).scalar_one()
    assert digest.quote == "我觉得这个功能挺没意思的"


async def test_quote_survives_decorative_brackets(session: AsyncSession) -> None:
    """候选是带「」喂进去的，模型常连引号一起抄回来。"""
    await seed_conversation(session, "想回家给她煮一碗", "好")
    provider = provider_with([
        summary_turn("", "写了故事", quote="想回家给她煮一碗"),
        digest_turn("写了故事", ["写了短篇"], quote="「想回家给她煮一碗」"),
    ])

    await Consolidator(session, provider).run(TODAY)

    digest = (await session.execute(select(DailyDigest))).scalar_one()
    assert digest.quote == "想回家给她煮一碗"


async def test_highlights_are_capped_at_five(session: AsyncSession) -> None:
    await seed_conversation(session, "干了很多事", "好")
    provider = provider_with([
        summary_turn("", "干了很多事"),
        digest_turn("忙碌的一天", [f"第 {i} 件" for i in range(1, 9)]),
    ])

    await Consolidator(session, provider).run(TODAY)

    digest = (await session.execute(select(DailyDigest))).scalar_one()
    assert len(digest.highlights) == 5


async def test_malformed_echoes_are_filtered(session: AsyncSession) -> None:
    """形状不对的丢掉，kind 认不出的归 recurring —— 内容才是有价值的部分。"""
    await seed_conversation(session, "聊天", "好")
    provider = provider_with([
        summary_turn("", "聊了些事"),
        digest_turn(
            "聊了些事", ["聊天"],
            echoes=[
                "这是个字符串不是对象",
                {"kind": "recurring", "text": ""},
                {"kind": "什么鬼", "text": "第 3 次聊到记忆"},
                {"kind": "followup", "text": "8/05 说的事今天做了"},
                {"kind": "recurring", "text": "多余的第四条"},
                {"kind": "recurring", "text": "多余的第五条"},
            ],
        ),
    ])

    await Consolidator(session, provider).run(TODAY)

    digest = (await session.execute(select(DailyDigest))).scalar_one()
    assert digest.echoes == [
        {"kind": "recurring", "text": "第 3 次聊到记忆"},
        {"kind": "followup", "text": "8/05 说的事今天做了"},
        {"kind": "recurring", "text": "多余的第四条"},
    ]


async def test_history_is_fed_to_the_digest(session: AsyncSession) -> None:
    """echoes 只能引用真实存在的历史，所以历史必须真的进提示词。"""
    session.add(DailyDigest(
        day=TODAY - dt.timedelta(days=2),
        headline="把标题生成挪到便宜模型",
        highlights=["换掉标题模型"],
    ))
    await session.commit()

    await seed_conversation(session, "今天干了活", "好")
    provider = provider_with([
        summary_turn("", "干了活"),
        digest_turn("干了活", ["干活"]),
    ])
    await Consolidator(session, provider).run(TODAY)

    digest_prompt = provider.client.messages.calls[-1]["messages"][0]["content"]
    assert "把标题生成挪到便宜模型" in digest_prompt


async def test_history_absent_tells_model_not_to_invent(session: AsyncSession) -> None:
    await seed_conversation(session, "第一天", "好")
    provider = provider_with([summary_turn("", "第一天"), digest_turn("第一天", ["开始"])])

    await Consolidator(session, provider).run(TODAY)

    digest_prompt = provider.client.messages.calls[-1]["messages"][0]["content"]
    assert "不要写 echoes" in digest_prompt


async def test_recap_prompt_covers_life_not_only_work(session: AsyncSession) -> None:
    """取材口径只写「技术进展」时，私人助手大半对话会留空，回顾就没素材了。"""
    assert "他告诉你的关于他自己" in SUMMARY_SYSTEM
    assert "不是「这段有没有技术含量」" in SUMMARY_SYSTEM


async def test_digest_prompt_has_no_highlight_floor() -> None:
    """写死「3 到 5 条」会逼模型把一件事拆成三条来凑数。"""
    assert "最多 5 条" in DIGEST_PROMPT
    assert "3 到 5 条" not in DIGEST_PROMPT


# --- 重跑 ---------------------------------------------------------------------


async def test_rerun_keeps_the_full_day_material(session: AsyncSession) -> None:
    """摘要是增量的，重跑时大部分会话没有新消息、不产出 take。

    只喂本次 take 的话，越点「重新整理」digest 的素材越少，一次比一次差 ——
    这正是回顾质量看起来在退化的原因。
    """
    await seed_conversation(session, "写个台风夜的故事", "好")
    first = provider_with([
        summary_turn("", "写了台风夜煮虾滑的短篇", quote="想回家给她煮一碗"),
        digest_turn("写了个故事", ["写了台风夜的短篇"]),
    ])
    await Consolidator(session, first).run(TODAY)

    # 第二天……不，同一天再点一次「重新整理」，且这期间只有另一个会话有新消息
    await seed_conversation(session, "顺手修个正则", "改好了")
    second = provider_with([
        summary_turn("", "修好了手机号正则"),
        digest_turn("写了故事也修了正则", ["写了短篇", "修好正则"]),
    ])
    await Consolidator(session, second).run(TODAY)

    digest_prompt = second.client.messages.calls[-1]["messages"][0]["content"]
    # 上一轮的会话没有新消息、不会重新摘要，但它的素材必须还在
    assert "台风夜煮虾滑的短篇" in digest_prompt
    assert "想回家给她煮一碗" in digest_prompt
    assert "修好了手机号正则" in digest_prompt


async def test_rerun_with_nothing_new_still_regenerates(session: AsyncSession) -> None:
    """全天都已摘要过时，「重新整理」也该真的重写一份回顾，而不是静默跳过。"""
    await seed_conversation(session, "写个故事", "好")
    first = provider_with([
        summary_turn("", "写了个短篇"),
        digest_turn("写了个故事", ["写了短篇"], title="第一版"),
    ])
    await Consolidator(session, first).run(TODAY)

    second = provider_with([digest_turn("重写过的标题", ["重写的收获"], title="第二版")])
    result = await Consolidator(session, second).run(TODAY)

    assert not result.skipped
    digest = (await session.execute(select(DailyDigest))).scalar_one()
    assert digest.title == "第二版"
    assert digest.headline == "重写过的标题"


async def test_digest_retries_once_on_junk_output(session: AsyncSession) -> None:
    """模型偶发返回空正文。一天只跑一次，为一次抽风丢掉整份回顾不值得。"""
    await seed_conversation(session, "干了点活", "好")
    provider = provider_with([
        summary_turn("", "干了点活"),
        text_turn(""),  # 第一次：空
        digest_turn("干了点活", ["干活"]),  # 重试成功
    ])

    result = await Consolidator(session, provider).run(TODAY)

    assert not result.digest_failed
    assert result.headline == "干了点活"


async def test_quote_falls_back_to_a_candidate(session: AsyncSession) -> None:
    """模型在这个字段上极度保守，有候选也常返回空。候选已筛过，别整栏空着。"""
    await seed_conversation(session, "你这个效果不太好呀", "抱歉")
    provider = provider_with([
        summary_turn("", "他说效果不好", quote="你这个效果不太好呀"),
        digest_turn("聊了效果", ["讨论效果"], quote=""),
    ])

    await Consolidator(session, provider).run(TODAY)

    digest = (await session.execute(select(DailyDigest))).scalar_one()
    assert digest.quote == "你这个效果不太好呀"


async def test_quote_stays_empty_without_candidates(session: AsyncSession) -> None:
    """一条候选都没有时不能凭空造 —— 兜底只在候选里挑。"""
    await seed_conversation(session, "帮我改个配置", "改好了")
    provider = provider_with([
        summary_turn("", "改了配置"),
        digest_turn("改了配置", ["改配置"]),
    ])

    await Consolidator(session, provider).run(TODAY)

    digest = (await session.execute(select(DailyDigest))).scalar_one()
    assert digest.quote == ""
