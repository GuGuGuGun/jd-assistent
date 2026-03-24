"""认证相关 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..auth.admin_guard import is_admin_email
from ..auth.dependencies import get_current_user
from ..auth.jwt_handler import create_access_token, hash_password, verify_password
from ..db import database
from ..db.models import CreditLedger, User
from ..schemas.api import AuthRequest, CurrentUserResponse, TokenResponse
from ..services.task_store import task_store

router = APIRouter(prefix="/api/v1/auth", tags=["认证"])


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _build_user_response(user: User) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=user.id,
        email=user.email,
        auth_provider=user.auth_provider,
        credits=user.credits,
        tier=user.tier,
        is_admin=is_admin_email(user.email),
    )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="邮箱注册",
)
async def register(payload: AuthRequest):
    """创建本地账号，并直接签发访问令牌。"""
    await task_store.ensure_ready()
    normalized_email = _normalize_email(payload.email)

    async with database.async_session_factory() as session:
        existing_user = (
            await session.execute(select(User).where(User.email == normalized_email))
        ).scalar_one_or_none()
        if existing_user is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该邮箱已注册，请直接登录。",
            )

        user = User(
            email=normalized_email,
            hashed_pw=hash_password(payload.password),
            auth_provider="local",
        )
        session.add(user)

        try:
            await session.flush()
            session.add(
                CreditLedger(
                    user_id=user.id,
                    delta=user.credits,
                    balance_after=user.credits,
                    reason="initial_grant",
                    note="新用户初始额度",
                )
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该邮箱已注册，请直接登录。",
            ) from exc

        await session.refresh(user)

    access_token = create_access_token(subject=user.id)
    return TokenResponse(access_token=access_token, user=_build_user_response(user))


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="邮箱密码登录",
)
async def login(payload: AuthRequest):
    """使用邮箱与密码登录。"""
    await task_store.ensure_ready()
    normalized_email = _normalize_email(payload.email)

    async with database.async_session_factory() as session:
        user = (
            await session.execute(select(User).where(User.email == normalized_email))
        ).scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.hashed_pw):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="邮箱或密码错误。",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(subject=user.id)
    return TokenResponse(access_token=access_token, user=_build_user_response(user))


@router.get(
    "/me",
    response_model=CurrentUserResponse,
    summary="获取当前登录用户",
)
async def read_current_user(current_user: User = Depends(get_current_user)):
    """返回当前登录用户的公开信息。"""
    await task_store.ensure_ready()
    return _build_user_response(current_user)
