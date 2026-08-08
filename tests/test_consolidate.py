import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Conversation, ConversationSummary, MemoryVersion, Message
from app.jobs.consolidate import Consolidator
from app.llm.anthropic_provider import AnthropicProvider
from app.memory.store import MemoryStore
from tests.fakes import FakeAnthropic, text_turn, tool_turn

TODAY = dt.date.today()


def provider_with(turns: list) -> AnthropicProvider:
    return AnthropicProvider(
        settings=Settings(anthropic_api_key="test"), client=FakeAnthropic(turns)
    )


async def seed_conversation(session: AsyncSession, *texts: str) -> Conversation:
    conversation = Conversation(title="测试会话")
    session.add(conversation)
    await session.flush()
    for i, text in enumerate(texts):
        session.add(
            Message(
                conversation_id=conversation.id,
                role="user" if i % 2 == 0 else "assistant",
                content=[{"type": "text", "text": text}],
            )
        )
    await session.commit()
    return conversation


async def test_no_conversations_is_skipped(session: AsyncSession) -> None:
    result = await Consolidator(session, provider_with([])).run(dt.date(2020, 1, 1))
    assert result.skipped
    assert result.tool_calls == 0


async def test_summarizes_and_writes_memory(session: AsyncSession) -> None:
    await seed_conversation(session, "我现在用 uv 管理依赖", "记住了")

    provider = provider_with(
        [
            text_turn("用户说他用 uv 管理 Python 依赖"),  # 摘要
            tool_turn(
                "memory",
                {
                    "command": "create",
                    "path": "/memories/profile/preferences.md",
                    "file_text": "- 用 uv 管理 Python 依赖",
                },
            ),
            text_turn("已整理"),
        ]
    )

    result = await Consolidator(session, provider).run(TODAY)

    assert not result.skipped
    assert result.memory_writes == 1
    assert result.failed_summaries == 0

    store = MemoryStore(session)
    assert "uv" in await store.view("/memories/profile/preferences.md")

    # 版本记录的 actor 要能区分出这是整理任务写的
    versions = list((await session.execute(select(MemoryVersion))).scalars())
    assert {v.actor for v in versions} == {"consolidation"}


async def test_summary_watermark_prevents_reprocessing(session: AsyncSession) -> None:
    await seed_conversation(session, "第一句", "回应")

    first = provider_with([text_turn("摘要一"), text_turn("整理完成")])
    await Consolidator(session, first).run(TODAY)

    summaries = list(
        (await session.execute(select(ConversationSummary))).scalars()
    )
    assert len(summaries) == 1

    # 没有新消息 → 第二次跑不该再生成摘要
    second = provider_with([])
    result = await Consolidator(session, second).run(TODAY)
    assert result.skipped
    assert len(second.client.messages.calls) == 0


async def test_summary_of_none_is_dropped(session: AsyncSession) -> None:
    """模型回"无"表示这段对话没有值得记的内容。"""
    await seed_conversation(session, "帮我写个正则", "好的")
    provider = provider_with([text_turn("无")])

    result = await Consolidator(session, provider).run(TODAY)
    assert result.skipped
    assert len(provider.client.messages.calls) == 1  # 只调了摘要，没进整理


async def test_summary_failure_is_reported(session: AsyncSession) -> None:
    await seed_conversation(session, "一些内容", "回应")
    provider = provider_with([])  # 摘要调用直接抛异常

    result = await Consolidator(session, provider).run(TODAY)

    assert result.skipped
    assert result.failed_summaries == 1
    assert "失败" in result.detail


async def test_transcript_ignores_thinking_and_tool_blocks(
    session: AsyncSession,
) -> None:
    conversation = Conversation(title="带工具的会话")
    session.add(conversation)
    await session.flush()
    session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                role="user",
                content=[{"type": "text", "text": "记住我住在示例市"}],
            ),
            Message(
                conversation_id=conversation.id,
                role="assistant",
                content=[
                    {"type": "thinking", "thinking": "内部推理不该进摘要", "signature": "s"},
                    {"type": "tool_use", "id": "t1", "name": "memory", "input": {}},
                ],
            ),
        ]
    )
    await session.commit()

    provider = provider_with([text_turn("用户住在示例市"), text_turn("整理完成")])
    await Consolidator(session, provider).run(TODAY)

    transcript = provider.client.messages.calls[0]["messages"][0]["content"]
    assert "示例市" in transcript
    assert "内部推理" not in transcript


