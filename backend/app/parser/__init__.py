"""解析引擎包：结构化格式（JSON / Markdown）→ Parsed → validator → pipeline。

v1.3 起仅保留两种导入格式：

* :mod:`app.parser.jsonbank` —— JSON 字段映射（跳过文本切分）；
* :mod:`app.parser.markdown` —— Markdown 行级状态机（吃下自家导出的 md 并可往返）。

DOCX/TXT/PDF 的文本切分链路（``splitter`` / ``extract``）已整体下线。
"""

from __future__ import annotations

from .extractor import Parsed, make_parsed
from .normalize import normalize_text
from .pipeline import ParsedItem, PartResult, PipelineResult, run, run_parts
from .reader import ReaderError, decode_bytes
from .validator import Anomaly, validate

__all__ = [
    "Anomaly",
    "Parsed",
    "ParsedItem",
    "PartResult",
    "PipelineResult",
    "ReaderError",
    "decode_bytes",
    "make_parsed",
    "normalize_text",
    "run",
    "run_parts",
    "validate",
]
