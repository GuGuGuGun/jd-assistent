"""
用户能力画像模型 — Node 1 (画像构建者) 的输出 Schema。
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class UserProfile(BaseModel):
    """用户能力画像 — 仅提取客观事实，不做任何润色。"""

    name: str = Field(..., description="姓名")
    contact: dict = Field(
        default_factory=dict,
        description="联系方式，如 {email, phone, linkedin}",
    )
    years_of_experience: float = Field(..., description="工作年限")
    education: List[dict] = Field(
        default_factory=list,
        description="教育背景，每条包含 school, degree, major, year",
    )
    skill_matrix: dict[str, List[str]] = Field(
        default_factory=dict,
        description='技能矩阵，如 {"frontend": ["Vue", "React"], "backend": ["Python"]}',
    )
    raw_experiences: List[dict] = Field(
        default_factory=list,
        description="原始工作经历，每条包含 company, title, duration, responsibilities, achievements",
    )
    certifications: Optional[List[str]] = Field(
        default=None, description="证书 / 资质"
    )
    languages: Optional[List[str]] = Field(
        default=None, description="语言能力"
    )
