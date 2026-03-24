"""
数据库模型元数据测试。
"""

from ..db.database import Base
from ..db import models  # noqa: F401  # 确保模型已注册到 metadata


def test_r1_tables_are_registered():
    """R1 规划要求的四张核心表应完成注册。"""
    table_names = set(Base.metadata.tables.keys())

    assert {"users", "tasks", "resumes", "user_profiles", "credit_ledger"}.issubset(
        table_names
    )


def test_tasks_table_contains_audit_columns():
    """任务表应具备后续持久化与审计所需的关键字段。"""
    tasks_table = Base.metadata.tables["tasks"]

    assert "status" in tasks_table.c
    assert "node_logs" in tasks_table.c
    assert "token_usage" in tasks_table.c
    assert "billing_charged_at" in tasks_table.c
    assert "billing_charge_amount" in tasks_table.c
    assert "billing_status" in tasks_table.c
    assert "billing_reserved_at" in tasks_table.c
    assert "billing_released_at" in tasks_table.c
    assert "billing_reservation_amount" in tasks_table.c


def test_credit_ledger_table_contains_balance_history_columns():
    """额度流水表应具备重建余额历史所需的关键字段。"""

    ledger_table = Base.metadata.tables["credit_ledger"]

    assert "user_id" in ledger_table.c
    assert "task_id" in ledger_table.c
    assert "delta" in ledger_table.c
    assert "balance_after" in ledger_table.c
    assert "reason" in ledger_table.c
    assert "created_by" in ledger_table.c
