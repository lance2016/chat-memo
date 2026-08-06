"""可写运行时设置。

三层解析：会话覆盖 > 数据库设置 > .env 默认。重点验证白名单拦截和「改完立刻生效」。
"""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.session import get_session
from app.main import create_app
from app.settings_store import SettingError, apply, describe, load_overrides
from app.settings_store import resolve_settings


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def base_settings(**kw) -> Settings:
    return Settings(deepseek_api_key="sk-real-key", **kw)


# ---------- 解析 ----------


async def test_no_override_returns_env_defaults(session: AsyncSession) -> None:
    base = base_settings(owner_name="默认名")
    assert (await resolve_settings(session, base)).owner_name == "默认名"


async def test_db_override_wins_over_env(session: AsyncSession) -> None:
    base = base_settings(owner_name="默认名")
    await apply(session, {"owner_name": "阿明"}, base)
    await session.commit()

    assert (await resolve_settings(session, base)).owner_name == "阿明"


async def test_resolve_does_not_mutate_base(session: AsyncSession) -> None:
    """base 是 lru_cache 出来的全局单例，改它会污染所有请求。"""
    base = base_settings(owner_name="默认名")
    await apply(session, {"owner_name": "阿明"}, base)
    await session.commit()

    await resolve_settings(session, base)
    assert base.owner_name == "默认名"


async def test_null_restores_env_default(session: AsyncSession) -> None:
    base = base_settings(owner_name="默认名")
    await apply(session, {"owner_name": "阿明"}, base)
    await session.commit()

    await apply(session, {"owner_name": None}, base)
    await session.commit()

    assert (await resolve_settings(session, base)).owner_name == "默认名"
    assert await load_overrides(session) == {}


# ---------- 白名单 ----------


@pytest.mark.parametrize(
    "key", ["api_key", "database_url", "anthropic_api_key", "cors_origins", "log_level"]
)
async def test_rejects_env_only_fields(session: AsyncSession, key: str) -> None:
    """密钥、基础设施、以及改错会把自己锁在门外的东西，一律不可写。"""
    with pytest.raises(SettingError, match="不可通过接口修改"):
        await apply(session, {key: "x"}, base_settings())


async def test_rejects_unknown_field(session: AsyncSession) -> None:
    with pytest.raises(SettingError):
        await apply(session, {"nonexistent": 1}, base_settings())


# ---------- 取值校验 ----------


@pytest.mark.parametrize(
    ("payload", "hint"),
    [
        ({"consolidate_hour": 99}, "不能大于"),
        ({"consolidate_hour": -1}, "不能小于"),
        ({"max_tokens": 10}, "不能小于"),
        ({"max_tool_iterations": 999}, "不能大于"),
        ({"effort": "ultra"}, "只能是"),
        ({"deepseek_thinking": "yes"}, "true 或 false"),
        ({"consolidate_hour": "4"}, "必须是整数"),
        ({"owner_name": ""}, "不能为空"),
        ({"owner_name": "x" * 50}, "最多"),
    ],
)
async def test_value_validation(
    session: AsyncSession, payload: dict, hint: str
) -> None:
    with pytest.raises(SettingError, match=hint):
        await apply(session, payload, base_settings())


async def test_empty_allowed_for_consolidate_model(session: AsyncSession) -> None:
    """留空表示「和聊天用同一个模型」，是合法值。"""
    await apply(session, {"consolidate_model": ""}, base_settings())
    await session.commit()
    assert (await load_overrides(session))["consolidate_model"] == ""


# ---------- provider 切换的安全阀 ----------


async def test_cannot_switch_to_provider_without_key(session: AsyncSession) -> None:
    """切到没配 key 的 provider，之后每条消息都 401，设置页自己也会报错。"""
    with pytest.raises(SettingError, match="ANTHROPIC_API_KEY"):
        await apply(session, {"provider": "anthropic"}, base_settings())


async def test_can_switch_when_key_present(session: AsyncSession) -> None:
    base = Settings(deepseek_api_key="sk-a", anthropic_api_key="sk-b")
    await apply(session, {"provider": "anthropic"}, base)
    await session.commit()
    assert (await resolve_settings(session, base)).provider == "anthropic"


def test_placeholder_key_does_not_count() -> None:
    """.env.example 里的 sk-ant-... 是占位符，不能当成已配置。"""
    payload = describe(Settings(anthropic_api_key="sk-ant-..."), {})
    anthropic = next(p for p in payload["providers"] if p["value"] == "anthropic")
    assert anthropic["available"] is False


# ---------- describe ----------


def test_describe_reports_source_per_field() -> None:
    payload = describe(base_settings(owner_name="阿明"), {"owner_name": "阿明"})
    assert payload["sources"]["owner_name"] == "db"
    assert payload["sources"]["consolidate_hour"] == "env"


def test_describe_never_leaks_secrets() -> None:
    payload = describe(
        Settings(deepseek_api_key="sk-secret", api_key="admin-token"), {}
    )
    blob = str(payload)
    assert "sk-secret" not in blob
    assert "admin-token" not in blob


# ---------- 接口 ----------


async def test_patch_then_get_reflects_change(client: AsyncClient) -> None:
    body = (await client.patch("/api/settings", json={"owner_name": "阿明"})).json()
    assert body["values"]["owner_name"] == "阿明"
    assert body["sources"]["owner_name"] == "db"

    assert (await client.get("/api/settings")).json()["values"]["owner_name"] == "阿明"


async def test_patch_rejects_bad_value_with_400(client: AsyncClient) -> None:
    resp = await client.patch("/api/settings", json={"consolidate_hour": 99})
    assert resp.status_code == 400
    assert "不能大于" in resp.json()["detail"]


async def test_patch_is_partial(client: AsyncClient) -> None:
    await client.patch("/api/settings", json={"owner_name": "阿明"})
    await client.patch("/api/settings", json={"consolidate_hour": 6})

    values = (await client.get("/api/settings")).json()["values"]
    assert values["owner_name"] == "阿明"  # 没被第二次请求冲掉
    assert values["consolidate_hour"] == 6


async def test_get_exposes_env_only_list(client: AsyncClient) -> None:
    body = (await client.get("/api/settings")).json()
    assert "api_key" in body["env_only"]
    assert "database_url" in body["env_only"]


async def test_health_reports_the_merged_model_not_the_env_snapshot(
    client: AsyncClient,
) -> None:
    """/health 必须走 resolve_settings。

    闭包里的 settings 是启动时的 .env 快照，聊天用的却是「数据库覆盖叠加在 .env 之上」
    的合并值。用快照的话，在设置页换掉模型之后 /health 会一直报旧的 ——
    健康检查报错模型比不报还糟。
    """
    patched = await client.patch("/api/settings", json={"deepseek_model": "deepseek-x"})
    assert patched.status_code == 200

    body = (await client.get("/health")).json()

    assert body["model"] == "deepseek-x"
    assert body["provider"] == "deepseek"
