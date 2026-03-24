"""管理员权限校验。"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status

from ..config import auth_config
from ..db.models import User
from .dependencies import get_current_user


def is_admin_email(email: str) -> bool:
    """基于配置白名单判断管理员身份。"""
    return email.strip().lower() in set(auth_config.ADMIN_EMAILS)


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """要求当前用户具备管理员权限。"""
    if not is_admin_email(current_user.email):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前账号没有管理员权限。",
        )
    return current_user
