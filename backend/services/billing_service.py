"""额度计费服务。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult

from ..db import database
from ..db.models import CreditLedger, Task, User


class InsufficientCreditsError(RuntimeError):
    """用户额度不足。"""


class BillingService:
    """统一处理额度校验与扣减，避免业务层散落计费逻辑。"""

    @staticmethod
    def _build_initial_node_logs() -> list[dict]:
        return [
            {
                "node": node_name,
                "status": "pending",
                "message": None,
                "review_passed": None,
                "duration_ms": None,
                "started_at": None,
                "finished_at": None,
            }
            for node_name in [
                "profile_builder",
                "jd_analyst",
                "content_optimizer",
                "content_reviewer",
                "final_typesetter",
            ]
        ]

    @staticmethod
    def _create_ledger_entry(
        *,
        user_id: str,
        delta: int,
        balance_after: int,
        reason: str,
        note: str | None = None,
        task_id: str | None = None,
        created_by: str | None = None,
    ) -> CreditLedger:
        return CreditLedger(
            user_id=user_id,
            task_id=task_id,
            delta=delta,
            balance_after=balance_after,
            reason=reason,
            note=note,
            created_by=created_by,
        )

    async def create_task_with_reservation(
        self,
        *,
        task_id: str,
        user_id: str,
        jd_text: str,
        original_file: str | None,
    ) -> None:
        """创建任务并在同一事务内完成额度预留。"""

        async with database.async_session_factory() as session:
            user = await session.get(User, user_id)
            if user is None:
                raise RuntimeError("提交任务时未找到对应用户。")

            task = Task(
                id=task_id,
                user_id=user_id,
                status="pending",
                target_jd=jd_text,
                original_file=original_file,
                node_logs=self._build_initial_node_logs(),
                token_usage={},
            )
            session.add(task)
            await session.flush()

            if user.tier != "pro":
                reserved_at = datetime.now(timezone.utc)
                update_result = cast(
                    CursorResult,
                    await session.execute(
                        update(User)
                        .where(User.id == user_id, User.credits > 0)
                        .values(credits=User.credits - 1)
                        .returning(User.credits)
                    ),
                )
                new_balance = update_result.scalar_one_or_none()
                if new_balance is None:
                    await session.rollback()
                    raise InsufficientCreditsError(
                        "当前额度不足，请联系管理员或升级套餐后再试。"
                    )

                task.billing_status = "reserved"
                task.billing_reserved_at = reserved_at
                task.billing_reservation_amount = 1
                task.token_usage = {
                    **dict(task.token_usage or {}),
                    "billing": {
                        "reserved": True,
                        "charged": False,
                        "released": False,
                        "amount": 1,
                        "reserved_at": reserved_at.isoformat(),
                    },
                }
                session.add(
                    self._create_ledger_entry(
                        user_id=user.id,
                        task_id=task.id,
                        delta=-1,
                        balance_after=int(new_balance),
                        reason="task_submission_reserve",
                        note=f"任务 {task.id} 提交时预留额度",
                    )
                )
                user.credits = int(new_balance)
            else:
                task.billing_status = "waived"
                task.token_usage = {
                    **dict(task.token_usage or {}),
                    "billing": {
                        "reserved": False,
                        "charged": False,
                        "released": False,
                        "amount": 0,
                        "waived": True,
                    },
                }

            await session.commit()

    async def adjust_user_credits(
        self,
        *,
        user_id: str,
        delta: int,
        reason: str,
        created_by: str | None = None,
    ) -> User:
        """管理员调整用户额度并写入流水。"""

        async with database.async_session_factory() as session:
            user = await session.get(User, user_id)
            if user is None:
                raise ValueError("目标用户不存在。")

            update_result = cast(
                CursorResult,
                await session.execute(
                    update(User)
                    .where(User.id == user_id, User.credits + delta >= 0)
                    .values(credits=User.credits + delta)
                    .returning(User.credits)
                ),
            )
            new_balance = update_result.scalar_one_or_none()
            if new_balance is None:
                await session.rollback()
                raise InsufficientCreditsError("调整后额度不能小于 0。")

            session.add(
                self._create_ledger_entry(
                    user_id=user.id,
                    delta=delta,
                    balance_after=int(new_balance),
                    reason="admin_adjustment",
                    note=reason,
                    created_by=created_by,
                )
            )
            await session.commit()
            await session.refresh(user)
            return user

    async def ensure_user_can_submit(self, user_id: str) -> None:
        """在任务提交前校验额度是否足够。"""
        async with database.async_session_factory() as session:
            user = await session.get(User, user_id)

        if user is None:
            raise RuntimeError("提交任务时未找到对应用户。")

        if user.tier != "pro" and user.credits <= 0:
            raise InsufficientCreditsError(
                "当前额度不足，请联系管理员或升级套餐后再试。"
            )

    async def release_task_reservation(self, task_id: str, reason: str) -> None:
        """在任务失败时释放已预留额度。"""

        async with database.async_session_factory() as session:
            task = await session.get(Task, task_id)
            if task is None:
                return

            if task.billing_status in {"charged", "released", "waived"}:
                return

            amount = int(task.billing_reservation_amount or 0)
            released_at = datetime.now(timezone.utc)
            claim_result = cast(
                CursorResult,
                await session.execute(
                    update(Task)
                    .where(
                        Task.id == task_id,
                        Task.billing_status == "reserved",
                        Task.billing_charged_at.is_(None),
                        Task.billing_released_at.is_(None),
                    )
                    .values(
                        billing_status="released",
                        billing_released_at=released_at,
                    )
                ),
            )
            if (claim_result.rowcount or 0) == 0:
                await session.rollback()
                return

            await session.refresh(task)

            if amount <= 0:
                await session.commit()
                return

            update_result = cast(
                CursorResult,
                await session.execute(
                    update(User)
                    .where(User.id == task.user_id)
                    .values(credits=User.credits + amount)
                    .returning(User.credits)
                ),
            )
            new_balance = update_result.scalar_one()

            token_usage = dict(task.token_usage or {})
            billing_meta = dict(token_usage.get("billing") or {})
            billing_meta.update(
                {
                    "released": True,
                    "released_at": released_at.isoformat(),
                    "release_reason": reason,
                }
            )
            token_usage["billing"] = billing_meta
            task.token_usage = token_usage
            task.billing_status = "released"
            task.billing_released_at = released_at
            session.add(
                self._create_ledger_entry(
                    user_id=task.user_id,
                    task_id=task.id,
                    delta=amount,
                    balance_after=int(new_balance),
                    reason="task_failure_release",
                    note=reason,
                )
            )
            await session.commit()

    async def finalize_task_charge(self, task_id: str) -> None:
        """在任务成功完成时执行并发安全的幂等扣费。"""
        async with database.async_session_factory() as session:
            task = await session.get(Task, task_id)
            if task is None:
                raise RuntimeError(f"扣费时未找到任务 {task_id}。")

            if task.billing_status == "charged" or task.billing_charged_at is not None:
                return

            token_usage = dict(task.token_usage or {})
            billing_meta = dict(token_usage.get("billing") or {})
            if billing_meta.get("charged"):
                return

            if task.billing_status == "released":
                raise RuntimeError("任务额度已释放，不能再执行成功结算。")

            was_reserved = task.billing_status == "reserved"

            charged_at = datetime.now(timezone.utc)
            status_scope = ["reserved"] if was_reserved else ["none", "waived"]
            claim_result = cast(
                CursorResult,
                await session.execute(
                    update(Task)
                    .where(
                        Task.id == task_id,
                        Task.billing_charged_at.is_(None),
                        Task.billing_released_at.is_(None),
                        Task.billing_status.in_(status_scope),
                    )
                    .values(
                        billing_charged_at=charged_at,
                        billing_status="charged",
                    )
                ),
            )
            if (claim_result.rowcount or 0) == 0:
                await session.rollback()
                refreshed_task = await session.get(Task, task_id)
                if refreshed_task is None or refreshed_task.billing_status == "charged":
                    return
                if refreshed_task.billing_status == "released":
                    raise RuntimeError("任务额度已释放，不能再执行成功结算。")
                return

            await session.refresh(task)
            user = await session.get(User, task.user_id)
            if user is None:
                raise RuntimeError(f"扣费时未找到任务 {task_id} 对应用户。")

            amount = 0
            balance_after = user.credits
            if was_reserved:
                amount = int(task.billing_reservation_amount or 0)
            elif user.tier != "pro":
                update_result = cast(
                    CursorResult,
                    await session.execute(
                        update(User)
                        .where(User.id == user.id, User.credits > 0)
                        .values(credits=User.credits - 1)
                        .returning(User.credits)
                    ),
                )
                new_balance = update_result.scalar_one_or_none()
                if new_balance is None:
                    await session.rollback()
                    raise InsufficientCreditsError("任务执行完成时检测到用户额度不足。")
                amount = 1
                balance_after = int(new_balance)
                user.credits = balance_after

            billing_meta.update(
                {
                    "charged": True,
                    "amount": amount,
                    "charged_at": charged_at.isoformat(),
                }
            )
            token_usage["billing"] = billing_meta
            task.token_usage = token_usage
            task.billing_charge_amount = amount

            if amount > 0 and not was_reserved:
                session.add(
                    self._create_ledger_entry(
                        user_id=user.id,
                        task_id=task.id,
                        delta=-amount,
                        balance_after=balance_after,
                        reason="task_completion_charge",
                        note=f"任务 {task.id} 完成后扣费",
                    )
                )

            await session.commit()


billing_service = BillingService()
