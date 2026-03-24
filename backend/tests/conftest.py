"""
后端测试公共夹具。
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ..db import database
from ..main import app
from ..services.checkpoint_store import reset_checkpoint_store_for_testing
from ..services.task_store import task_store


@pytest.fixture
def client() -> TestClient:
    """创建 FastAPI 测试客户端。"""
    return TestClient(app)


@pytest.fixture(autouse=True)
def isolated_task_database(tmp_path, monkeypatch):
    """为每个测试提供独立的 SQLite 数据库，避免状态相互污染。"""
    db_path = tmp_path / "test_jd_assistent.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}", echo=False, future=True
    )
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    monkeypatch.setattr(database, "async_engine", engine)
    monkeypatch.setattr(database, "async_session_factory", session_factory)
    task_store.reset_for_testing()
    reset_checkpoint_store_for_testing()

    yield

    task_store.reset_for_testing()
    reset_checkpoint_store_for_testing()
    asyncio.run(engine.dispose())
