"""解析规则单元测试：规范化、题型判定、答案规范化、Parsed 构造、6 类异常。

v1.3 起 DOCX/TXT/PDF 的读取、切分、抽取用例已随文本管线下线（等价断言迁到
``test_markdown.py``，用 Markdown 样本重建）；本文件只覆盖**跨解析器共用**的规则：

* :func:`app.parser.normalize.normalize_text` —— 全角→半角、空白折叠；
* :func:`app.parser.extractor.decide_qtype` / ``normalize_answer`` / ``make_parsed``；
* :func:`app.parser.validator.validate` / ``build_counter`` —— 6 类异常规则。

运行：``cd backend && python -m pytest app/tests/test_parser.py -q``

注意：normalize 阶段会把全角标点（含 ``？`` ``，`` ``：``）统一为半角，
因此断言中的题干使用半角标点。
"""

from __future__ import annotations

import pytest

from app.parser.extractor import Parsed, decide_qtype, make_parsed, normalize_answer
from app.parser.normalize import normalize_text
from app.parser.validator import build_counter, validate


# ---------------------------------------------------------------------------
# 1. normalize_text
# ---------------------------------------------------------------------------
def test_normalize_fullwidth_to_halfwidth() -> None:
    """全角字母/数字/标点统一为半角。"""
    assert normalize_text("Ａ．房颤") == "A.房颤"
    assert normalize_text("答案：ＡＢＤ") == "答案:ABD"
    assert normalize_text("（　）") == "( )"
    assert normalize_text("心律失常？") == "心律失常?"


def test_normalize_trims_and_drops_blank() -> None:
    """首尾空白被清理，纯空白行归一为空串。"""
    assert normalize_text("  第一行  ") == "第一行"
    assert normalize_text("") == ""
    assert normalize_text("   ") == ""
    assert normalize_text("\u3000") == ""


def test_normalize_collapses_inner_spaces() -> None:
    """行内连续空格压缩为一个。"""
    assert normalize_text("A.   房颤") == "A. 房颤"


def test_normalize_is_idempotent() -> None:
    """幂等：重复规范化不再改变文本（Markdown 往返收敛的前提）。"""
    for text in ("Ａ．　房颤", "**答案**：ＡＢＤ", "（题干为空）"):
        once = normalize_text(text)
        assert normalize_text(once) == once


def test_normalize_keeps_chinese_period() -> None:
    """中文句号保留原样（不转为半角点，避免被误认为题号）。"""
    assert normalize_text("正常成人腋温 36.0℃。") == "正常成人腋温 36.0℃。"


def test_normalize_bracket_and_bom() -> None:
    """``【】`` → ``[]``，BOM 与零宽字符被丢弃。"""
    assert normalize_text("【单选题】") == "[单选题]"
    assert normalize_text("\ufeff## 1. [单选题]") == "## 1. [单选题]"
    assert normalize_text("a\u200bb") == "ab"


# ---------------------------------------------------------------------------
# 2. 题型判定与答案规范化
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("options", "answer", "expected"),
    [
        ({}, "对", "judge"),
        ({}, "√", "judge"),
        ({}, "错误", "judge"),
        ({"A": "甲", "B": "乙"}, "AB", "multiple"),
        ({"A": "甲", "B": "乙"}, "abd", "multiple"),
        ({"A": "甲", "B": "乙", "C": "丙", "D": "丁"}, "A", "single"),
        ({"A": "甲", "B": "乙"}, "", "unknown"),
        ({"A": "甲"}, "不确定内容", "unknown"),
    ],
)
def test_decide_qtype(options: dict[str, str], answer: str, expected: str) -> None:
    """题型判定优先级：judge → multiple → single → unknown。"""
    assert decide_qtype(options, answer) == expected


@pytest.mark.parametrize(
    ("answer", "qtype", "expected"),
    [
        ("对", "judge", "√"),
        ("正确", "judge", "√"),
        ("T", "judge", "√"),
        ("错", "judge", "×"),
        ("错误", "judge", "×"),
        ("F", "judge", "×"),
        ("dba", "multiple", "ABD"),
        ("ABD", "multiple", "ABD"),
        ("a", "single", "A"),
        ("", "single", ""),
    ],
)
def test_normalize_answer(answer: str, qtype: str, expected: str) -> None:
    """答案规范化：多选升序大写、判断统一 √/×。"""
    assert normalize_answer(answer, qtype) == expected


