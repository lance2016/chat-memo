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
    assert all("api_key" not in str(profile).lower() for profile in body["profiles"])


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

    default_response = await client.post(
        "/api/models/default",
        json={"purpose": "chat", "profile_id": profile["id"]},
    )
    assert default_response.status_code == 200
    assert default_response.json()["default_profile_id"] == profile["id"]


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
