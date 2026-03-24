"""FastAPI 认证依赖。"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..db import database
from ..db.models import User
from ..services.task_store import task_store
from .jwt_handler import InvalidTokenError, decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="登录状态无效或已过期，请重新登录。",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _resolve_user_by_token(token: str | None) -> User:
    """按 token 解析当前用户，供不同鉴权入口复用。"""
    if not token:
        raise _unauthorized_exception()

    await task_store.ensure_ready()

    try:
        payload = decode_access_token(token)
    except InvalidTokenError as exc:
        raise _unauthorized_exception() from exc

    user_id = str(payload.get("sub") or "").strip()
    if not user_id:
        raise _unauthorized_exception()

    async with database.async_session_factory() as session:
        user = await session.get(User, user_id)

    if user is None:
        raise _unauthorized_exception()

    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> User:
    """仅接受 Authorization Bearer 的通用鉴权依赖。"""
    token: str | None = None
    if credentials is not None and credentials.scheme.lower() == "bearer":
        token = credentials.credentials

    return await _resolve_user_by_token(token)


async def get_current_user_for_sse(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    access_token: str | None = Query(None),
) -> User:
    """SSE 场景允许 query token 兜底，其余接口不开放 URL token。"""
    token: str | None = None
    if credentials is not None and credentials.scheme.lower() == "bearer":
        token = credentials.credentials
    elif access_token:
        token = access_token

    return await _resolve_user_by_token(token)
