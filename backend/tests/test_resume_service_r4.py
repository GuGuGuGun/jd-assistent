"""R4 阶段画像持久化测试。"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from ..db import database
from ..db.models import User, UserProfile
from ..services.resume_service import _persist_user_profile_snapshot
from ..services.task_store import task_store


@pytest.mark.asyncio
async def test_persist_user_profile_snapshot_upserts_profile_asset():
    """画像节点输出应被持久化到 user_profiles，供 Dashboard 画像摘要复用。"""

    await task_store.ensure_ready()

    async with database.async_session_factory() as session:
        user = User(
            email="profile-persist@example.com", hashed_pw="hashed", tier="free"
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    task_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    await task_store.create_task(
        task_id,
        jd_text="需要熟悉画像摘要。",
        original_file="resume.md",
        user_id=user.id,
    )

    await _persist_user_profile_snapshot(
        task_id,
        {
            "skill_matrix": {
                "backend": ["FastAPI", "SQLAlchemy"],
                "frontend": ["Vue"],
            },
            "raw_experiences": [{"company": "A 公司"}],
            "education": [{"school": "测试大学"}],
        },
    )

    async with database.async_session_factory() as session:
        profile = (
            await session.execute(
                select(UserProfile).where(UserProfile.user_id == user.id)
            )
        ).scalar_one_or_none()

    assert profile is not None
    assert profile.skill_matrix == {
        "backend": ["FastAPI", "SQLAlchemy"],
        "frontend": ["Vue"],
    }
    assert profile.raw_experiences == [{"company": "A 公司"}]
    assert profile.education == [{"school": "测试大学"}]
