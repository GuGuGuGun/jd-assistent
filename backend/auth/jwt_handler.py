"""JWT 签发与密码哈希工具。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from jwt import InvalidTokenError
from pwdlib import PasswordHash

from ..config import auth_config

password_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """对用户密码做安全哈希。"""
    return password_hasher.hash(password)


def verify_password(password: str, hashed_password: str | None) -> bool:
    """校验明文密码是否与哈希值匹配。"""
    if not hashed_password:
        return False
    return password_hasher.verify(password, hashed_password)


def create_access_token(*, subject: str) -> str:
    """创建访问令牌。"""
    expire_at = datetime.now(timezone.utc) + timedelta(
        minutes=auth_config.JWT_EXPIRE_MINUTES
    )
    payload: dict[str, Any] = {"sub": subject, "exp": expire_at}
    return jwt.encode(
        payload,
        auth_config.JWT_SECRET,
        algorithm=auth_config.JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> dict[str, Any]:
    """解析访问令牌，非法时抛出统一异常。"""
    return jwt.decode(
        token,
        auth_config.JWT_SECRET,
        algorithms=[auth_config.JWT_ALGORITHM],
    )


__all__ = [
    "InvalidTokenError",
    "create_access_token",
    "decode_access_token",
    "hash_password",
    "verify_password",
]
