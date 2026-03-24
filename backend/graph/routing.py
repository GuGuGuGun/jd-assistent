"""
LangGraph 条件路由函数。
"""

import logging
from backend.graph.state import ResumeGraphState

logger = logging.getLogger("jd_assistent.graph.routing")

MAX_RETRY_COUNT = 3


def route_after_review(state: ResumeGraphState) -> str:
    """
    Node 4 之后的条件路由：
    - 审查通过 → 进入 Node 5 排版
    - 审查未通过 → 返回 Node 3 重写（有重试上限）
    - 超过重试上限 → 强制进入 Node 5
    """
    if state.get("review_passed"):
        logger.info("路由决策: 审查通过 → 进入终审排版")
        return "pass"

    retry_count = state.get("retry_count", 0)
    if retry_count >= MAX_RETRY_COUNT:
        logger.warning(
            "路由决策: 已达最大重试次数 (%d)，强制进入终审排版",
            MAX_RETRY_COUNT,
        )
        return "pass"

    logger.info("路由决策: 审查未通过 → 返回文案优化师 (retry %d/%d)", retry_count + 1, MAX_RETRY_COUNT)
    return "fail"
