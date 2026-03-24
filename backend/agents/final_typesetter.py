"""
Node 5: 终审排版员 (Final Typesetter)
格式规范化 + 组装为前端可直接渲染的 JSON。
"""

import json
import logging
from pathlib import Path
from typing import cast

from ..graph.state import ResumeGraphState
from ..schemas import RenderReadyResume
from ..services.llm_service import LLMGenerateResult, safe_llm_generate
from ..utils.text_sanitizer import sanitize_resume_payload

logger = logging.getLogger("jd_assistent.agents.final_typesetter")

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "final_typesetter.txt"
_PROMPT_TEMPLATE = _PROMPT_PATH.read_text(encoding="utf-8")


async def final_typesetter_node(state: ResumeGraphState) -> dict:
    """
    终审排版员节点：组装可渲染的最终简历 JSON。

    输入: state["user_profile"], state["optimized_contents"] (review_passed 必须为 True)
    输出: {"final_resume": dict}
    """
    logger.info("Node 5 [终审排版员] 开始执行")

    if not state.get("review_passed"):
        logger.warning("审查未通过但仍然进入排版节点，可能是超过重试上限的强制通过")

    user_profile = state.get("user_profile", {})
    optimized_contents = state.get("optimized_contents", [])

    prompt = _PROMPT_TEMPLATE.format(
        user_profile_json=json.dumps(user_profile, ensure_ascii=False, indent=2),
        optimized_contents_json=json.dumps(
            optimized_contents, ensure_ascii=False, indent=2
        ),
    )

    generation = cast(
        LLMGenerateResult[RenderReadyResume],
        await safe_llm_generate(
            prompt=prompt,
            schema=RenderReadyResume,
            system_prompt="你是一位专业的简历排版编辑，擅长将内容组装为标准化的可渲染结构。",
            include_audit=True,
        ),
    )
    resume = generation.data

    logger.info(
        "Node 5 [终审排版员] 完成: %s, 共 %d 个区块",
        resume.name,
        len(resume.sections),
    )

    final_resume = sanitize_resume_payload(resume.model_dump())

    return {
        "final_resume": final_resume,
        "llm_audit": generation.audit,
    }
