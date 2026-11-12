"""异常校验层：6 类异常规则判定（枚举固定，架构 §6）。

| type              | 判定规则                                             |
|-------------------|------------------------------------------------------|
| missing_answer    | answer 为空                                          |
| missing_options   | qtype ∈ {single, multiple} 且选项数 = 0              |
| too_few_options   | qtype ∈ {single, multiple} 且 0 < 选项数 < 4         |
| empty_stem        | 题干去空白后长度 < 3                                 |
| duplicate_number  | 同一文件内题号重复（Counter > 1）                    |
| parse_error       | 切分/抽取抛异常或无题号可识别                        |
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Mapping

from ..config import MIN_OPTION_COUNT
from .extractor import Parsed

CHOICE_TYPES: frozenset[str] = frozenset({"single", "multiple"})


@dataclass
class Anomaly:
    """一条异常记录。"""

    type: str
    detail: str


def validate(parsed: Parsed, no_counter: Mapping[str, int] | None = None) -> List[Anomaly]:
    """按固定顺序返回该题的全部异常。"""
    anomalies: List[Anomaly] = []
    counter = no_counter or {}

    answer = (parsed.answer or "").strip()
    if not answer:
        anomalies.append(Anomaly("missing_answer", "未识别到答案，需人工补充"))

    option_count = len(parsed.options or {})
    if parsed.qtype in CHOICE_TYPES:
        if option_count == 0:
            anomalies.append(
                Anomaly("missing_options", f"题型判定为 {parsed.qtype}，但未识别到任何选项")
            )
        elif option_count < MIN_OPTION_COUNT:
            letters = "、".join(sorted((parsed.options or {}).keys()))
            anomalies.append(
                Anomaly(
                    "too_few_options",
                    f"仅识别到 {option_count} 个选项（{letters}），少于 {MIN_OPTION_COUNT} 个，疑似缺项",
                )
            )

    stem_length = len((parsed.stem or "").strip())
    if stem_length < 3:
        anomalies.append(Anomaly("empty_stem", f"题干长度仅 {stem_length} 字符，可能未正确解析"))

    if parsed.no:
        repeated = int(counter.get(parsed.no, 1))
        if repeated > 1:
            anomalies.append(
                Anomaly("duplicate_number", f"题号 {parsed.no} 在同一文件中出现 {repeated} 次")
            )

    if parsed.parse_error:
        detail = parsed.error_detail or "解析异常，需人工核对"
        anomalies.append(Anomaly("parse_error", detail))

    return anomalies


def build_counter(numbers: List[str]) -> dict[str, int]:
    """统计题号出现次数（空题号忽略）。"""
    counter: dict[str, int] = {}
    for number in numbers:
        key = (number or "").strip()
        if not key:
            continue
        counter[key] = counter.get(key, 0) + 1
    return counter
