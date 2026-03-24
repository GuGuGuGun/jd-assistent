"""
Node 1: 画像构建者 (Profile Builder)
从原始简历中提取结构化的用户能力画像。
"""

import logging
from pathlib import Path
from typing import cast

from ..graph.state import ResumeGraphState
from ..schemas import UserProfile
from ..services.llm_service import LLMGenerateResult, safe_llm_generate

logger = logging.getLogger("jd_assistent.agents.profile_builder")

# 加载 Prompt 模板
_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "profile_builder.txt"
_PROMPT_TEMPLATE = _PROMPT_PATH.read_text(encoding="utf-8")


async def profile_builder_node(state: ResumeGraphState) -> dict:
    """
    画像构建者节点：提取用户能力画像。

    输入: state["original_resume_text"]
    输出: {"user_profile": dict}
    """
    logger.info("Node 1 [画像构建者] 开始执行")

    original_text = state["original_resume_text"]
    if not original_text.strip():
        raise ValueError("原始简历文本为空，无法提取画像")

    # 填充 Prompt
    prompt = _PROMPT_TEMPLATE.format(original_resume_text=original_text)

    # 调用 LLM（三重防线）
    generation = cast(
        LLMGenerateResult[UserProfile],
        await safe_llm_generate(
            prompt=prompt,
            schema=UserProfile,
            system_prompt="你是一位资深的人力资源专家，擅长从简历中提取结构化信息。",
            include_audit=True,
        ),
    )
    profile = generation.data

    logger.info(
        "Node 1 [画像构建者] 完成: 提取到 %d 条工作经历", len(profile.raw_experiences)
    )

    return {
        "user_profile": profile.model_dump(),
        "llm_audit": generation.audit,
    }
