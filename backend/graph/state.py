"""
LangGraph 全局状态定义 — 所有 Agent 节点的数据总线。
"""

from typing import TypedDict, List, Optional


class ResumeGraphState(TypedDict):
    """LangGraph 全局状态，所有节点的输入输出均通过此 State 传递。"""

    # ═══ 用户输入 ═══
    original_resume_text: str  # 用户上传的原始简历纯文本
    target_jd_text: str  # 目标岗位的 JD 纯文本

    # ═══ Node 1 产出 ═══
    user_profile: Optional[dict]  # 结构化能力画像 (UserProfile)

    # ═══ Node 2 产出 ═══
    jd_analysis: Optional[dict]  # 岗位需求分析 (JobDescriptionAnalysis)

    # ═══ Node 3 产出 ═══
    optimized_contents: List[dict]  # 优化后的经历数组 (OptimizedExperience[])

    # ═══ Node 4 产出 ═══
    review_feedback: Optional[str]  # 审查员的修改意见（为空则通过）
    review_passed: bool  # 审查是否通过

    # ═══ 系统控制 ═══
    retry_count: int  # 当前重试次数（上限 3）

    # ═══ 最终交付 ═══
    final_resume: Optional[dict]  # 终审后可渲染的完整简历 JSON

    # ═══ 审计信息 ═══
    llm_audit: Optional[dict]  # 当前节点的 LLM 调用审计数据


# 初始化默认值
INITIAL_STATE: ResumeGraphState = {
    "original_resume_text": "",
    "target_jd_text": "",
    "user_profile": None,
    "jd_analysis": None,
    "optimized_contents": [],
    "review_feedback": None,
    "review_passed": False,
    "retry_count": 0,
    "final_resume": None,
    "llm_audit": None,
}
