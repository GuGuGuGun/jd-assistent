"""
岗位需求分析模型 — Node 2 (JD 分析师) 的输出 Schema。
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class JobDescriptionAnalysis(BaseModel):
    """岗位需求深度拆解。"""

    job_title: str = Field(..., description="岗位名称")
    company_info: Optional[str] = Field(
        default=None, description="公司/团队信息"
    )
    must_have_skills: List[str] = Field(
        default_factory=list, description="硬性技能要求"
    )
    nice_to_have_skills: List[str] = Field(
        default_factory=list, description="加分项技能"
    )
    pain_points: List[str] = Field(
        default_factory=list, description="业务痛点 / 团队当前面临的挑战"
    )
    responsibility_keywords: List[str] = Field(
        default_factory=list, description="职责关键词（用于后续关键词匹配）"
    )
    experience_range: Optional[str] = Field(
        default=None, description="经验年限要求"
    )
    culture_keywords: Optional[List[str]] = Field(
        default=None, description="文化/软技能关键词"
    )
