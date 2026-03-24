"""
Node 4: 内容审查员 (Content Reviewer)
交叉比对优化内容与原始信息，拦截 AI 幻觉。
"""

import json
import logging
from pathlib import Path
from typing import List, Optional, cast

from ..graph.state import ResumeGraphState
from ..services.llm_service import LLMGenerateResult, safe_llm_generate

from pydantic import BaseModel, Field

logger = logging.getLogger("jd_assistent.agents.content_reviewer")

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "content_reviewer.txt"
_PROMPT_TEMPLATE = _PROMPT_PATH.read_text(encoding="utf-8")


class ReviewCheckItem(BaseModel):
    """单个审查检查项的结果。"""

    item: str = Field(..., description="检查项名称")
    status: str = Field(..., description="pass / fail / warning")
    detail: str = Field(..., description="具体判断依据")


class ReviewResult(BaseModel):
    """审查员的完整输出。"""

    passed: bool = Field(..., description="是否通过审查")
    feedback: str = Field(default="", description="修改意见（通过时为空）")
    checks: List[ReviewCheckItem] = Field(
        default_factory=list, description="逐项检查结果"
    )


async def content_reviewer_node(state: ResumeGraphState) -> dict:
    """
    内容审查员节点：交叉比对原始信息与优化内容。

    输入: state["user_profile"]["raw_experiences"], state["optimized_contents"]
    输出: {"review_passed": bool, "review_feedback": Optional[str]}
    """
    logger.info("Node 4 [内容审查员] 开始执行")

    user_profile = state.get("user_profile") or {}
    raw_experiences = user_profile.get("raw_experiences", [])
    optimized_contents = state.get("optimized_contents", [])

    if not optimized_contents:
        raise ValueError("缺少优化后的内容，审查员无法执行")

    prompt = _PROMPT_TEMPLATE.format(
        raw_experiences_json=json.dumps(raw_experiences, ensure_ascii=False, indent=2),
        optimized_contents_json=json.dumps(
            optimized_contents, ensure_ascii=False, indent=2
        ),
    )

    generation = cast(
        LLMGenerateResult[ReviewResult],
        await safe_llm_generate(
            prompt=prompt,
            schema=ReviewResult,
            system_prompt="你是一位严谨的简历质量审查专家，专注于发现 AI 幻觉和数据造假。",
            include_audit=True,
        ),
    )
    review = generation.data

    # 记录审查详情
    for check in review.checks:
        log_level = logging.WARNING if check.status != "pass" else logging.DEBUG
        logger.log(
            log_level, "审查项 [%s]: %s — %s", check.item, check.status, check.detail
        )

    if review.passed:
        logger.info("Node 4 [内容审查员] 审查通过 ✅")
    else:
        logger.warning("Node 4 [内容审查员] 审查未通过 ❌: %s", review.feedback)

    return {
        "review_passed": review.passed,
        "review_feedback": review.feedback if not review.passed else None,
        "llm_audit": generation.audit,
    }
