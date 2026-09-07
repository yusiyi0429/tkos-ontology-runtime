"""HTTP Basic gate for the temporary Clark compatibility façade."""
from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from adapter.settings import Settings, get_settings

_basic = HTTPBasic(auto_error=False)


def require_clark_auth(
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic)],
) -> None:
    """Fail closed unless the request matches configured ``user:password``."""
    raw = settings.adapter_auth
    separator = raw.find(":")
    if separator <= 0 or not raw[separator + 1 :]:
        raise HTTPException(
            status_code=503,
            detail="ADAPTER_AUTH 未配置为 user:password，Clark 兼容接口不可用",
        )
    expected_user = raw[:separator]
    expected_password = raw[separator + 1 :]
    if credentials is None or not (
        secrets.compare_digest(credentials.username, expected_user)
        and secrets.compare_digest(credentials.password, expected_password)
    ):
        raise HTTPException(
            status_code=401,
            detail="Clark 兼容接口认证失败",
            headers={"WWW-Authenticate": "Basic"},
        )
