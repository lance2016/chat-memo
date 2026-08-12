from __future__ import annotations

from types import SimpleNamespace
from typing import Any, ClassVar

from openai import AsyncOpenAI

from app.config import Settings
from app.llm.events import AssistantTurn, TextDelta, ToolResult, ToolUse
from app.llm.openai_responses_provider import OpenAIResponsesProvider
from app.llm.target import DEFAULT_CAPABILITIES, ModelTarget


class FakeStream:
    def __init__(self, events: list[Any]) -> None:
        self.events = events

    def __aiter__(self):
        return self._events()

    async def _events(self):
        for event in self.events:
            yield event


class FakeResponses:
    def __init__(self, events: list[Any], response: Any | None = None) -> None:
        self.events = events
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return FakeStream(self.events)
        return self.response


def target() -> ModelTarget:
    return ModelTarget(
        protocol="openai_responses",
        model_id="gpt-5.6-luna",
        display_name="Luna",
        base_url="http://127.0.0.1:18080/v1",
        api_key="dummy",
        capabilities={**DEFAULT_CAPABILITIES, "thinking": True, "vision": True},
        max_tokens=1234,
        effort="low",
        thinking_default=True,
    )


def test_openai_base_url_temporarily_becomes_default_provider() -> None:
    automatic = ModelTarget.from_settings(
        Settings(openai_base_url="http://127.0.0.1:18080/v1")
    )
    explicit_deepseek = ModelTarget.from_settings(
        Settings(
            provider="deepseek",
            openai_base_url="http://127.0.0.1:18080/v1",
            deepseek_api_key="test",
        )
    )

    assert automatic.protocol == "openai_responses"
    assert automatic.model_id == "gpt-5.6-luna"
    assert explicit_deepseek.protocol == "openai_compatible"


async def drain(provider: OpenAIResponsesProvider, **kwargs: Any) -> list[Any]:
    events = []
    async for event in provider.run(
        system="system prompt",
        messages=[{"role": "user", "content": [{"type": "text", "text": "Hello"}]}],
        **kwargs,
    ):
        events.append(event)
    return events


async def test_responses_stream_request_and_text() -> None:
    fake = FakeResponses(
        [
            SimpleNamespace(type="response.output_text.delta", delta="Hello"),
            SimpleNamespace(type="response.completed", response=SimpleNamespace(
                usage=SimpleNamespace(model_dump=lambda **_: {"input_tokens": 2}),
                output=[],
            )),
        ]
    )
    client = AsyncOpenAI(api_key="dummy", base_url="http://localhost")
    client.responses.create = fake.create  # type: ignore[method-assign]
    provider = OpenAIResponsesProvider(
        settings=Settings(max_tool_iterations=2), client=client, target=target()
    )

    events = await drain(provider)

    assert [event.text for event in events if isinstance(event, TextDelta)] == ["Hello"]
    request = fake.calls[0]
    assert request["instructions"] == "system prompt"
    assert request["model"] == "gpt-5.6-luna"
    assert request["max_output_tokens"] == 1234
    assert request["store"] is False
    assert request["reasoning"] == {"effort": "low"}
    assert request["input"][0]["content"] == [{"type": "input_text", "text": "Hello"}]


async def test_responses_function_call_round_trip() -> None:
    first = [
        SimpleNamespace(
            type="response.output_item.added",
            item=SimpleNamespace(
                type="function_call", id="fc_1", call_id="call_1", name="memory", arguments=""
            ),
        ),
        SimpleNamespace(
            type="response.function_call_arguments.delta", item_id="fc_1", delta='{"command":"view"}'
        ),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                usage=None,
                output=[SimpleNamespace(
                    type="function_call", id="fc_1", call_id="call_1", name="memory", arguments='{"command":"view"}'
                )],
            ),
        ),
    ]
    second = [
        SimpleNamespace(type="response.output_text.delta", delta="Done"),
        SimpleNamespace(type="response.completed", response=SimpleNamespace(usage=None, output=[])),
    ]

    class Executor:
        openai_definitions: ClassVar[list[dict[str, Any]]] = [
            {
                "type": "function",
                "function": {
                    "name": "memory",
                    "description": "Read memory",
                    "parameters": {"type": "object"},
                },
            }
        ]

        async def execute(self, name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
            assert name == "memory"
            assert tool_input == {"command": "view"}
            return "memory contents", False

    fake = FakeResponses(first)
    original_create = fake.create
    call_count = 0

    async def create(**kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        fake.calls.append(kwargs)
        return FakeStream(first if call_count == 1 else second)

    client = AsyncOpenAI(api_key="dummy", base_url="http://localhost")
    client.responses.create = create  # type: ignore[method-assign]
    provider = OpenAIResponsesProvider(
        settings=Settings(max_tool_iterations=2), client=client, target=target()
    )

    events = await drain(provider, executor=Executor())

    assert any(isinstance(event, ToolUse) for event in events)
    assert any(isinstance(event, ToolResult) for event in events)
    assert any(
        isinstance(event, AssistantTurn)
        and any(block.get("text") == "Done" for block in event.content)
        for event in events
    )
    assert fake.calls[1]["input"][2]["type"] == "function_call_output"
    assert fake.calls[1]["input"][2]["call_id"] == "call_1"
    assert original_create is not None
