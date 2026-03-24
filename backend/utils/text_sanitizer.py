"""
文本清洗工具。

设计意图：
1. 清理复制/模型生成过程中常见的不可见字符与控制字符。
2. 去掉 STAR 标记残留（如【S】、【T】），避免它们直接出现在最终简历与导出文档里。
3. 保留普通中文、英文、数字、标点和 Markdown 粗体标记，避免过度清洗。
"""

from __future__ import annotations

import re
from typing import Any

_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u2060\ufeff\ufffc]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_STAR_MARKER_RE = re.compile(r"(?<!\w)[【\[]\s*([SsTtAaRr])\s*[】\]]\s*[:：]?\s*")
_LITERAL_ESCAPED_BREAK_RE = re.compile(r"\\[Rr]")
_MULTI_BLANK_LINES_RE = re.compile(r"\n{3,}")
_SPACE_BEFORE_NEWLINE_RE = re.compile(r"[ \t]+\n")


def sanitize_inline_text(value: str) -> str:
    """清洗单段文本，但保留换行与 Markdown 粗体语义。"""
    if not value:
        return ""

    text = str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _LITERAL_ESCAPED_BREAK_RE.sub("\n", text)
    text = _ZERO_WIDTH_RE.sub("", text)
    text = _CONTROL_RE.sub("", text)
    text = _STAR_MARKER_RE.sub("", text)
    text = _SPACE_BEFORE_NEWLINE_RE.sub("\n", text)
    text = _MULTI_BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


def sanitize_resume_text(value: str) -> str:
    """用于原始简历/JD 文本的清洗入口。"""
    return sanitize_inline_text(value)


def sanitize_resume_payload(value: Any) -> Any:
    """递归清洗最终简历 JSON，兼容 dict/list/str 结构。"""
    if isinstance(value, str):
        return sanitize_inline_text(value)
    if isinstance(value, list):
        return [sanitize_resume_payload(item) for item in value]
    if isinstance(value, dict):
        return {key: sanitize_resume_payload(item) for key, item in value.items()}
    return value


def strip_markdown_bold(value: str) -> str:
    """导出文档时移除 **粗体** 标记，但保留文字本身。"""
    cleaned = sanitize_inline_text(value)
    return cleaned.replace("**", "")
