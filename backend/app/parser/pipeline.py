"""解析管线编排：reader → normalize → splitter → extractor → validator。

对外只暴露 ``run(path)`` 与 ``run_lines(lines)``；``imports`` API 直接消费
``PipelineResult.items`` 落库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from . import reader, splitter
from .extractor import Parsed, extract
from .normalize import normalize
from .reader import Line
from .validator import Anomaly, build_counter, validate


@dataclass
class ParsedItem:
    """单题解析结果 + 其异常列表。"""

    parsed: Parsed
    anomalies: List[Anomaly] = field(default_factory=list)


@dataclass
class PipelineResult:
    """一次文件解析的完整结果。"""

    items: List[ParsedItem] = field(default_factory=list)
    line_count: int = 0
    block_count: int = 0

    @property
    def question_count(self) -> int:
        """题目数量。"""
        return len(self.items)

    @property
    def anomaly_count(self) -> int:
        """异常总条数。"""
        return sum(len(item.anomalies) for item in self.items)


def run_lines(raw_lines: List[Line]) -> PipelineResult:
    """对已读入的原始行执行完整管线（便于单测）。"""
    lines = normalize(raw_lines)
    blocks = splitter.split(lines)
    counter = build_counter([block.no_text for block in blocks])

    items: List[ParsedItem] = []
    for block in blocks:
        try:
            parsed = extract(block)
        except Exception as exc:  # 兜底，正常情况下 extract 内部已捕获
            parsed = Parsed(
                no=block.no_text,
                stem="",
                options={},
                answer="",
                analysis="",
                qtype="unknown",
                parse_error=True,
                error_detail=f"解析异常：{exc}",
            )
        items.append(ParsedItem(parsed=parsed, anomalies=validate(parsed, counter)))

    return PipelineResult(items=items, line_count=len(lines), block_count=len(blocks))


def run(path: Path | str) -> PipelineResult:
    """解析单个文件（任一读取失败抛 ReaderError）。"""
    raw_lines = reader.read(path)
    return run_lines(raw_lines)
