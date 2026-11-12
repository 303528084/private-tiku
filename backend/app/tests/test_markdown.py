"""Markdown 解析器测试：变体矩阵 / 往返一致性 / 行号报错 / 判断题折叠。

本文件承担 v1.3 解析层的主要覆盖，取代已下线的文本管线用例（``test_accuracy.py``
与 ``test_parser.py`` 里的 reader/splitter/extract 部分）：

1. **变体矩阵**：``sample_variants.md`` 覆盖 PRD §v1.3.4 的全部「可容忍变体」
   （题号 4 种写法、题型标记 4 种括号、选项 5 种写法、答案 5 种前缀、
   判断题 6 种答案词、解析 4 种前缀、缺省题型推断）；
2. **往返强断言**：「导出 md → 导入 → 再导出」除 ``- 导出时间：`` 一行外**逐字节相等**，
   并额外断言二次往返收敛（``md2 == md3``）；
3. **报错定位**：无 ``## `` 标题时报「第 N 行：未找到题目标题，需要 `## 1. [单选题]` 形式」；
4. **判断题折叠**：``A/B = 正确/错误`` 折叠为「无选项 + √/×」；
5. **重号与空块**：重号按顺序重排 1..n（不报 duplicate_number）、只有标题的题目块落
   ``parse_error`` 但整个文件仍算解析成功。
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any, Dict, List

import pytest

from app.api.export import _render_markdown
from app.parser import markdown, pipeline
from app.parser.reader import ReaderError

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BANK_NAME = "roundtrip.md"


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def build_md(items: List[Dict[str, Any]], title: str = "测试题库") -> str:
    """用「已知字段」拼一份导出格式的 Markdown（不含异常标记行）。"""
    lines: List[str] = [f"# {title}", "", "- 导出时间：2025-01-01 10:00:00", ""]
    lines.append(f"- 题目总数：{len(items)}")
    lines.append("")
    for item in items:
        lines.append(f"## {item['no']}. [{item['label']}]")
        lines.append("")
        lines.append(item["stem"] or "（题干为空）")
        lines.append("")
        for letter, text in item.get("options", {}).items():
            lines.append(f"- **{letter}.** {text}")
        if item.get("options"):
            lines.append("")
        lines.append(f"**答案**：{item.get('answer') or '（无答案）'}")
        if item.get("analysis"):
            lines.append("")
            lines.append(f"**解析**：{item['analysis']}")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def questions_from_md(text: str) -> List[Dict[str, Any]]:
    """解析 Markdown 并按「库里的一条题目」形状还原（供再导出比对）。"""
    bank = markdown.parse_bank(text, BANK_NAME)
    part = pipeline._bank_part(bank)
    result: List[Dict[str, Any]] = []
    for index, item in enumerate(part.items, start=1):
        parsed = item.parsed
        result.append(
            {
                "id": index,
                "import_file_id": 1,
                "question_no": parsed.no,
                "qtype": parsed.qtype,
                "stem": parsed.stem,
                "options": parsed.options,
                "answer": parsed.answer,
                "analysis": parsed.analysis,
                "anomalies": [{"type": anomaly.type} for anomaly in item.anomalies],
                "filename": BANK_NAME,
            }
        )
    return result


def assert_byte_equal_except_timestamp(left: str, right: str) -> None:
    """断言两份导出文本除「- 导出时间：」一行外逐字节相等。"""
    a_all = left.split("\n")
    b_all = right.split("\n")
    timestamps_a = [line for line in a_all if line.startswith("- 导出时间：")]
    timestamps_b = [line for line in b_all if line.startswith("- 导出时间：")]
    assert len(timestamps_a) == len(timestamps_b) == 1, (timestamps_a, timestamps_b)

    a = [line for line in a_all if not line.startswith("- 导出时间：")]
    b = [line for line in b_all if not line.startswith("- 导出时间：")]
    assert len(a) == len(b), f"行数不同：{len(a)} vs {len(b)}"
    assert a == b, "\n".join(difflib.unified_diff(a, b, lineterm="", n=2))


# ===========================================================================
# 1. 基础样本端到端
# ===========================================================================
def test_sample_basic_end_to_end() -> None:
    """``sample_basic.md``：7 题、2 条异常（选项不足 + 缺答案）。"""
    parts = pipeline.run_parts(FIXTURES / "sample_basic.md")
    assert len(parts) == 1
    part = parts[0]
    assert part.name == "sample_basic.md"
    assert part.question_count == 7

    numbers = [item.parsed.no for item in part.items]
    assert numbers == ["1", "2", "3", "4", "5", "6", "7"]

    first = part.items[0].parsed
    assert first.stem == "脉搏短绌常见于下列哪种心律失常?"
    assert first.options == {"A": "房颤", "B": "室颤", "C": "房扑", "D": "期前收缩"}
    assert (first.answer, first.qtype) == ("A", "single")
    assert first.analysis.startswith("脉搏短绌是心房颤动的典型体征")

    second = part.items[1].parsed
    assert (second.answer, second.qtype) == ("ABD", "multiple")
    third = part.items[2].parsed
    assert (third.answer, third.qtype, third.options) == ("√", "judge", {})
    seventh = part.items[6].parsed
    assert (seventh.answer, seventh.qtype) == ("×", "judge")

    kinds = [anomaly.type for item in part.items for anomaly in item.anomalies]
    assert sorted(kinds) == ["missing_answer", "too_few_options"]
    # 缺答案的是第 6 题、选项不足的是第 4 题
    assert [a.type for a in part.items[3].anomalies] == ["too_few_options"]
    assert [a.type for a in part.items[5].anomalies] == ["missing_answer"]
    # 没有任何 parse_error（文件整体健康）
    assert "parse_error" not in kinds


def test_load_banks_uses_display_name(tmp_path: Path) -> None:
    """``load_banks`` 的题库名取 ``display_name``（上传原始文件名）。"""
    target = tmp_path / "uuid.bin"
    target.write_text((FIXTURES / "sample_basic.md").read_text(encoding="utf-8"), encoding="utf-8")
    banks = markdown.load_banks(target, display_name="护理学基础.md")
    assert len(banks) == 1
    assert banks[0].name == "护理学基础.md"
    # 不给 display_name 时退化为磁盘文件名
    assert markdown.load_banks(target)[0].name == "uuid.bin"


# ===========================================================================
# 2. 变体矩阵（PRD §v1.3.4 可容忍变体）
# ===========================================================================
def test_sample_variants_matrix() -> None:
    """手写变体样本：9 题全部可解，且题型/答案/选项逐项正确。"""
    part = pipeline.run_parts(FIXTURES / "sample_variants.md")[0]
    assert part.question_count == 9
    parsed = [item.parsed for item in part.items]

    # 1) `## 1、[单选题]` + 选项 4 种写法（- **A.** / - B. / - **C、** / D：）+ `答案：a`
    assert parsed[0].no == "1"
    assert (parsed[0].qtype, parsed[0].answer) == ("single", "A")
    assert parsed[0].options == {
        "A": "心房颤动",
        "B": "心室颤动",
        "C": "心房扑动",
        "D": "期前收缩",
    }

    # 2) `## 2 [多选题]`（无分隔符）+ `正确答案：A、B、D`（顿号分隔）
    assert (parsed[1].no, parsed[1].qtype, parsed[1].answer) == ("2", "multiple", "ABD")

    # 3) `## 第3题 [判断题]` + `对`
    assert (parsed[2].no, parsed[2].qtype, parsed[2].answer) == ("3", "judge", "√")

    # 4) `## 4. （判断题）` + A/B=正确/错误 → 折叠为无选项 + ×
    assert (parsed[3].no, parsed[3].qtype) == ("4", "judge")
    assert parsed[3].options == {}
    assert parsed[3].answer == "×"

    # 5) `## 5. 【单选题】` + 无列表符 `A. x` + `**答案**:A`
    assert (parsed[4].no, parsed[4].qtype, parsed[4].answer) == ("5", "single", "A")
    assert parsed[4].options["D"] == "便于实施治疗"

    # 6) `## 第6题. (单选题)` 声明单选但答案 3 个字母 → 以答案为准改判多选
    assert (parsed[5].no, parsed[5].qtype, parsed[5].answer) == ("6", "multiple", "ABC")

    # 7) 英文题型标记 `[judge]` + `答案：T`
    assert (parsed[6].qtype, parsed[6].answer) == ("judge", "√")

    # 8) `- **A、**` 写法 + 多行解析合并（单空格拼接）
    assert parsed[7].options["A"] == "操作前洗手戴口罩"
    assert parsed[7].answer == "ABCD"
    assert parsed[7].analysis == "四项均为无菌技术基本原则, 第二行解析应并入同一段落。"

    # 9) 缺省题型（无标记）→ 按答案字母个数推断为多选
    assert (parsed[8].no, parsed[8].qtype, parsed[8].answer) == ("9", "multiple", "AB")


def test_judge_answer_words_are_folded() -> None:
    """判断题答案词：√/×/对/错/正确/错误/T/F 全部归一为 √/×。"""
    cases = {
        "√": "√", "×": "×", "对": "√", "错": "×",
        "正确": "√", "错误": "×", "T": "√", "F": "×",
    }
    for word, expected in cases.items():
        text = f"## 1. [判断题]\n\n题干内容足够长。\n\n**答案**：{word}\n"
        parsed = markdown.parse_bank(text, "x.md").questions[0]
        assert parsed.qtype == "judge", word
        assert parsed.answer == expected, word
        assert parsed.options == {}, word


def test_judge_options_fold_only_when_contents_are_boolean() -> None:
    """只有 A/B 内容确为「正确/错误」时才折叠；普通二选项选择题不受影响。"""
    folded = markdown.parse_bank(
        "## 1. [判断题]\n\n题干内容足够长。\n\n- **A.** 正确\n- **B.** 错误\n\n**答案**：A\n",
        "x.md",
    ).questions[0]
    assert folded.options == {}
    assert (folded.qtype, folded.answer) == ("judge", "√")

    kept = markdown.parse_bank(
        "## 1. [单选题]\n\n题干内容足够长。\n\n- **A.** 甲\n- **B.** 乙\n\n**答案**：A\n",
        "x.md",
    ).questions[0]
    assert kept.options == {"A": "甲", "B": "乙"}
    assert (kept.qtype, kept.answer) == ("single", "A")


def test_missing_type_tag_inferred_from_answer() -> None:
    """题型标记缺失时，按「有无选项 + 答案字母个数」推断。"""
    single = markdown.parse_bank(
        "## 1\n\n题干内容足够长。\n\nA. 甲\nB. 乙\nC. 丙\nD. 丁\n\n**答案**：B\n", "x.md"
    ).questions[0]
    assert (single.qtype, single.answer) == ("single", "B")

    judge = markdown.parse_bank("## 1\n\n题干内容足够长。\n\n**答案**：错\n", "x.md").questions[0]
    assert (judge.qtype, judge.answer) == ("judge", "×")


def test_source_and_anomaly_marker_lines_are_dropped() -> None:
    """``**来源**`` 与 ``**异常标记**`` 行导入时丢弃（来源取文件名、异常重算）。"""
    text = (
        "## 1. [单选题]\n\n题干内容足够长?\n\n- **A.** 甲\n- **B.** 乙\n- **C.** 丙\n- **D.** 丁\n\n"
        "**答案**：A\n\n**异常标记**：缺答案、选项不足\n\n**来源**：护基1.docx\n\n---\n"
    )
    parsed = markdown.parse_bank(text, "上传.md").questions[0]
    assert parsed.stem == "题干内容足够长?"
    assert parsed.answer == "A"
    assert parsed.analysis == ""
    assert parsed.parse_error is False


def test_encoding_variants_bom_crlf_and_gbk(tmp_path: Path) -> None:
    """BOM / CRLF / GBK 编码都能正确解析。"""
    body = "## 1. [单选题]\n\n慢性阻塞性肺疾病患者氧疗应采用?\n\n- **A.** 低流量持续给氧\n- **B.** 高流量间断给氧\n- **C.** 纯氧吸入\n- **D.** 无需吸氧\n\n**答案**：A\n"

    utf8_bom = tmp_path / "bom.md"
    utf8_bom.write_bytes(b"\xef\xbb\xbf" + body.replace("\n", "\r\n").encode("utf-8"))
    parsed = markdown.load_banks(utf8_bom)[0].questions[0]
    assert parsed.stem == "慢性阻塞性肺疾病患者氧疗应采用?"
    assert parsed.answer == "A"

    gbk = tmp_path / "gbk.md"
    gbk.write_bytes(body.encode("gb18030"))
    parsed_gbk = markdown.load_banks(gbk)[0].questions[0]
    assert parsed_gbk.stem == "慢性阻塞性肺疾病患者氧疗应采用?"
    assert parsed_gbk.options["A"] == "低流量持续给氧"


# ===========================================================================
# 3. 往返强断言：导出 md → 导入 → 再导出（逐字节相等）
# ===========================================================================
ROUNDTRIP_ITEMS: List[Dict[str, Any]] = [
    {
        "no": "1",
        "label": "单选题",
        "stem": "脉搏短绌最常见于下列哪种心律失常?",
        "options": {"A": "心房颤动", "B": "心室颤动", "C": "心房扑动", "D": "期前收缩"},
        "answer": "A",
        "analysis": "脉搏短绌指脉率少于心率,为房颤的典型体征。",
    },
    {
        "no": "2",
        "label": "判断题",
        "stem": "正常成人腋温范围为36.0~37.0℃。",
        "options": {},
        "answer": "√",
        "analysis": "",
    },
    {
        "no": "3",
        "label": "多选题",
        "stem": "心肺复苏有效指征包括?",
        "options": {"A": "可触及大动脉搏动", "B": "面色由紫绀转红润", "C": "瞳孔由大变小"},
        "answer": "AB",
        "analysis": "",
    },
    {
        "no": "4",
        "label": "未知题型",
        "stem": "",
        "options": {"A": "甲", "B": "乙", "C": "丙", "D": "丁"},
        "answer": "",
        "analysis": "",
    },
]


def test_markdown_roundtrip_is_byte_identical() -> None:
    """导出 → 导入 → 再导出：除「导出时间」外逐字节相等，且二次往返不再变化。"""
    seed = build_md(ROUNDTRIP_ITEMS)
    first_pass = questions_from_md(seed)
    assert len(first_pass) == len(ROUNDTRIP_ITEMS)

    md1 = _render_markdown(first_pass)
    second_pass = questions_from_md(md1)
    md2 = _render_markdown(second_pass)

    assert_byte_equal_except_timestamp(md1, md2)

    # 题量与逐题字段必须完全一致（题干/选项/答案/解析/题型/题号/异常标签）
    assert len(first_pass) == len(second_pass)
    for before, after in zip(first_pass, second_pass):
        for field in ("question_no", "qtype", "stem", "options", "answer", "analysis", "anomalies"):
            assert before[field] == after[field], (field, before, after)

    # 二次往返仍收敛（幂等）
    md3 = _render_markdown(questions_from_md(md2))
    assert_byte_equal_except_timestamp(md2, md3)
    assert md2 == md3.replace(md3.split("\n")[2], md2.split("\n")[2])


def test_markdown_roundtrip_preserves_placeholders() -> None:
    """占位符 ``（题干为空）`` / ``（无答案）`` 往返还原为空值并重新占位。"""
    seed = build_md(
        [
            {"no": "1", "label": "未知题型", "stem": "", "options": {}, "answer": "", "analysis": ""},
        ]
    )
    md1 = _render_markdown(questions_from_md(seed))
    assert "（题干为空）" in md1
    assert "**答案**：（无答案）" in md1

    parsed = markdown.parse_bank(md1, BANK_NAME).questions[0]
    assert parsed.stem == ""
    assert parsed.answer == ""
    kinds = [anomaly.type for anomaly in pipeline._bank_part(
        markdown.parse_bank(md1, BANK_NAME)
    ).items[0].anomalies]
    assert kinds == ["missing_answer", "empty_stem"]

    md2 = _render_markdown(questions_from_md(md1))
    assert_byte_equal_except_timestamp(md1, md2)


def test_export_of_imported_markdown_does_not_drift_analysis() -> None:
    """解析文本单空格拼接，保证二次往返时解析字段不漂移。"""
    text = (
        "## 1. [单选题]\n\n题干内容足够长?\n\n- **A.** 甲\n- **B.** 乙\n- **C.** 丙\n- **D.** 丁\n\n"
        "**答案**：A\n\n**解析**：第一行解析\n第二行解析\n\n---\n"
    )
    first = markdown.parse_bank(text, BANK_NAME).questions[0]
    assert first.analysis == "第一行解析 第二行解析"

    rebuilt = _render_markdown(
        [
            {
                "id": 1,
                "import_file_id": 1,
                "question_no": first.no,
                "qtype": first.qtype,
                "stem": first.stem,
                "options": first.options,
                "answer": first.answer,
                "analysis": first.analysis,
                "anomalies": [],
                "filename": BANK_NAME,
            }
        ]
    )
    second = markdown.parse_bank(rebuilt, BANK_NAME).questions[0]
    assert second.analysis == first.analysis
    assert second.stem == first.stem
    assert second.options == first.options
    assert second.answer == first.answer


# ===========================================================================
# 4. 报错定位
# ===========================================================================
def test_no_heading_reports_first_non_empty_line() -> None:
    """全文无 ``## `` 标题 → 报首个非空行行号与期望写法。"""
    text = "\n\n\n这不是题目标题\n\n无菌技术操作原则包括?\n"
    with pytest.raises(ReaderError) as excinfo:
        markdown.parse_bank(text, "bad.md")
    message = str(excinfo.value)
    assert message == "第 4 行：未找到题目标题，需要 `## 1. [单选题]` 形式"


