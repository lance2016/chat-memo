"""只能改 .env 的那些配置的状态。

这块唯一不能出错的事：**密钥的值绝不出现在响应里**。设置页是浏览器里的东西，
一个不小心把 `DATABASE_URL` 或 API key 回显出来，截图、录屏、分享都会带出去。
"""

from app.config import Settings
from app.settings_store import ENV_FIELDS, env_status, is_configured


def _rows(**overrides):
    return {row["key"]: row for row in env_status(Settings(**overrides))}


def test_secret_values_never_appear_in_the_response() -> None:
    """密钥只报布尔值。这是这个模块存在的前提，不是可选的谨慎。"""
    rows = _rows(
        database_url="postgresql+asyncpg://chat:SUPERSECRET@db:5432/chat",
        anthropic_api_key="sk-ant-real-key",
        deepseek_api_key="sk-deepseek-real",
        api_key="my-api-key",
    )
    blob = str(rows)

    for leaked in ("SUPERSECRET", "sk-ant-real-key", "sk-deepseek-real", "my-api-key"):
        assert leaked not in blob, leaked
    assert rows["anthropic_api_key"]["configured"] is True
    assert rows["anthropic_api_key"]["value"] == ""


def test_placeholder_does_not_count_as_configured() -> None:
    """`.env.example` 里是 `sk-...`。

    照抄模板却没填的人最多，把它判成「已配置」会让人对着一个 401 找半天。
    """
    assert is_configured("sk-...") is False
    assert is_configured("  ") is False
    assert is_configured("sk-real") is True

    assert _rows(anthropic_api_key="sk-...")["anthropic_api_key"]["configured"] is False


def test_empty_api_key_is_called_out_as_a_security_note() -> None:
    """空 api_key = 所有 /api 请求完全不校验（见 security.require_api_key）。

    单机 localhost 无所谓，但暴露到局域网之前必须配 —— roadmap 暴露面 checklist
    的第一条就是它，而现在界面上完全看不出来。
    """
    row = _rows(api_key="")["api_key"]

    assert row["configured"] is False
    assert "不做校验" in row["note"]

    assert _rows(api_key="something")["api_key"]["note"] == ""


def test_non_secret_values_are_shown() -> None:
    """地址和日志级别不敏感，显示出来才有用 —— 否则还是得去翻 .env。"""
    rows = _rows(deepseek_base_url="https://api.deepseek.com", log_level="DEBUG")

    assert rows["deepseek_base_url"]["value"] == "https://api.deepseek.com"
    assert rows["log_level"]["value"] == "DEBUG"


def test_every_field_maps_to_a_real_setting() -> None:
    """写错字段名会让它永远显示「未配置」，而且不报错。"""
    for field in ENV_FIELDS:
        assert field.key in Settings.model_fields, field.key


def test_connection_string_is_treated_as_a_secret() -> None:
    """`database_url` 长得不像密钥，但它带密码。分类判据是值敏不敏感，不是名字。"""
    field = next(f for f in ENV_FIELDS if f.key == "database_url")

    assert field.kind == "secret"


def test_describe_carries_the_status(monkeypatch) -> None:
    from app.settings_store import describe

    payload = describe(Settings(anthropic_api_key="sk-real"), {})

    assert payload["env_status"]
    assert all("value" in row for row in payload["env_status"])