def test_make_parsed_sorts_options_and_upper_cases() -> None:
    """``make_parsed``：选项键排序并统一大写，答案按题型归一。"""
    parsed = make_parsed(
        no=7,
        stem="  题干  ",
        options={"d": "丁", "a": "甲", "b": "乙", "c": "丙"},
        answer="c",
        analysis="  解析  ",
        qtype="single",
    )
    assert parsed.no == "7"
    assert parsed.stem == "题干"
    assert parsed.analysis == "解析"
    assert list(parsed.options) == ["A", "B", "C", "D"]
    assert parsed.answer == "C"
    assert parsed.qtype == "single"


def test_make_parsed_infers_qtype_when_absent() -> None:
    """未指定题型时按答案自动判定（手录/编辑共用同一口径）。"""
    assert make_parsed("1", "题干", {"A": "甲", "B": "乙"}, "AB", "").qtype == "multiple"
    assert make_parsed("1", "题干", {}, "√", "").qtype == "judge"
    assert make_parsed("1", "题干", {"A": "甲"}, "", "").qtype == "unknown"


# ---------------------------------------------------------------------------
# 3. validator —— 6 类异常
# ---------------------------------------------------------------------------
def _parsed(**kwargs: object) -> Parsed:
    """快速构造 Parsed。"""
    base: dict[str, object] = {
        "no": "1",
        "stem": "正常题干内容",
        "options": {"A": "甲", "B": "乙", "C": "丙", "D": "丁"},
        "answer": "A",
        "analysis": "",
        "qtype": "single",
    }
    base.update(kwargs)
    return Parsed(**base)  # type: ignore[arg-type]


def test_validator_clean_question_has_no_anomaly() -> None:
    """正常题无异常。"""
    assert validate(_parsed(), {"1": 1}) == []


def test_validator_missing_answer() -> None:
    """answer 为空 → missing_answer。"""
    types = [item.type for item in validate(_parsed(answer=""), {"1": 1})]
    assert types == ["missing_answer"]


def test_validator_missing_options() -> None:
    """选择题无选项 → missing_options。"""
    types = [item.type for item in validate(_parsed(options={}), {"1": 1})]
    assert types == ["missing_options"]


def test_validator_too_few_options() -> None:
    """选择题 0<选项数<4 → too_few_options。"""
    types = [item.type for item in validate(_parsed(options={"A": "甲", "B": "乙"}), {"1": 1})]
    assert types == ["too_few_options"]


def test_validator_empty_stem() -> None:
    """题干长度 <3 → empty_stem。"""
    types = [item.type for item in validate(_parsed(stem="查"), {"1": 1})]
    assert types == ["empty_stem"]


def test_validator_duplicate_number() -> None:
    """同文件题号重复 → duplicate_number。"""
    anomalies = validate(_parsed(), {"1": 3})
    assert [item.type for item in anomalies] == ["duplicate_number"]
    assert "3" in anomalies[0].detail


def test_validator_parse_error() -> None:
    """parse_error 透传 detail。"""
    anomalies = validate(_parsed(parse_error=True, error_detail="抽取异常：x"), {"1": 1})
    assert [item.type for item in anomalies] == ["parse_error"]
    assert anomalies[0].detail == "抽取异常：x"


def test_validator_multiple_anomalies_order() -> None:
    """多异常按固定顺序返回。"""
    anomalies = validate(_parsed(answer="", options={"A": "甲"}), {"1": 2})
    assert [item.type for item in anomalies] == [
        "missing_answer",
        "too_few_options",
        "duplicate_number",
    ]


def test_judge_question_never_gets_option_anomaly() -> None:
    """判断题无选项不算异常。"""
    parsed = _parsed(options={}, answer="√", qtype="judge")
    assert validate(parsed, {"1": 1}) == []


def test_build_counter_ignores_blank_numbers() -> None:
    """题号计数器忽略空题号。"""
    assert build_counter(["1", "2", "2", "", "  "]) == {"1": 1, "2": 2}
