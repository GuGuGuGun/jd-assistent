"""
R1 阶段数据库 ORM 模型定义。
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from .database import Base


def _json_type():
    """为不同数据库方言提供兼容的 JSON 列类型。"""
    return JSON().with_variant(JSONB, "postgresql")


class User(Base):
    """平台用户。"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_pw: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auth_provider: Mapped[str] = mapped_column(
        String(50), default="local", nullable=False
    )
    credits: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    tier: Mapped[str] = mapped_column(String(50), default="free", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Task(Base):
    """简历优化任务。"""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    target_jd: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    node_logs: Mapped[list] = mapped_column(_json_type(), default=list, nullable=False)
    token_usage: Mapped[dict] = mapped_column(
        _json_type(), default=dict, nullable=False
    )
    billing_status: Mapped[str] = mapped_column(
        String(50), default="none", nullable=False
    )
    billing_reserved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    billing_released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    billing_reservation_amount: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    billing_charged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    billing_charge_amount: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Resume(Base):
    """任务生成的最终简历数据。"""

    __tablename__ = "resumes"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    task_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("tasks.id"), unique=True, nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    render_data: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserProfile(Base):
    """用户能力画像资产池。"""

    __tablename__ = "user_profiles"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id"), unique=True, nullable=False
    )
    skill_matrix: Mapped[dict] = mapped_column(
        _json_type(), default=dict, nullable=False
    )
    raw_experiences: Mapped[list] = mapped_column(
        _json_type(), default=list, nullable=False
    )
    education: Mapped[list] = mapped_column(_json_type(), default=list, nullable=False)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CreditLedger(Base):
    """用户额度流水。"""

    __tablename__ = "credit_ledger"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    task_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("tasks.id"), nullable=True
    )
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(100), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