def test_sample_messy_fixture_reports_line_one() -> None:
    """``sample_messy.md`` 整体报错（等同于 v1.2 的「无题号文档整体失败」）。"""
    with pytest.raises(ReaderError) as excinfo:
        pipeline.run_parts(FIXTURES / "sample_messy.md")
    assert "第 1 行：未找到题目标题" in str(excinfo.value)


def test_empty_file_raises_reader_error(tmp_path: Path) -> None:
    """空文件 → ``文件内容为空``（沿用 API 层的 50001 语义）。"""
    empty = tmp_path / "empty.md"
    empty.write_bytes(b"")
    with pytest.raises(ReaderError) as excinfo:
        markdown.load_banks(empty)
    assert str(excinfo.value) == "文件内容为空"

    blank = tmp_path / "blank.md"
    blank.write_text("   \n\n\t\n", encoding="utf-8")
    with pytest.raises(ReaderError) as excinfo:
        markdown.load_banks(blank)
    assert str(excinfo.value) == "文件内容为空"


# ===========================================================================
# 5. 重号 / 空块 / 行内容边界
# ===========================================================================
def test_duplicate_numbers_are_renumbered_without_anomaly() -> None:
    """重号按文件内出现顺序重排 1..n，不产生 duplicate_number 异常。"""
    text = (
        "## 1. [单选题]\n\n第一题题干内容?\n\n- **A.** 甲\n- **B.** 乙\n- **C.** 丙\n- **D.** 丁\n\n"
        "**答案**：A\n\n## 1. [单选题]\n\n第二题题干内容?\n\n- **A.** 甲\n- **B.** 乙\n"
        "- **C.** 丙\n- **D.** 丁\n\n**答案**：B\n"
    )
    part = pipeline._bank_part(markdown.parse_bank(text, "dup.md"))
    assert [item.parsed.no for item in part.items] == ["1", "2"]
    kinds = [anomaly.type for item in part.items for anomaly in item.anomalies]
    assert "duplicate_number" not in kinds
    assert kinds == []


