"""解析引擎包：reader → normalize → splitter → extractor → validator → pipeline。

JSON 题库走 :mod:`app.parser.jsonbank`（结构化字段映射，跳过文本切分）。
"""

from __future__ import annotations

from .extractor import Parsed, extract
from .normalize import normalize, normalize_text
from .pipeline import ParsedItem, PartResult, PipelineResult, run, run_lines, run_parts
from .reader import Line, ReaderError, read
from .splitter import Block, split
from .validator import Anomaly, validate

__all__ = [
    "Anomaly",
    "Block",
    "Line",
    "Parsed",
    "ParsedItem",
    "PartResult",
    "PipelineResult",
    "ReaderError",
    "extract",
    "normalize",
    "normalize_text",
    "read",
    "run",
    "run_lines",
    "run_parts",
    "split",
    "validate",
]
