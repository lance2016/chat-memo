"""DeepSeek 的分片必须边收边发。

上游本来就是流式的，provider 却一度先 ``_consume()`` 攒完整轮再统一 yield ——
SSE、前端都没问题，用户看到的却是「转圈很久，然后整段蹦出来」。
这里锁住的就是「第一个分片到达时上游还没读完」这个时序，
只断言内容相等是抓不到伪流式的。
"""

from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from app.config import Settings
from app.llm.deepseek_provider import DeepSeekProvider
from app.llm.events import AssistantTurn, TextDelta, ThinkingDelta, ToolUse
from app.memory.tool import MemoryToolExecutor


@dataclass
class FakeFunction:
    name: str | None = None
    arguments: str | None = None


@dataclass
class FakeToolCall:
    index: int
    id: str | None = None
    function: FakeFunction = field(default_factory=FakeFunction)


@dataclass
class FakeDelta:
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[FakeToolCall] | None = None


@dataclass
class FakeChoice:
    delta: FakeDelta | None = None
    finish_reason: str | None = None


@dataclass
class FakeUsage:
    prompt_tokens: int = 7
    completion_tokens: int = 3

    def model_dump(self, exclude_none: bool = False) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


@dataclass
class FakeChunk:
    choices: list[FakeChoice] = field(default_factory=list)
    usage: FakeUsage | None = None


def chunk(
    *,
    text: str | None = None,
    reasoning: str | None = None,
    tool_calls: list[FakeToolCall] | None = None,
    finish: str | None = None,
) -> FakeChunk:
    return FakeChunk(
        choices=[
            FakeChoice(
                delta=FakeDelta(
                    content=text, reasoning_content=reasoning, tool_calls=tool_calls
                ),
                finish_reason=finish,
            )
        ]
    )


class ScriptedCompletions:
    """按脚本吐分片，并记下已经吐了几个 —— 消费方靠它判断有没有攒批。"""

    def __init__(self, *rounds: list[FakeChunk]) -> None:
        self._rounds = list(rounds)
        self.produced = 0

    async def create(self, **kwargs: Any) -> Any:
        chunks = self._rounds.pop(0)

        async def stream() -> Any:
            for item in chunks:
                self.produced += 1
                yield item

        return stream()


def provider_for(*rounds: list[FakeChunk]) -> tuple[DeepSeekProvider, ScriptedCompletions]:
    completions = ScriptedCompletions(*rounds)
    client = AsyncOpenAI(api_key="test", base_url="http://localhost")
    client.chat.completions = completions  # type: ignore[assignment]
    provider = DeepSeekProvider(
        settings=Settings(deepseek_api_key="test"), client=client
    )
    return provider, completions


async def collect(provider: DeepSeekProvider, **kwargs: Any) -> list[Any]:
    return [
        event
        async for event in provider.run(
            system="sys",
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            **kwargs,
        )
    ]


async def test_text_deltas_arrive_before_upstream_finishes() -> None:
    chunks = [chunk(text=t) for t in ("你", "好", "呀")]
    chunks.append(chunk(finish="stop"))
    provider, completions = provider_for(chunks)

    seen: list[tuple[str, int]] = []
    async for event in provider.run(
        system="sys",
        messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
    ):
        if isinstance(event, TextDelta):
            seen.append((event.text, completions.produced))

    # 第 n 个分片必须在上游刚吐出第 n 个时就到手，攒批的话这里全是 4。
    assert seen == [("你", 1), ("好", 2), ("呀", 3)]


async def test_thinking_deltas_stream_too() -> None:
    provider, completions = provider_for(
        [chunk(reasoning="想"), chunk(text="答"), chunk(finish="stop")]
    )

    seen: list[tuple[type, int]] = []
    async for event in provider.run(
        system="sys",
        messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
    ):
        if isinstance(event, ThinkingDelta | TextDelta):
            seen.append((type(event), completions.produced))

    assert seen == [(ThinkingDelta, 1), (TextDelta, 2)]


async def test_full_text_still_lands_in_assistant_turn() -> None:
    """分片发出去了，落库用的完整内容一份都不能少。"""
    provider, _ = provider_for(
        [
            chunk(reasoning="推"),
            chunk(reasoning="理"),
            chunk(text="回"),
            chunk(text="答"),
            FakeChunk(choices=[FakeChoice(finish_reason="stop")], usage=FakeUsage()),
        ]
    )

    events = await collect(provider)
    turn = next(e for e in events if isinstance(e, AssistantTurn))

    assert turn.content == [
        {"type": "thinking", "thinking": "推理"},
        {"type": "text", "text": "回答"},
    ]
    assert turn.stop_reason == "stop"
    assert turn.usage == {"prompt_tokens": 7, "completion_tokens": 3}


async def test_tool_arguments_are_assembled_before_execution(tmp_path: Any) -> None:
    """arguments 是逐片下发的 JSON，必须攒齐再解析 —— 这部分不能跟着流。"""
    from app.memory.store import MemoryStore

    pieces = ['{"command"', ': "view", ', '"path": "/memories"}']
    first = [
        chunk(
            tool_calls=[
                FakeToolCall(index=0, id="call_1", function=FakeFunction(name="memory"))
            ]
        )
    ]
    first += [
        chunk(tool_calls=[FakeToolCall(index=0, function=FakeFunction(arguments=p))])
        for p in pieces
    ]
    first.append(chunk(finish="tool_calls"))
    second = [chunk(text="看完了"), chunk(finish="stop")]

    provider, _ = provider_for(first, second)
    executor = MemoryToolExecutor(MemoryStore(tmp_path))

    events = await collect(provider, executor=executor)
    use = next(e for e in events if isinstance(e, ToolUse))

    assert use.name == "memory"
    assert use.input == {"command": "view", "path": "/memories"}
