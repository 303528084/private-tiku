"""文本读取层：DOCX/TXT/PDF → 带序号与页码的原始文本行。

统一返回 ``list[Line]``，其中 ``seq`` 为从 0 开始的全局行号，``page`` 仅 PDF 有值。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import chardet

# 优先尝试的编码顺序：探测结果 → utf-8-sig → utf-8 → gb18030（覆盖 GBK/GB2312）
_FALLBACK_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1")


class ReaderError(Exception):
    """文件读取/解码失败（对应 API 错误码 50001）。"""


@dataclass
class Line:
    """一行文本。"""

    seq: int
    text: str
    page: Optional[int] = None


def read(path: Path | str) -> List[Line]:
    """按扩展名分发读取，返回原始文本行。"""
    file_path = Path(path)
    if not file_path.is_file():
        raise ReaderError(f"文件不存在：{file_path}")
    suffix = file_path.suffix.lower()
    if suffix == ".txt":
        return _read_txt(file_path)
    if suffix == ".docx":
        return _read_docx(file_path)
    if suffix == ".pdf":
        return _read_pdf(file_path)
    raise ReaderError(f"不支持的文件类型：{suffix or '未知'}")


# ---------------------------------------------------------------------------
# TXT
# ---------------------------------------------------------------------------
def _decode_bytes(raw: bytes) -> tuple[str, str]:
    """探测并解码字节流，返回 (文本, 使用的编码)。"""
    candidates: List[str] = []
    detected = chardet.detect(raw[:65536]) or {}
    encoding = detected.get("encoding")
    confidence = float(detected.get("confidence") or 0.0)
    if encoding and confidence >= 0.6:
        candidates.append(encoding)
    candidates.extend(_FALLBACK_ENCODINGS)
    seen: set[str] = set()
    for enc in candidates:
        key = enc.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"


def _read_txt(path: Path) -> List[Line]:
    """读取纯文本文件，自动探测 GBK/UTF-8 等编码。"""
    try:
        raw = path.read_bytes()
    except OSError as exc:  # pragma: no cover - 磁盘异常
        raise ReaderError(f"读取文本文件失败：{exc}") from exc
    if not raw:
        raise ReaderError("文件内容为空")
    text, _encoding = _decode_bytes(raw)
    return _lines_from_text(text)


def _lines_from_text(text: str) -> List[Line]:
    """把整段文本按行切分为 Line 列表。"""
    normalized_newlines = text.replace("\r\n", "\n").replace("\r", "\n")
    result: List[Line] = []
    for index, raw_line in enumerate(normalized_newlines.split("\n")):
        result.append(Line(seq=index, text=raw_line, page=None))
    return result


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------
def _read_docx(path: Path) -> List[Line]:
    """使用 python-docx 读取段落与表格文本。"""
    try:
        from docx import Document  # 延迟导入，减少无 docx 场景的启动开销
    except ImportError as exc:  # pragma: no cover - 依赖缺失
        raise ReaderError("未安装 python-docx，无法解析 DOCX") from exc

    try:
        document = Document(str(path))
    except Exception as exc:  # python-docx 内部异常类型较多，统一收敛
        raise ReaderError(f"DOCX 解析失败：{exc}") from exc

    texts: List[str] = []
    for paragraph in document.paragraphs:
        texts.append(paragraph.text or "")
    for table in document.tables:
        for row in table.rows:
            cells = [(cell.text or "").strip() for cell in row.cells]
            joined = " ".join(cell for cell in cells if cell)
            if joined:
                texts.append(joined)

    if not any(text.strip() for text in texts):
        raise ReaderError("DOCX 无可提取文本")

    return [Line(seq=i, text=text, page=None) for i, text in enumerate(texts)]


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def _read_pdf(path: Path) -> List[Line]:
    """使用 pdfplumber 逐页提取文本行（仅支持文本型 PDF）。"""
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - 依赖缺失
        raise ReaderError("未安装 pdfplumber，无法解析 PDF") from exc

    lines: List[Line] = []
    seq = 0
    try:
        with pdfplumber.open(str(path)) as pdf:
            for page_index, page in enumerate(pdf.pages, start=1):
                page_text = page.extract_text() or ""
                for raw_line in page_text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
                    lines.append(Line(seq=seq, text=raw_line, page=page_index))
                    seq += 1
    except ReaderError:
        raise
    except Exception as exc:
        raise ReaderError(f"PDF 解析失败：{exc}") from exc

    if not any(line.text.strip() for line in lines):
        raise ReaderError("PDF 无可提取文本（可能为扫描件，本期不做 OCR）")
    return lines
