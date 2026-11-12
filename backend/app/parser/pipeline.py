"""解析管线编排：按格式分发到 JSON 字段映射 / Markdown 状态机 → validator。

对外入口：

* :func:`run_parts`：**统一入口**，返回**按题库分片**的结果列表。
  ``.json`` 可能产出多个题库分片（``{"banks": [...]}``）；``.md/.markdown``
  恒为 1 个分片（一个 md = 一个题库）；``imports`` API 一律走本函数，
  一个分片落一条 ``import_files`` 记录。
* :func:`run`：``run_parts`` 的扁平包装（全部题目合成一个结果），便于单测/脚本。

v1.3 起 DOCX/TXT/PDF 的文本切分管线（reader → normalize → splitter → extractor）
已整体下线，仅保留 JSON 与 Markdown 两条结构化通道。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from . import jsonbank, markdown
from .extractor import Parsed
from .reader import ReaderError
from .validator import Anomaly, build_counter, validate


@dataclass
class ParsedItem:
    """单题解析结果 + 其异常列表。"""

    parsed: Parsed
    anomalies: List[Anomaly] = field(default_factory=list)


@dataclass
class PipelineResult:
    """一次文件解析的完整结果（``run_parts`` 的扁平视图）。"""

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


@dataclass
class PartResult:
    """一个「题库分片」的解析结果。

    Markdown 文件恒为单分片（``name`` = 原始文件名）；JSON 多题库包按题库分片，
    ``name`` 为「题库标题（作者）.json」。一个分片对应库里一条 ``import_files``
    记录，题目挂在该记录下，因此来源信息不会在合并导入时丢失。
    """

    name: str
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


def run(path: Path | str) -> PipelineResult:
    """解析单个文件（任一读取失败抛 :class:`ReaderError`）。

    JSON 题库文件会被展开为全部题库的题目合集（``block_count`` 取题目数）；
    需要保留题库来源时改用 :func:`run_parts`。
    """
    parts = run_parts(path)
    if len(parts) == 1:
        part = parts[0]
        return PipelineResult(
            items=part.items, line_count=part.line_count, block_count=part.block_count
        )
    items = [item for part in parts for item in part.items]
    return PipelineResult(items=items, line_count=0, block_count=len(items))


def run_parts(path: Path | str, display_name: str | None = None) -> List[PartResult]:
    """按题库分片解析单个文件（读取/格式错误抛 :class:`ReaderError`）。

    ``display_name``：落盘文件名可能被改成 UUID，传入原始上传文件名以保留可读
    来源；JSON 多题库包只在「单题库」时采用它（多题库用各自标题，更有信息量），
    Markdown 恒采用它。

    Args:
        path: 落盘后的文件路径。
        display_name: 原始上传文件名（展示用）。

    Returns:
        分片结果列表（``.json`` 可能多个，``.md/.markdown`` 恒 1 个）。

    Raises:
        ReaderError: 扩展名不受支持，或文件内容无法解析。
    """
    file_path = Path(path)
    suffix = file_path.suffix.lower()

    if suffix == ".json":
        banks = jsonbank.load_banks(file_path)
        if display_name and len(banks) == 1:
            banks[0].name = display_name
        return [_bank_part(bank) for bank in banks]

    if suffix in (".md", ".markdown"):
        banks = markdown.load_banks(file_path, display_name=display_name)
        return [_bank_part(bank) for bank in banks]

    raise ReaderError(f"不支持的文件类型：{suffix or '未知'}")


def _bank_part(bank: jsonbank.RawBank) -> PartResult:
    """把题库映射为分片结果（题号重排 + 异常校验）。

    题号重复处理：这类导出常见「按题型分段编号」（判断题 1..14、单选题 1..25、
    多选题 1..12 各自从 1 开始），重号是编号习惯而非解析错误。此时按文件内
    出现顺序重排为 1..n，既避免 duplicate_number 误报，也保证展示与导出里
    「题号」唯一可引用；无重号时原样保留原始题号。
    """
    numbers = [question.no for question in bank.questions]
    if len(set(numbers)) != len(numbers):
        for index, question in enumerate(bank.questions, start=1):
            question.no = str(index)

    counter = build_counter([question.no for question in bank.questions])
    items = [
        ParsedItem(parsed=question, anomalies=validate(question, counter))
        for question in bank.questions
    ]
    return PartResult(name=bank.name, items=items, line_count=0, block_count=len(items))
