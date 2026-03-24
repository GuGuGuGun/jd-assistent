"""
文件解析模块，用于从不同格式的简历文件中提取纯文本。
支持的格式：PDF, DOCX, MD, TXT
"""

import io
import logging

from .text_sanitizer import sanitize_resume_text

# 使用条件导入以防依赖未就绪
try:
    import pdfplumber
    import docx
except ImportError:
    pdfplumber = None
    docx = None

logger = logging.getLogger(__name__)


def parse_resume_file(filename: str, file_bytes: bytes) -> str:
    """
    根据文件扩展名解析简历文件内容。

    Args:
        filename: 文件名 (包含扩展名)
        file_bytes: 文件的二进制内容

    Returns:
        提取出的纯文本简历内容
    """
    if not filename:
        raise ValueError("缺少文件名")

    ext = filename.lower().split(".")[-1]

    # 1. 纯文本格式直接解码
    if ext in ["txt", "md", "markdown"]:
        try:
            return sanitize_resume_text(file_bytes.decode("utf-8"))
        except UnicodeDecodeError:
            # 尝试 fallback 编码
            return sanitize_resume_text(file_bytes.decode("gbk", errors="ignore"))

    # 2. PDF 解析
    elif ext == "pdf":
        if not pdfplumber:
            raise RuntimeError("未安装 PDF 解析依赖，请联系管理员")

        try:
            text_parts = []
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        text_parts.append(text)

            result_text = "\n".join(text_parts)
            if not result_text.strip():
                raise ValueError("PDF 解析结果为空，可能是扫描版图片 PDF")
            return sanitize_resume_text(result_text)
        except Exception as e:
            logger.error(f"解析 PDF 失败: {e}")
            raise ValueError(f"无法解析该 PDF 文件: {str(e)}")

    # 3. Word 文档解析
    elif ext in ["docx", "doc"]:
        if not docx:
            raise RuntimeError("未安装 Word 解析依赖，请联系管理员")

        if ext == "doc":
            logger.warning("旧版 .doc 格式可能无法完整支持，尝试作为 .docx 解析")

        try:
            doc = docx.Document(io.BytesIO(file_bytes))
            return sanitize_resume_text("\n".join([p.text for p in doc.paragraphs]))
        except Exception as e:
            logger.error(f"解析 Word 失败: {e}")
            raise ValueError(
                "无法解析该 Word 文件，仅支持 .docx 格式，请另存为后重试。"
            )

    else:
        raise ValueError(
            f"不支持的文件格式: .{ext}。请上传 PDF, Word (docx), 或 Markdown/Txt 简历。"
        )
