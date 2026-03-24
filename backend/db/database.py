"""
数据库连接与会话工厂。
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from backend.config import database_config


class Base(DeclarativeBase):
    """所有 ORM 模型的声明基类。"""


async_engine = create_async_engine(
    database_config.DATABASE_URL,
    echo=False,
    future=True,
)

async_session_factory = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
