import httpx

from app.web_search import WebSearchToolExecutor


async def test_tavily_search_posts_only_expected_search_options(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "results": [
                    {
                        "title": "官方页面",
                        "url": "https://example.com/docs",
                        "content": "这是搜索摘要。",
                        "published_date": "2026-08-09",
                    }
                ]
            }

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, url: str, *, json: dict):
            calls.append({"url": url, "json": json})
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    result, is_error = await WebSearchToolExecutor(
        "tvly-secret", "https://api.tavily.com"
    ).execute(
        "web_search",
        {"query": "最新文档", "topic": "news", "time_range": "week", "max_results": 99},
    )

    assert not is_error
    assert "https://example.com/docs" in result
    assert calls == [{
        "url": "https://api.tavily.com/search",
        "json": {
            "api_key": "tvly-secret",
            "query": "最新文档",
            "topic": "news",
            "search_depth": "basic",
            "max_results": 8,
            "include_answer": False,
            "include_raw_content": False,
            "time_range": "week",
        },
    }]


async def test_tavily_http_errors_become_tool_errors(monkeypatch) -> None:
    request = httpx.Request("POST", "https://api.tavily.com/search")

    class FakeResponse:
        status_code = 401

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError("unauthorized", request=request, response=self)

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    result, is_error = await WebSearchToolExecutor("bad-key", "https://api.tavily.com").execute(
        "web_search", {"query": "test"}
    )

    assert is_error
    assert "API Key 无效" in result
