"""
数据库驱动的任务存储。

设计意图：
1. 任务生命周期状态以数据库为唯一事实来源，保证 API 查询可恢复。
2. SSE 传输通过独立事件总线抽象实现，可在内存与 Redis 之间切换，但不承担持久化职责。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from ..config import event_bus_config
from ..db import database
from ..db.models import Resume, Task, User
from .billing_service import billing_service
from .event_bus import EventBus, EventSubscriber, create_event_bus

logger = logging.getLogger("jd_assistent.task_store")

DEFAULT_NODE_NAMES = [
    "profile_builder",
    "jd_analyst",
    "content_optimizer",
    "content_reviewer",
    "final_typesetter",
]
SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"
SYSTEM_USER_EMAIL = "local-system@jd-assistent.local"


@dataclass
class NodeLogEntry:
    """单个节点的执行记录。"""

    node: str
    status: str = "pending"
    message: Optional[str] = None
    review_passed: Optional[bool] = None
    duration_ms: Optional[int] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "node": self.node,
            "status": self.status,
            "message": self.message,
            "review_passed": self.review_passed,
            "duration_ms": self.duration_ms,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass
class TaskRecord:
    """API 层使用的任务视图对象。"""

    task_id: str
    status: str = "processing"
    result: Optional[dict] = None
    error: Optional[str] = None
    node_logs: list[NodeLogEntry] = field(default_factory=list)
    created_at: float = 0.0
    completed_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "node_logs": [log.to_dict() for log in self.node_logs],
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


class TaskStore:
    """任务状态存储与 SSE 事件分发器。"""

    def __init__(self):
        self._init_lock = asyncio.Lock()
        self._event_bus_lock = asyncio.Lock()
        self._db_ready = False
        self._event_bus: Optional[EventBus] = None

    async def ensure_ready(self):
        """确保数据库表已初始化。"""
        await self._ensure_database_ready()
        await self._ensure_event_bus_ready()

    async def shutdown(self):
        """关闭任务存储使用的附加资源。"""
        if self._event_bus is not None:
            await self._event_bus.close()
        self._event_bus = None

    def reset_for_testing(self):
        """重置内存态，供测试隔离使用。"""
        self._db_ready = False
        self._init_lock = asyncio.Lock()
        self._event_bus_lock = asyncio.Lock()
        self._event_bus = None

    async def create_task(
        self,
        task_id: str,
        jd_text: str = "",
        original_file: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> TaskRecord:
        """创建新任务并写入数据库。"""
        await self._ensure_database_ready()

        async with database.async_session_factory() as session:
            resolved_user_id = user_id
            if resolved_user_id is None:
                system_user = await self._ensure_system_user(session)
                resolved_user_id = system_user.id

            task = Task(
                id=task_id,
                user_id=resolved_user_id,
                status="pending",
                target_jd=jd_text,
                original_file=original_file,
                node_logs=self._build_initial_node_logs(),
                token_usage={},
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)

        logger.info("任务创建: %s", task_id)

        created_record = await self.get_task(task_id)
        if created_record is None:
            raise RuntimeError(f"任务 {task_id} 创建后未能回读数据库记录")

        return created_record

    async def mark_task_started(self, task_id: str) -> bool:
        """在任务真正进入执行入口时，将数据库状态从 pending 切换为 processing。"""

        def updater(task: Task):
            # 设计意图：任务创建与任务开始执行解耦。
            # 只有 local 协程或 Celery worker 真正拿到执行权时，才把状态推进到 processing。
            # 这样既能保留 DB 中更精确的生命周期语义，又不会影响外部 API 继续把 pending/processing 统一映射为 processing。
            if task.status == "pending":
                task.status = "processing"
                task.error_msg = None

        return await self._update_task(task_id, updater)

    async def get_task(self, task_id: str) -> Optional[TaskRecord]:
        """从数据库读取任务并转换为 API 响应结构。"""
        await self._ensure_database_ready()

        async with database.async_session_factory() as session:
            task = await session.get(Task, task_id)
            if not task:
                return None

            resume = await self._get_resume(session, task_id)
            return self._map_task_to_record(task, resume)

    async def get_task_for_user(
        self, task_id: str, user_id: str
    ) -> Optional[TaskRecord]:
        """按用户隔离读取任务，避免跨租户访问。"""
        await self._ensure_database_ready()

        async with database.async_session_factory() as session:
            task = (
                await session.execute(
                    select(Task).where(Task.id == task_id, Task.user_id == user_id)
                )
            ).scalar_one_or_none()
            if task is None:
                return None

            resume = await self._get_resume(session, task_id)
            return self._map_task_to_record(task, resume)

    async def mark_node_start(self, task_id: str, node_name: str, message: str = ""):
        """标记节点开始执行并推送 SSE 事件。"""

        def updater(task: Task):
            node_logs = self._normalize_node_logs(task.node_logs)
            for log in node_logs:
                if log["node"] == node_name:
                    log["status"] = "running"
                    log["message"] = message
                    log["started_at"] = self._now_timestamp()
                    break
            task.node_logs = node_logs

        updated = await self._update_task(task_id, updater)
        if not updated:
            return

        await self._publish_event(
            task_id,
            {
                "event": "node_start",
                "data": {"node": node_name, "message": message},
            },
        )

    async def mark_node_complete(self, task_id: str, node_name: str):
        """标记节点完成并推送 SSE 事件。"""
        duration_ms: Optional[int] = None

        def updater(task: Task):
            nonlocal duration_ms
            node_logs = self._normalize_node_logs(task.node_logs)
            for log in node_logs:
                if log["node"] == node_name:
                    finished_at = self._now_timestamp()
                    log["status"] = "done"
                    log["finished_at"] = finished_at
                    if log.get("started_at") is not None:
                        duration_ms = int((finished_at - log["started_at"]) * 1000)
                        log["duration_ms"] = duration_ms
                    break
            task.node_logs = node_logs

        updated = await self._update_task(task_id, updater)
        if not updated:
            return

        await self._publish_event(
            task_id,
            {
                "event": "node_complete",
                "data": {"node": node_name, "duration_ms": duration_ms},
            },
        )

    async def record_node_token_usage(
        self, task_id: str, node_name: str, audit_payload: dict
    ) -> bool:
        """将节点级 LLM 审计信息写入 Task.token_usage。"""

        def updater(task: Task):
            token_usage = dict(task.token_usage or {})
            node_usage = dict(token_usage.get("nodes") or {})
            node_usage[node_name] = audit_payload
            token_usage["nodes"] = node_usage
            task.token_usage = token_usage

        return await self._update_task(task_id, updater)

    async def mark_node_error(self, task_id: str, node_name: str, error: str):
        """记录节点错误到数据库。"""

        def updater(task: Task):
            node_logs = self._normalize_node_logs(task.node_logs)
            for log in node_logs:
                if log["node"] == node_name:
                    log["status"] = "error"
                    log["message"] = error
                    log["finished_at"] = self._now_timestamp()
                    break
            task.node_logs = node_logs

        await self._update_task(task_id, updater)

    async def mark_review_feedback(
        self, task_id: str, passed: bool, feedback: str = ""
    ):
        """记录审查反馈，并通过 SSE 推送给前端。"""

        def updater(task: Task):
            node_logs = self._normalize_node_logs(task.node_logs)
            for log in node_logs:
                if log["node"] == "content_reviewer":
                    log["message"] = feedback or None
                    log["review_passed"] = passed
                    break
            task.node_logs = node_logs

        updated = await self._update_task(task_id, updater)
        if not updated:
            return

        await self._publish_event(
            task_id,
            {
                "event": "review_feedback",
                "data": {
                    "node": "content_reviewer",
                    "passed": passed,
                    "feedback": feedback,
                },
            },
        )

    async def mark_task_complete(self, task_id: str, result: dict):
        """标记任务完成，并将最终结果写入 resumes 表。"""
        await self._ensure_database_ready()

        async with database.async_session_factory() as session:
            task = await session.get(Task, task_id)
            if not task:
                return

            completed_at = self._now_datetime()
            task.status = "completed"
            task.error_msg = None
            task.completed_at = completed_at
            task.duration_ms = self._calculate_duration_ms(
                task.created_at, completed_at
            )

            resume = await self._get_resume(session, task_id)
            if resume:
                resume.render_data = result
                resume.version += 1
            else:
                session.add(
                    Resume(
                        task_id=task_id,
                        user_id=task.user_id,
                        render_data=result,
                        version=1,
                    )
                )

            await session.commit()

        await self._publish_event(
            task_id,
            {
                "event": "complete",
                "data": {"task_id": task_id, "message": "简历优化完成"},
            },
        )
        logger.info("任务完成: %s", task_id)

    async def mark_task_failed(self, task_id: str, error: str):
        """标记任务失败。"""
        await self._ensure_database_ready()

        async with database.async_session_factory() as session:
            task = await session.get(Task, task_id)
            if not task:
                return

            completed_at = self._now_datetime()
            task.status = "failed"
            task.error_msg = error
            task.completed_at = completed_at
            task.duration_ms = self._calculate_duration_ms(
                task.created_at, completed_at
            )
            await session.commit()

        await self._publish_event(
            task_id,
            {
                "event": "error",
                "data": {"task_id": task_id, "error": error},
            },
        )
        await billing_service.release_task_reservation(task_id, error)
        logger.error("任务失败: %s — %s", task_id, error)

    async def subscribe_to_events(self, task_id: str) -> EventSubscriber:
        """订阅指定任务的实时事件。"""
        event_bus = await self._ensure_event_bus_ready()
        return await event_bus.subscribe(task_id)

    async def replay_task_events(self, task_id: str) -> list[dict]:
        """根据数据库状态重建 SSE 回放事件。"""
        record = await self.get_task(task_id)
        if record is None:
            return []

        replay_events: list[dict] = []
        for node_log in record.node_logs:
            if node_log.started_at is not None or node_log.status in {
                "running",
                "done",
                "error",
            }:
                replay_events.append(
                    {
                        "event": "node_start",
                        "data": {
                            "node": node_log.node,
                            "message": node_log.message or "",
                        },
                    }
                )

            if node_log.status == "done":
                replay_events.append(
                    {
                        "event": "node_complete",
                        "data": {
                            "node": node_log.node,
                            "duration_ms": node_log.duration_ms,
                        },
                    }
                )

            if (
                node_log.node == "content_reviewer"
                and node_log.review_passed is not None
            ):
                # 设计意图：当前阶段不引入额外表结构，因此将 review_feedback 的附加状态继续
                # 存进 tasks.node_logs JSON 中，以支持 SSE 断线重连后的无损回放。
                replay_events.append(
                    {
                        "event": "review_feedback",
                        "data": {
                            "node": "content_reviewer",
                            "passed": node_log.review_passed,
                            "feedback": node_log.message or "",
                        },
                    }
                )

        if record.status == "completed":
            replay_events.append(
                {
                    "event": "complete",
                    "data": {"task_id": task_id, "message": "简历优化完成"},
                }
            )
        elif record.status == "failed" and record.error:
            replay_events.append(
                {
                    "event": "error",
                    "data": {"task_id": task_id, "error": record.error},
                }
            )

        return replay_events

    async def _ensure_database_ready(self):
        if self._db_ready:
            return

        async with self._init_lock:
            if self._db_ready:
                return

            # 设计意图：当前切片尚未强依赖 Alembic，因此在应用启动与首次调用时
            # 自动兜底建表，确保“无 Celery / Redis / Auth”的现有模式仍可直接运行。
            async with database.async_engine.begin() as conn:
                await conn.run_sync(database.Base.metadata.create_all)

            self._db_ready = True

    async def _ensure_event_bus_ready(self) -> EventBus:
        if self._event_bus is not None:
            return self._event_bus

        async with self._event_bus_lock:
            if self._event_bus is not None:
                return self._event_bus

            self._event_bus = await create_event_bus(
                backend=event_bus_config.BACKEND,
                redis_url=event_bus_config.REDIS_URL,
                channel_prefix=event_bus_config.REDIS_CHANNEL_PREFIX,
            )
            return self._event_bus

    async def _ensure_system_user(self, session):
        user = await session.get(User, SYSTEM_USER_ID)
        if user:
            return user

        user = User(
            id=SYSTEM_USER_ID,
            email=SYSTEM_USER_EMAIL,
            auth_provider="local",
            credits=5,
            tier="free",
        )
        session.add(user)
        await session.flush()
        return user

    async def _get_resume(self, session, task_id: str) -> Optional[Resume]:
        result = await session.execute(select(Resume).where(Resume.task_id == task_id))
        return result.scalar_one_or_none()

    async def _update_task(self, task_id: str, updater) -> bool:
        await self._ensure_database_ready()

        async with database.async_session_factory() as session:
            task = await session.get(Task, task_id)
            if not task:
                return False

            updater(task)
            await session.commit()
            return True

    async def _publish_event(self, task_id: str, event: dict):
        event_bus = await self._ensure_event_bus_ready()
        await event_bus.publish(task_id, event)

    def _build_initial_node_logs(self) -> list[dict]:
        return [NodeLogEntry(node=name).to_dict() for name in DEFAULT_NODE_NAMES]

    def _normalize_node_logs(self, node_logs: Optional[list]) -> list[dict]:
        normalized_logs: list[dict] = []
        for node_name in DEFAULT_NODE_NAMES:
            matched = None
            for raw_log in node_logs or []:
                if raw_log.get("node") == node_name:
                    matched = NodeLogEntry(
                        node=node_name,
                        status=raw_log.get("status", "pending"),
                        message=raw_log.get("message"),
                        review_passed=raw_log.get("review_passed"),
                        duration_ms=raw_log.get("duration_ms"),
                        started_at=raw_log.get("started_at"),
                        finished_at=raw_log.get("finished_at"),
                    ).to_dict()
                    break

            normalized_logs.append(matched or NodeLogEntry(node=node_name).to_dict())

        return normalized_logs

    def _map_task_to_record(self, task: Task, resume: Optional[Resume]) -> TaskRecord:
        # 设计意图：对外 API 契约保持不变，因此这里显式做一次“数据库字段 → API 字段”映射，
        # 避免调用方感知到底层已从内存模型迁移到 Task/Resume 两张表。
        return TaskRecord(
            task_id=task.id,
            status=self._map_task_status(task.status),
            result=resume.render_data if resume else None,
            error=task.error_msg,
            node_logs=[
                NodeLogEntry(**node_log)
                for node_log in self._normalize_node_logs(task.node_logs)
            ],
            created_at=self._datetime_to_timestamp(task.created_at) or 0.0,
            completed_at=self._datetime_to_timestamp(task.completed_at),
        )

    def _map_task_status(self, db_status: str) -> str:
        status_mapping = {
            "pending": "processing",
            "processing": "processing",
            "completed": "completed",
            "failed": "failed",
        }
        return status_mapping.get(db_status, "processing")

    def _datetime_to_timestamp(self, value: Optional[datetime]) -> Optional[float]:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()

    def _calculate_duration_ms(
        self,
        created_at: Optional[datetime],
        completed_at: Optional[datetime],
    ) -> Optional[int]:
        if not created_at or not completed_at:
            return None

        started = (
            created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
        )
        finished = (
            completed_at
            if completed_at.tzinfo
            else completed_at.replace(tzinfo=timezone.utc)
        )
        return int((finished - started).total_seconds() * 1000)

    def _now_datetime(self) -> datetime:
        return datetime.now(timezone.utc)

    def _now_timestamp(self) -> float:
        return self._now_datetime().timestamp()


task_store = TaskStore()