async def test_self_check_reports_index_drift(session: AsyncSession) -> None:
    """整理跑完要能说出「索引和实际记忆对不上」——否则漏索引是彻底静默的。"""
    await seed_conversation(session, "我在做一个聊天项目", "记住了")
    await MemoryStore(session, actor="manual").create("/memories/MEMORY.md", "# 记忆索引")

    provider = provider_with(
        [
            text_turn("用户在做一个聊天项目"),
            tool_turn(
                "memory",
                {
                    "command": "create",
                    "path": "/memories/projects/chat.md",
                    "file_text": "- 个人 AI 助手项目",
                },
            ),
            # 模型写了记忆文件却没更新索引 —— 这正是要抓的那种失败
            text_turn("已整理"),
        ]
    )

    result = await Consolidator(session, provider).run(TODAY)

    assert result.memory_writes == 1
    assert result.index_issues == 1
    assert "没进索引" in result.index_report


async def test_self_check_passes_when_index_matches(session: AsyncSession) -> None:
    await seed_conversation(session, "我在做一个聊天项目", "记住了")
    store = MemoryStore(session, actor="manual")
    await store.create("/memories/projects/chat.md", "- 个人 AI 助手项目")
    await store.create(
        "/memories/MEMORY.md", "- [聊天项目](projects/chat.md) — 项目目标与进展"
    )

    provider = provider_with([text_turn("用户在做一个聊天项目"), text_turn("无需改动")])
    result = await Consolidator(session, provider).run(TODAY)

    assert result.index_issues == 0
    assert "通过" in result.index_report


async def test_known_index_issues_enter_the_next_prompt(
    session: AsyncSession,
) -> None:
    """反馈回路：代码发现的问题必须传给模型，它自己看不到实际有哪些记忆文件。"""
    await seed_conversation(session, "我在做一个聊天项目", "记住了")
    store = MemoryStore(session, actor="manual")
    await store.create("/memories/projects/chat.md", "- 个人 AI 助手项目")
    await store.create("/memories/MEMORY.md", "# 记忆索引")  # 条目缺失

    provider = provider_with([text_turn("用户在做一个聊天项目"), text_turn("已整理")])
    await Consolidator(session, provider).run(TODAY)

    # calls[0] 是摘要，calls[1] 才是整理
    prompt = provider.client.messages.calls[1]["messages"][0]["content"][0]["text"]
    assert "/memories/projects/chat.md" in prompt
    assert "缺条目" in prompt


async def test_clean_index_adds_nothing_to_the_prompt(session: AsyncSession) -> None:
    """没问题时整段不出现 —— 每轮塞一句「本次无问题」是纯粹的 token 浪费。"""
    await seed_conversation(session, "我在做一个聊天项目", "记住了")

    provider = provider_with([text_turn("用户在做一个聊天项目"), text_turn("已整理")])
    await Consolidator(session, provider).run(TODAY)

    prompt = provider.client.messages.calls[1]["messages"][0]["content"][0]["text"]
    assert "索引问题" not in prompt and "缺条目" not in prompt


async def test_a_run_is_recorded_so_it_is_not_redone(session: AsyncSession) -> None:
    """整理跑完要在 consolidation_runs 留一笔。

    补跑逻辑的判据就是这张表；不记的话每十分钟会重跑同一天。
    手动触发也走同一条路径，所以手动补过的日子不会被自动再跑一遍。
    """
    from app.jobs import backfill

    await seed_conversation(session, "我在做一个聊天项目", "记住了")
    provider = provider_with([text_turn("用户在做一个聊天项目"), text_turn("已整理")])

    result = await Consolidator(session, provider).run(TODAY)

    runs = await backfill.recent_runs(session)
    assert len(runs) == 1
    assert runs[0].day == TODAY
    assert runs[0].status == ("skipped" if result.skipped else "ok")
    assert runs[0].seconds >= 0


async def test_an_errored_agent_loop_is_recorded_as_failed(
    session: AsyncSession,
) -> None:
    """**agent loop 出错时不抛异常** —— 它把消息塞进 detail 然后正常返回。

    只看异常的话这种情况会被记成 ok，补跑逻辑再也不碰这一天，那天的记忆就此
    永久缺失，而且完全静默。这正是这张表要防的东西。
    """
    from app.jobs import backfill

    await seed_conversation(session, "会失败的一天", "好")
    provider = provider_with([text_turn("摘要")])  # 整理那轮没有预设，agent loop 报错

    result = await Consolidator(session, provider).run(TODAY)

    assert not result.skipped and result.detail  # 没抛，但确实出错了
    runs = await backfill.recent_runs(session)
    assert len(runs) == 1 and runs[0].status == "failed"
    assert runs[0].detail


async def test_partial_input_loss_is_not_recorded_as_success(
    session: AsyncSession,
) -> None:
    """摘要失败 = 模型压根没看到那段对话。

    这天记出来的东西是残缺的，不该被当成「整理过了」而不再重跑。
    """
    from app.jobs import backfill

    await seed_conversation(session, "一些内容", "回应")
    await Consolidator(session, provider_with([])).run(TODAY)  # 摘要直接抛

    runs = await backfill.recent_runs(session)
    assert runs[0].status == "failed"
    assert "输入不完整" in runs[0].detail
