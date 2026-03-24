"""
LangGraph 工作流构建与编译。

工作流结构：
  START → [Node 1 (画像构建) + Node 2 (JD 分析)] → Node 3 (文案优化) →
  Node 4 (内容审查) → [通过 → Node 5 (终审排版) → END]
                      [未通过 → 返回 Node 3 (重试)]
"""

import logging
from typing import Any

from langgraph.graph import StateGraph, END

from .state import ResumeGraphState
from .routing import route_after_review
from ..services.checkpoint_store import get_checkpoint_store
from ..agents import (
    profile_builder_node,
    jd_analyst_node,
    content_optimizer_node,
    content_reviewer_node,
    final_typesetter_node,
)

logger = logging.getLogger("jd_assistent.graph.workflow")


def build_workflow() -> Any:
    """
    构建并编译 LangGraph 工作流。

    Returns:
        编译后可直接 invoke/ainvoke 的 CompiledGraph
    """
    graph = StateGraph(ResumeGraphState)

    # ═══ 添加节点 ═══
    graph.add_node("profile_builder", profile_builder_node)
    graph.add_node("jd_analyst", jd_analyst_node)
    graph.add_node("content_optimizer", content_optimizer_node)
    graph.add_node("content_reviewer", content_reviewer_node)
    graph.add_node("final_typesetter", final_typesetter_node)

    # ═══ 定义边 ═══

    # 起点：同时触发 Node 1 和 Node 2（并行）
    graph.set_entry_point("profile_builder")
    # 注: LangGraph 的 StateGraph 不原生支持多入口并行，
    # 因此我们使用顺序执行 Node1 → Node2 → Node3 的方式，
    # 两个节点之间无数据依赖，结果等价于并行。
    graph.add_edge("profile_builder", "jd_analyst")

    # Node 2 → Node 3
    graph.add_edge("jd_analyst", "content_optimizer")

    # Node 3 → Node 4
    graph.add_edge("content_optimizer", "content_reviewer")

    # Node 4 → 条件路由
    graph.add_conditional_edges(
        "content_reviewer",
        route_after_review,
        {
            "pass": "final_typesetter",
            "fail": "content_optimizer",
        },
    )

    # Node 5 → 结束
    graph.add_edge("final_typesetter", END)

    checkpoint_store = get_checkpoint_store()

    logger.info("工作流构建完成，checkpoint store=%s", type(checkpoint_store).__name__)
    return graph.compile(checkpointer=checkpoint_store)


# 全局编译实例（懒加载）
_compiled_workflow = None


def get_workflow():
    """获取编译后的工作流实例（单例）。"""
    global _compiled_workflow
    if _compiled_workflow is None:
        _compiled_workflow = build_workflow()
    return _compiled_workflow
