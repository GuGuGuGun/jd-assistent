from __future__ import annotations

from ..utils.text_sanitizer import (
    sanitize_inline_text,
    sanitize_resume_payload,
    strip_markdown_bold,
)


def test_sanitize_inline_text_removes_star_markers_and_literal_line_break_escape():
    raw = "【S】负责系统设计\\R【R】性能提升 20%\ufeff"
    assert sanitize_inline_text(raw) == "负责系统设计\n性能提升 20%"


def test_sanitize_resume_payload_recursively_cleans_nested_strings():
    payload = {
        "summary": "【T】主导性能优化",
        "sections": [
            {
                "title": "经历",
                "items": [{"highlights": ["【A】引入缓存\\R【R】延迟下降"]}],
            }
        ],
    }

    cleaned = sanitize_resume_payload(payload)

    assert cleaned["summary"] == "主导性能优化"
    assert cleaned["sections"][0]["items"][0]["highlights"][0] == "引入缓存\n延迟下降"


def test_strip_markdown_bold_preserves_text_but_removes_markers():
    assert strip_markdown_bold("【S】实现 **核心接口**\\R") == "实现 核心接口"
