"""
简历优化业务服务 — 调用 LangGraph 工作流并管理节点进度。
"""

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from ..db import database
from ..db.models import Task, UserProfile as UserProfileAsset
from ..graph.state import INITIAL_STATE
from ..graph.workflow import get_workflow
from .checkpoint_store import build_checkpoint_config, has_persisted_checkpoint
from .billing_service import billing_service
from .cost_calculator import calculate_cost_usd
from .task_store import task_store
from ..utils.text_sanitizer import sanitize_resume_payload, sanitize_resume_text

logger = logging.getLogger("jd_assistent.services.resume")

# 节点名称到中文描述的映射
NODE_MESSAGES = {
    "profile_builder": "正在提取用户画像...",
    "jd_analyst": "正在分析岗位需求...",
    "content_optimizer": "正在优化简历内容...",
    "content_reviewer": "正在审查优化内容...",
    "final_typesetter": "正在进行终审排版...",
}


async def _persist_user_profile_snapshot(task_id: str, user_profile: dict) -> None:
    """将画像节点的真实输出落到 user_profiles 表，供 Dashboard 画像摘要复用。"""

    async with database.async_session_factory() as session:
        task = await session.get(Task, task_id)
        if task is None:
            logger.warning("持久化用户画像时未找到任务: %s", task_id)
            return

        profile: UserProfileAsset | None = (
            await session.execute(
                select(UserProfileAsset).where(UserProfileAsset.user_id == task.user_id)
            )
        ).scalar_one_or_none()

        if profile is None:
            profile = UserProfileAsset(user_id=task.user_id)
            session.add(profile)

        profile.skill_matrix = dict(user_profile.get("skill_matrix") or {})
        profile.raw_experiences = list(user_profile.get("raw_experiences") or [])
        profile.education = list(user_profile.get("education") or [])
        profile.last_updated = datetime.now(timezone.utc)
        await session.commit()


async def run_optimize_task(task_id: str, resume_text: str, jd_text: str):
    """
    执行简历优化任务（异步后台运行）。

    Args:
        task_id: 任务 ID
        resume_text: 原始简历文本
        jd_text: 目标岗位 JD 文本
    """
    try:
        workflow: Any = get_workflow()
        workflow_config = build_checkpoint_config(task_id)

        # 初始化状态
        cleaned_resume_text = sanitize_resume_text(resume_text)
        cleaned_jd_text = sanitize_resume_text(jd_text)

        initial_state = {
            **INITIAL_STATE,
            "original_resume_text": cleaned_resume_text,
            "target_jd_text": cleaned_jd_text,
        }

        logger.info("任务 %s 开始执行工作流", task_id)

        if has_persisted_checkpoint(task_id):
            # 设计意图：当前切片先补齐“同一 task_id 绑定同一 thread_id”的恢复钩子，
            # 真正的中断点续跑仍交给 LangGraph checkpointer 语义本身与后续 runtime 策略演进。
            logger.info(
                "任务 %s 检测到已有 checkpoint，将复用 thread_id 继续执行", task_id
            )

        # 使用 astream 逐节点推送进度
        async for event in workflow.astream(initial_state, config=workflow_config):
            for node_name, node_output in event.items():
                if node_name == "__end__":
                    continue

                # 标记节点开始
                message = NODE_MESSAGES.get(node_name, f"正在执行 {node_name}...")
                await task_store.mark_node_start(task_id, node_name, message)

                # 标记节点完成
                await task_store.mark_node_complete(task_id, node_name)

                if isinstance(node_output, dict):
                    llm_audit = node_output.get("llm_audit")
                    if isinstance(llm_audit, dict):
                        audit_payload = {
                            **llm_audit,
                            "cost_usd": calculate_cost_usd(
                                llm_audit.get("model", ""),
                                llm_audit.get("usage") or {},
                            ),
                        }
                        await task_store.record_node_token_usage(
                            task_id,
                            node_name,
                            audit_payload,
                        )

                    if node_name == "profile_builder":
                        user_profile_payload = node_output.get("user_profile")
                        if isinstance(user_profile_payload, dict):
                            await _persist_user_profile_snapshot(
                                task_id,
                                user_profile_payload,
                            )

                # 特殊处理：审查结果推送
                if node_name == "content_reviewer" and isinstance(node_output, dict):
                    passed = node_output.get("review_passed", False)
                    feedback = node_output.get("review_feedback", "")
                    await task_store.mark_review_feedback(
                        task_id, passed, feedback or ""
                    )

                # 提取最终结果
                if node_name == "final_typesetter" and isinstance(node_output, dict):
                    final_resume = node_output.get("final_resume")
                    if final_resume:
                        final_resume = sanitize_resume_payload(final_resume)
                        # 设计意图：成功扣费要发生在最终结果落库前，确保“失败不扣费、成功只扣一次”的规则成立。
                        await billing_service.finalize_task_charge(task_id)
                        await task_store.mark_task_complete(task_id, final_resume)
                        logger.info("任务 %s 执行完成", task_id)
                        return

        # 如果循环结束但没有拿到 final_resume，则从最后状态中获取
        # 这在某些 LangGraph 版本中可能发生
        await task_store.mark_task_failed(task_id, "工作流执行完毕但未生成最终简历")

    except Exception as e:
        logger.exception("任务 %s 执行失败: %s", task_id, str(e))
        await task_store.mark_task_failed(task_id, str(e))