def test_heading_only_block_becomes_parse_error() -> None:
    """只有标题、没有任何内容的题目块 → parse_error，但文件仍算解析成功。"""
    text = "## 1. [单选题]\n\n## 2. [单选题]\n\n题干内容足够长?\n\n- **A.** 甲\n- **B.** 乙\n- **C.** 丙\n- **D.** 丁\n\n**答案**：A\n"
    part = pipeline._bank_part(markdown.parse_bank(text, "half.md"))
    assert part.question_count == 2

    broken = part.items[0]
    assert broken.parsed.parse_error is True
    assert broken.parsed.error_detail == "第 1 行起的题目块无有效内容"
    # 声明了单选却没有选项/题干/答案 → 除 parse_error 外还带三条字段类异常
    assert [anomaly.type for anomaly in broken.anomalies] == [
        "missing_answer",
        "missing_options",
        "empty_stem",
        "parse_error",
    ]

    healthy = part.items[1]
    assert healthy.parsed.parse_error is False
    assert healthy.anomalies == []


def test_separator_and_heading_rules_do_not_split_question() -> None:
    """``---`` 与文件头 ``# `` 行不参与分题，题目数量只由 ``## `` 决定。"""
    text = (
        "# 题库说明\n\n- 导出时间：2025-01-01 10:00:00\n- 题目总数：1\n\n"
        "## 1. [单选题]\n\n题干内容足够长?\n\n# 这是段内误写的一级标题（应被丢弃）\n\n"
        "- **A.** 甲\n- **B.** 乙\n- **C.** 丙\n- **D.** 丁\n\n**答案**：A\n\n---\n"
    )
    bank = markdown.parse_bank(text, "x.md")
    assert len(bank.questions) == 1
    assert bank.questions[0].stem == "题干内容足够长?"


def test_option_continuation_merges_into_last_option() -> None:
    """选项软换行并入最后一个选项（与导出格式对齐）。"""
    text = (
        "## 1. [单选题]\n\n题干内容足够长?\n\n- **A.** 甲\n- **B.** 乙项第一段\n"
        "第二段续写\n- **C.** 丙\n- **D.** 丁\n\n**答案**：A\n"
    )
    parsed = markdown.parse_bank(text, "x.md").questions[0]
    assert parsed.options["B"] == "乙项第一段 第二段续写"
    assert parsed.options["A"] == "甲"


def test_analysis_stops_at_horizontal_rule() -> None:
    """``---`` 之后的内容不得并入上一题解析（v1.3 QA 一般-3）。"""
    text = (
        "## 1. [单选题]\n\n题干内容足够长?\n\n- **A.** 甲\n- **B.** 乙\n\n"
        "**答案**：A\n\n**解析**：正常解析第一行\n\n---\n\n这段是版式尾巴不应进解析\n"
    )
    parsed = markdown.parse_bank(text, "x.md").questions[0]
    assert parsed.analysis == "正常解析第一行"
    assert "版式尾巴" not in parsed.analysis
