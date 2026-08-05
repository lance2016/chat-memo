import hmac

from fastapi import Header, HTTPException, status

from app.config import get_settings


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """单人使用的最低限度防护：仅防止 docker 端口意外暴露。

    settings.api_key 为空时不校验，方便本地开发。
    """
    expected = get_settings().api_key
    if not expected:
        return
    if x_api_key is None or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key"
        )
