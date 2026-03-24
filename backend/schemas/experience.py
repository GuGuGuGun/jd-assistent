"""
优化后经历模型 — Node 3 (文案优化师) 的输出 Schema。
"""

from typing import List
from pydantic import BaseModel, Field


class OptimizedExperience(BaseModel):
    """单条优化后的工作经历。"""

    company: str = Field(..., description="公司名")
    title: str = Field(..., description="职位名（可微调以匹配 JD）")
    duration: str = Field(..., description="在职时间段")
    highlights: List[str] = Field(
        default_factory=list,
        description="优化后的亮点描述（遵循 STAR 法则）",
    )
    matched_keywords: List[str] = Field(
        default_factory=list,
        description="本条经历命中的 JD 关键词",
    )


class OptimizedContentList(BaseModel):
    """Node 3 的完整输出 — 优化后的经历数组。"""

    experiences: List[OptimizedExperience] = Field(
        default_factory=list,
        description="优化后的全部工作经历",
    )
