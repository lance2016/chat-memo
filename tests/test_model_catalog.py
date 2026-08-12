from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.main import create_app


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_catalog_exposes_builtin_services_and_profiles(
    client: AsyncClient,
) -> None:
    response = await client.get("/api/models")

    assert response.status_code == 200
    body = response.json()
    assert {service["slug"] for service in body["services"]} >= {"anthropic", "deepseek"}
    assert {profile["service_slug"] for profile in body["profiles"]} >= {
        "anthropic",
        "deepseek",
    }
    # 断言的是**值**没泄露，不是变量名 —— reason 里会写「未配置 ANTHROPIC_API_KEY」，
    # 那正是要告诉人的信息。原来匹配 "api_key" 子串会把它一起判成泄露。
    assert all("sk-" not in str(profile) for profile in body["profiles"])
    anthropic = next(
        profile for profile in body["profiles"] if profile["service_slug"] == "anthropic"
    )
    deepseek = next(
        profile for profile in body["profiles"] if profile["service_slug"] == "deepseek"
    )
    assert anthropic["thinking_efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert anthropic["thinking_effort_default"] in anthropic["thinking_efforts"]
    assert isinstance(anthropic["thinking_default"], bool)
    # DeepSeek documents a service-specific Chat Completions dialect.  It must
    # not be confused with the generic OpenAI-compatible protocol default.
    assert deepseek["capabilities"]["thinking"] is True
    assert deepseek["thinking_efforts"] == ["low", "high", "max"]
    assert deepseek["thinking_effort_default"] == "high"


async def test_builtin_openai_catalog_contains_clipproxy_models(
    client: AsyncClient,
) -> None:
    body = (await client.get("/api/models")).json()
    profiles = [
        profile
        for profile in body["profiles"]
        if profile["service_slug"] == "openai-codex"
    ]

    assert {profile["model_id"] for profile in profiles} >= {
        "gpt-5.4",
        "gpt-5.6-luna",
        "codex-auto-review",
        "gpt-image-1.5",
        "gpt-5.4-mini",
        "gpt-5.5",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-image-2",
        "gpt-5.3-codex-spark",
    }
    luna = next(profile for profile in profiles if profile["model_id"] == "gpt-5.6-luna")
    openai_service = next(
        service for service in body["services"] if service["slug"] == "openai-codex"
    )
    if openai_service["enabled"]:
        assert luna["is_default"] is True
    for model_id in ("gpt-image-1.5", "gpt-image-2"):
        image_model = next(profile for profile in profiles if profile["model_id"] == model_id)
        assert image_model["available"] is False
        assert image_model["capabilities"]["tool_calling"] is False


async def test_can_add_service_and_model_profile(client: AsyncClient) -> None:
    service_response = await client.post(
        "/api/models/services",
        json={
            "name": "本地 OpenAI 兼容服务",
            "slug": "local-test",
            "protocol": "openai_compatible",
            "base_url": "http://localhost:9000/v1",
        },
    )
    assert service_response.status_code == 201
    service = next(
        item for item in service_response.json()["services"] if item["slug"] == "local-test"
    )

    profile_response = await client.post(
        "/api/models/profiles",
        json={
            "service_id": service["id"],
            "model_id": "qwen-test",
            "display_name": "本地 Qwen 测试",
            "capabilities": {"thinking": True},
        },
    )
    assert profile_response.status_code == 201
    profile = next(
        item
        for item in profile_response.json()["profiles"]
        if item["model_id"] == "qwen-test"
    )
    assert profile["available"] is True
    assert profile["service_name"] == "本地 OpenAI 兼容服务"
    # Do not leak DeepSeek's request dialect to arbitrary compatible services.
    assert profile["thinking_efforts"] == []
    assert profile["thinking_effort_default"] is None

    default_response = await client.post(
        "/api/models/default",
        json={"purpose": "chat", "profile_id": profile["id"]},
    )
    assert default_response.status_code == 200
    assert default_response.json()["default_profile_id"] == profile["id"]

    title_default_response = await client.post(
        "/api/models/default",
        json={"purpose": "title", "profile_id": profile["id"]},
    )
    assert title_default_response.status_code == 200
    assert title_default_response.json()["default_profile_id"] == profile["id"]


async def test_profile_can_narrow_reasoning_efforts(client: AsyncClient) -> None:
    service_response = await client.post(
        "/api/models/services",
        json={
            "name": "Responses subset",
            "slug": "responses-subset",
            "protocol": "openai_responses",
            "base_url": "http://localhost:9999/v1",
        },
    )
    service_id = next(
        item["id"]
        for item in service_response.json()["services"]
        if item["slug"] == "responses-subset"
    )
    created = await client.post(
        "/api/models/profiles",
        json={
            "service_id": service_id,
            "model_id": "reasoning-subset",
            "thinking_efforts": ["low", "high"],
            "thinking_effort_default": "high",
        },
    )
    assert created.status_code == 201
    profile = next(
        item
        for item in created.json()["profiles"]
        if item["model_id"] == "reasoning-subset"
    )
    assert profile["thinking_efforts"] == ["low", "high"]
    assert profile["thinking_effort_default"] == "high"

    # Narrowing the list cannot leave a stale, invalid default behind.
    invalid = await client.patch(
        f"/api/models/profiles/{profile['id']}",
        json={"thinking_efforts": ["low"]},
    )
    assert invalid.status_code == 400
    assert "默认思考深度" in invalid.json()["detail"]

    updated = await client.patch(
        f"/api/models/profiles/{profile['id']}",
        json={
            "thinking_efforts": ["low"],
            "thinking_effort_default": "low",
        },
    )
    assert updated.status_code == 200
    narrowed = next(
        item
        for item in updated.json()["profiles"]
        if item["id"] == profile["id"]
    )
    assert narrowed["thinking_efforts"] == ["low"]
    assert narrowed["thinking_effort_default"] == "low"


async def test_credential_value_never_appears_in_catalog(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MODEL_CATALOG_TEST_KEY", "super-secret-value")
    service_response = await client.post(
        "/api/models/services",
        json={
            "name": "带凭据服务",
            "slug": "secret-test",
            "protocol": "openai_compatible",
            "credential_ref": "MODEL_CATALOG_TEST_KEY",
        },
    )
    assert service_response.status_code == 201
    body = str(service_response.json())
    assert "super-secret-value" not in body
    service = next(
        item for item in service_response.json()["services"] if item["slug"] == "secret-test"
    )
    assert service["credential_configured"] is True


# ---------- 三个回归 ----------


async def test_default_model_survives_a_reload(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """在界面上换默认模型 → 保存成功 → 刷新又变回去了。

    写入走 `resolve_settings`（对的），读取却落到 `get_settings()` 的 .env 启动快照，
    而 `chat_model_profile_id` 就存在数据库里 —— 写进去了，但读的是另一个地方。

    自己造一个凭据可控的模型，不去挑数据集里「另一个」profile —— 那取决于开发机
    `.env` 里恰好配了哪些 key，换台机器就可能挑中一个没配凭据的。
    """
    monkeypatch.setenv("RELOAD_TEST_API_KEY", "sk-configured")
    service = await client.post(
        "/api/models/services",
        json={
            "name": "重载测试",
            "slug": "reload-test",
            "protocol": "openai_compatible",
            "base_url": "https://example.invalid/v1",
            "credential_ref": "RELOAD_TEST_API_KEY",
        },
    )
    assert service.status_code == 201
    created = await client.post(
        "/api/models/profiles",
        json={"service_id": next(
            item["id"] for item in service.json()["services"] if item["slug"] == "reload-test"
        ), "model_id": "reload-model"},
    )
    profile_id = next(
        item["id"] for item in created.json()["profiles"] if item["model_id"] == "reload-model"
    )

    saved = await client.post(
        "/api/models/default", json={"purpose": "chat", "profile_id": profile_id}
    )
    assert saved.json()["default_profile_id"] == profile_id

    # 关键：重新 GET 一次，必须还是刚才选的那个
    assert (await client.get("/api/models")).json()["default_profile_id"] == profile_id


async def test_choosing_a_model_without_credentials_is_a_400(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """选一个没配凭据的模型要给出能照着做的 400，不是 500。

    `resolve_model_target` 抛的是 ValueError，不翻译就直接冒成服务端错误 ——
    而这件事完全是用户可修的：去配那个环境变量。
    """
    monkeypatch.delenv("NOCRED_API_KEY", raising=False)
    service = await client.post(
        "/api/models/services",
        json={
            "name": "没配凭据",
            "slug": "no-cred",
            "protocol": "openai_compatible",
            "credential_ref": "NOCRED_API_KEY",
        },
    )
    created = await client.post(
        "/api/models/profiles",
        json={"service_id": next(
            item["id"] for item in service.json()["services"] if item["slug"] == "no-cred"
        ), "model_id": "nope"},
    )
    profile_id = next(
        item["id"] for item in created.json()["profiles"] if item["model_id"] == "nope"
    )

    response = await client.post(
        "/api/models/default", json={"purpose": "chat", "profile_id": profile_id}
    )

    assert response.status_code == 400
    assert "NOCRED_API_KEY" in response.json()["detail"]


async def test_a_placeholder_key_is_not_treated_as_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.env.example` 的 `sk-ant-...` 抄过去没填，是最常见的一种「配了但没配」。

    判据必须和设置页那边共用 —— 各写一套的后果实测过：模型目录说「凭据已配置」、
    界面把 Claude 列成可用，选中之后发送时才 401。
    """
    from app.config import Settings
    from app.llm.catalog import _secret

    assert _secret("ANTHROPIC_API_KEY", Settings(anthropic_api_key="sk-ant-...")) == ""
    assert _secret("ANTHROPIC_API_KEY", Settings(anthropic_api_key="sk-real")) == "sk-real"


async def test_credential_ref_cannot_read_non_credential_settings() -> None:
    """`credential_ref` 的校验只要求全大写下划线，`DATABASE_URL` 完全合法。

    原来 `_secret` 是任意 `getattr(settings, ref.lower())`，于是**带密码的数据库
    连接串会被当成 api key**，发到该服务 base_url 指向的第三方，界面上还显示
    「凭据已配置 ✓」。
    """
    from app.config import Settings
    from app.llm.catalog import _secret

    settings = Settings(
        database_url="postgresql+asyncpg://chat:SUPERSECRET@db:5432/chat",
        owner_name="lance",
        model="claude-opus-5",
    )

    for ref in ("DATABASE_URL", "OWNER_NAME", "MODEL"):
        assert _secret(ref, settings) == "", ref


async def test_credential_ref_still_reads_real_api_keys() -> None:
    """挡住内部字段的同时，真正的密钥字段要照常读得到。"""
    from app.config import Settings
    from app.llm.catalog import _secret

    settings = Settings(anthropic_api_key="sk-real-key")

    assert _secret("ANTHROPIC_API_KEY", settings) == "sk-real-key"


async def test_concurrent_first_load_does_not_collide(session: AsyncSession) -> None:
    """首次访问时前端会同时打好几个接口，几个请求一起引导内置目录。

    `slug` 上有唯一索引，裸的 SELECT-then-INSERT 会让一个成功、其余全 500 ——
    迁移完第一次打开界面就会撞上，第二次之后正常，所以开发时极容易看不见。
    """
    import asyncio

    from app.llm.catalog import ensure_builtin_catalog

    # 同一个 session 上串行跑多次也必须幂等（真实并发在下面的多 session 用例里）
    for _ in range(3):
        await ensure_builtin_catalog(session)
    await session.commit()

    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from app.db.models import ModelService

    count = await session.scalar(sa_select(func.count(ModelService.id)))
    assert count == 3

    assert asyncio  # 保持导入可读性，真正的并发覆盖见集成验证


async def test_creating_a_service_rejects_a_non_credential_ref(
    client: AsyncClient,
) -> None:
    """挡在写入这一步，用户当场看到原因。

    否则要等到发第一条消息才发现「未配置凭据」，或者更糟 —— 引用了
    `DATABASE_URL` 这种东西而它恰好有值。
    """
    response = await client.post(
        "/api/models/services",
        json={
            "name": "偷偷摸摸",
            "slug": "sneaky",
            "protocol": "openai_compatible",
            "credential_ref": "DATABASE_URL",
        },
    )

    assert response.status_code == 400
    assert "_API_KEY" in response.json()["detail"]


async def test_a_normal_vendor_key_is_accepted(client: AsyncClient) -> None:
    """收紧不能把正常的厂商密钥名一起挡掉。"""
    response = await client.post(
        "/api/models/services",
        json={
            "name": "OpenRouter",
            "slug": "openrouter",
            "protocol": "openai_compatible",
            "base_url": "https://openrouter.ai/api/v1",
            "credential_ref": "OPENROUTER_API_KEY",
        },
    )

    assert response.status_code == 201
