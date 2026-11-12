"""解析引擎包：reader → normalize → splitter → extractor → validator → pipeline。"""

from __future__ import annotations

from .extractor import Parsed, extract
from .normalize import normalize, normalize_text
from .pipeline import ParsedItem, PipelineResult, run, run_lines
from .reader import Line, ReaderError, read
from .splitter import Block, split
from .validator import Anomaly, validate

__all__ = [
    "Anomaly",
    "Block",
    "Line",
    "Parsed",
    "ParsedItem",
    "PipelineResult",
    "ReaderError",
    "extract",
    "normalize",
    "normalize_text",
    "read",
    "run",
    "run_lines",
    "split",
    "validate",
]
