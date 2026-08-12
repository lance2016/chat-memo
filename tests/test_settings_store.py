"""可写运行时设置。

三层解析：会话覆盖 > 数据库设置 > 代码/环境基础默认。重点验证白名单拦截和「改完立刻生效」。
"""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.session import get_session
from app.main import create_app
from app.settings_store import (
    ENV_ONLY,
    WRITABLE,
    WRITABLE_BY_KEY,
    SettingError,
    apply,
    describe,
    load_overrides,
    resolve_settings,
)


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


async def test_no_override_returns_code_defaults(session: AsyncSession) -> None:
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


async def test_null_restores_code_default(session: AsyncSession) -> None:
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


async def test_runtime_tuning_fields_are_database_settings(session: AsyncSession) -> None:
    base = base_settings()
    await apply(
        session,
        {"history_max_chars": 80_000, "notify_timeout": 20, "asr_timeout": 90},
        base,
    )
    await session.commit()

    resolved = await resolve_settings(session, base)
    assert resolved.history_max_chars == 80_000
    assert resolved.notify_timeout == 20
    assert resolved.asr_timeout == 90
    assert "history_max_chars" not in ENV_ONLY


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
    """示例配置里的 sk-ant-... 是占位符，不能当成已配置。"""
    payload = describe(Settings(anthropic_api_key="sk-ant-..."), {})
    anthropic = next(p for p in payload["providers"] if p["value"] == "anthropic")
    assert anthropic["available"] is False


# ---------- describe ----------


def test_describe_reports_source_per_field() -> None:
    payload = describe(base_settings(owner_name="阿明"), {"owner_name": "阿明"})
    assert payload["sources"]["owner_name"] == "db"
    assert payload["sources"]["consolidate_hour"] == "default"


def test_describe_never_leaks_secrets() -> None:
    payload = describe(
        Settings(deepseek_api_key="sk-secret", api_key="admin-token"), {}
    )
    blob = str(payload)
    assert "sk-secret" not in blob
    assert "admin-token" not in blob


def test_describe_masks_runtime_notification_key() -> None:
    payload = describe(
        Settings(bark_key="bark-device-secret"),
        {"bark_key": "bark-device-secret"},
    )
    assert payload["values"]["bark_key"] == ""
    assert next(field for field in payload["fields"] if field["key"] == "bark_key")["secret"] is True
    assert "bark-device-secret" not in str(payload)


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

    闭包里的 settings 是启动时的基础配置快照，聊天用的却是「数据库覆盖叠加在基础配置之上」
    的合并值。用快照的话，在设置页换掉模型之后 /health 会一直报旧的 ——
    健康检查报错模型比不报还糟。
    """
    patched = await client.patch("/api/settings", json={"deepseek_model": "deepseek-x"})
    assert patched.status_code == 200

    body = (await client.get("/health")).json()

    assert body["model"] == "deepseek-x"
    assert body["provider"] == "deepseek"


async def test_fields_carry_advanced_and_capability(client: AsyncClient) -> None:
    """分层信息必须由后端下发。

    这两个维度以前硬编码在 settings-page.tsx 的 *PrimaryFieldKeys 里：加一个配置项
    要在两处同步，漏掉前端那处的后果不是报错，而是**新字段在界面上凭空消失**。
    """
    fields = {f["key"]: f for f in (await client.get("/api/settings")).json()["fields"]}

    assert fields["tts_timeout"]["advanced"] is True
    assert fields["tts_timeout"]["capability"] == "voice"
    # 首屏该有的东西不能被折叠掉
    assert fields["tts_mode"]["advanced"] is False
    assert fields["provider"]["capability"] == ""


def test_core_settings_stay_out_of_the_advanced_drawer() -> None:
    """新手一进来就要面对的字段，必须留在首屏。

    钉住的是判据本身：`advanced` 是「改了多半更糟」，不是「我懒得排版」。
    这几项是「不配就用不起来」的那一类，任何时候都不该被折叠。
    """
    for key in ("provider", "owner_name", "tts_mode", "notify_enabled",
                "consolidate_auto", "backup_auto"):
        assert WRITABLE_BY_KEY[key].advanced is False, key


def test_optional_subsystems_declare_their_capability() -> None:
    """可选子系统的字段必须认领能力。

    漏标的后果是它会漏进核心配置里 —— 语音那 15 个字段依赖宿主机的 mlx-audio
    和几 GB 权重，摆在首屏等于让新手以为不下模型就用不了这个助手。
    """
    expected = {"tts": "voice", "asr": "voice_input", "notify": "notify",
                "skills": "skills", "debug": "debug"}
    for field in WRITABLE:
        want = expected.get(field.group)
        if want:
            assert field.capability == want, field.key


def test_legacy_model_fields_report_themselves_as_inactive() -> None:
    """设了聊天档案之后，旧的厂商/模型字段必须自报「不生效」。

    `resolve_model_target` 一旦拿到 `chat_model_profile_id` 就直接走档案，这三项
    完全不参与解析 —— 而它们在设置页仍然是可编辑的下拉框。不报的话，用户换了模型、
    保存成功、什么都没变，且没有任何错误可查。这是设置页里唯一一处静默失效。
    """
    from app.settings_store import inactive_reason

    without = Settings(chat_model_profile_id=None)
    with_profile = Settings(chat_model_profile_id=3)

    for key in ("provider", "model", "deepseek_model"):
        assert inactive_reason(key, without) == ""
        assert "接管" in inactive_reason(key, with_profile), key

    # 整理模型看的是另一个档案配置，别互相串了
    assert inactive_reason("consolidate_model", with_profile) == ""
    assert "接管" in inactive_reason(
        "consolidate_model", Settings(consolidate_model_profile_id=5)
    )
    # 还在兜底的调用参数不能被误报成不生效
    assert inactive_reason("max_tokens", with_profile) == ""
    assert inactive_reason("effort", with_profile) == ""


async def test_settings_payload_carries_inactive_reason(client: AsyncClient) -> None:
    fields = {f["key"]: f for f in (await client.get("/api/settings")).json()["fields"]}
    assert fields["provider"]["inactive_reason"] == ""

    await client.patch("/api/settings", json={"chat_model_profile_id": 3})

    fields = {f["key"]: f for f in (await client.get("/api/settings")).json()["fields"]}
    assert "接管" in fields["provider"]["inactive_reason"]


def test_tests_never_read_the_developers_env_file() -> None:
    """`Settings()` 在测试里必须只看代码默认值，不看仓库根的 `.env`。

    钉的是 `tests/conftest.py` 顶部那两行。没有它们的时候，开发机 `.env` 里随便加一项
    就能让一批毫不相关的用例失败（真实发生过：加了 `OPENAI_BASE_URL` 之后，
    `ModelTarget.from_settings` 的自动接管分支让 5 个思考开关用例开始报
    `'openai' != 'deepseek'`），而 CI 里没有 `.env`，两边结论相反。
    """
    assert Settings.model_config.get("env_file") is None
    # provider 的代码默认值必须是干净的，不带任何本机痕迹
    assert "provider" not in Settings().model_fields_set
