"""
Node 2: JD 分析师 (Job Description Analyst)
拆解岗位描述中的硬性要求、软技能及业务痛点。
"""

import logging
from pathlib import Path
from typing import cast

from ..graph.state import ResumeGraphState
from ..schemas import JobDescriptionAnalysis
from ..services.llm_service import LLMGenerateResult, safe_llm_generate

logger = logging.getLogger("jd_assistent.agents.jd_analyst")

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "jd_analyst.txt"
_PROMPT_TEMPLATE = _PROMPT_PATH.read_text(encoding="utf-8")


async def jd_analyst_node(state: ResumeGraphState) -> dict:
    """
    JD 分析师节点：深度拆解岗位描述。

    输入: state["target_jd_text"]
    输出: {"jd_analysis": dict}
    """
    logger.info("Node 2 [JD 分析师] 开始执行")

    jd_text = state["target_jd_text"]
    if not jd_text.strip():
        raise ValueError("JD 文本为空，无法进行分析")

    prompt = _PROMPT_TEMPLATE.format(target_jd_text=jd_text)

    generation = cast(
        LLMGenerateResult[JobDescriptionAnalysis],
        await safe_llm_generate(
            prompt=prompt,
            schema=JobDescriptionAnalysis,
            system_prompt="你是一位资深的招聘顾问，擅长从招聘方视角分析岗位需求。",
            include_audit=True,
        ),
    )
    analysis = generation.data

    logger.info(
        "Node 2 [JD 分析师] 完成: 岗位=%s, 硬性要求=%d 项",
        analysis.job_title,
        len(analysis.must_have_skills),
    )

    return {
        "jd_analysis": analysis.model_dump(),
        "llm_audit": generation.audit,
    }
