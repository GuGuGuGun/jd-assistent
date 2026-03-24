"""
最终简历模型 — Node 5 (终审排版员) 的输出 Schema。
"""

from typing import List
from pydantic import BaseModel, Field


class ResumeSection(BaseModel):
    """简历区块。"""

    type: str = Field(
        ...,
        description='区块类型: "summary" | "experience" | "education" | "skills" | "certifications"',
    )
    title: str = Field(..., description="区块显示标题")
    items: List[dict] = Field(
        default_factory=list,
        description="区块内容条目",
    )


class RenderReadyResume(BaseModel):
    """最终交付的简历 JSON，前端直接 map 渲染。"""

    name: str = Field(..., description="姓名")
    contact: dict = Field(default_factory=dict, description="联系方式")
    summary: str = Field(..., description="个人总结（3-5 句话）")
    sections: List[ResumeSection] = Field(
        default_factory=list,
        description="按渲染顺序排列的简历区块",
    )
