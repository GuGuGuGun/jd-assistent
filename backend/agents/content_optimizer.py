"""
Node 3: 文案优化师 (Content Optimizer)
基于 STAR 法则重写经历，强制匹配 JD 关键词。
"""

import json
import logging
from pathlib import Path
from typing import cast

from ..graph.state import ResumeGraphState
from ..schemas import OptimizedContentList
from ..services.llm_service import LLMGenerateResult, safe_llm_generate

logger = logging.getLogger("jd_assistent.agents.content_optimizer")

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "content_optimizer.txt"
_PROMPT_TEMPLATE = _PROMPT_PATH.read_text(encoding="utf-8")


async def content_optimizer_node(state: ResumeGraphState) -> dict:
    """
    文案优化师节点：基于画像和 JD 分析结果，优化工作经历。

    输入: state["user_profile"], state["jd_analysis"], state["review_feedback"]
    输出: {"optimized_contents": List[dict], "retry_count": int}
    """
    logger.info(
        "Node 3 [文案优化师] 开始执行 (retry_count=%d)", state.get("retry_count", 0)
    )

    user_profile = state.get("user_profile")
    jd_analysis = state.get("jd_analysis")

    if not user_profile:
        raise ValueError("缺少用户画像数据，文案优化师无法执行")
    if not jd_analysis:
        raise ValueError("缺少 JD 分析数据，文案优化师无法执行")

    # 填充 Prompt
    review_feedback = state.get("review_feedback") or "无（首次生成）"
    prompt = _PROMPT_TEMPLATE.format(
        user_profile_json=json.dumps(user_profile, ensure_ascii=False, indent=2),
        jd_analysis_json=json.dumps(jd_analysis, ensure_ascii=False, indent=2),
        review_feedback=review_feedback,
    )

    generation = cast(
        LLMGenerateResult[OptimizedContentList],
        await safe_llm_generate(
            prompt=prompt,
            schema=OptimizedContentList,
            system_prompt="你是一位顶尖的技术简历文案优化师，擅长用 STAR 法则改写工作经历，但是务必保持简历内容真实。",
            include_audit=True,
        ),
    )
    result = generation.data

    optimized = [exp.model_dump() for exp in result.experiences]

    logger.info("Node 3 [文案优化师] 完成: 优化了 %d 条经历", len(optimized))

    # 更新重试计数
    retry_count = state.get("retry_count", 0)
    if state.get("review_feedback"):
        retry_count += 1

    return {
        "optimized_contents": optimized,
        "retry_count": retry_count,
        "llm_audit": generation.audit,
    }
