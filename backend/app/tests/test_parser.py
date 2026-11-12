"""解析引擎单元测试：三格式读取、规范化、切分、抽取、6 类异常规则。

运行：``cd backend && python -m pytest app/tests/test_parser.py -q``

注意：normalize 阶段会把全角标点（含 ``？`` ``，`` ``：``）统一为半角，
因此断言中的题干使用半角标点。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.parser.extractor import Parsed, decide_qtype, extract, normalize_answer
from app.parser.normalize import normalize, normalize_text
from app.parser.pipeline import run, run_lines
from app.parser.reader import Line, ReaderError, read
from app.parser.splitter import Block, match_no, split, strip_no_prefix
from app.parser.validator import validate

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
BASIC_TXT = FIXTURE_DIR / "sample_basic.txt"
MESSY_TXT = FIXTURE_DIR / "sample_messy.txt"
GBK_TXT = FIXTURE_DIR / "sample_gbk.txt"
SAMPLE_DOCX = FIXTURE_DIR / "sample_questions.docx"
SAMPLE_PDF = FIXTURE_DIR / "sample_questions.pdf"


def _lines(*texts: str) -> list[Line]:
    """把若干行文本包装成 Line 列表。"""
    return [Line(seq=index, text=text, page=None) for index, text in enumerate(texts)]


def _blocks(*texts: str) -> list:
    """规范化 + 切分，返回题目块列表。"""
    return split(normalize(_lines(*texts)))


# ---------------------------------------------------------------------------
# 1. normalize
# ---------------------------------------------------------------------------
def test_normalize_fullwidth_to_halfwidth() -> None:
    """全角字母/数字/标点统一为半角。"""
    assert normalize_text("Ａ．房颤") == "A.房颤"
    assert normalize_text("答案：ＡＢＤ") == "答案:ABD"
    assert normalize_text("（　）") == "( )"
    assert normalize_text("心律失常？") == "心律失常?"


def test_normalize_drops_blank_lines_and_trims() -> None:
    """空行被丢弃，首尾空白被清理，顺序保持不变。"""
    lines = _lines("  第一行  ", "", "   ", "\u3000", "第二行")
    result = normalize(lines)
    assert [line.text for line in result] == ["第一行", "第二行"]
    assert [line.seq for line in result] == [0, 4]


def test_normalize_collapses_inner_spaces() -> None:
    """行内连续空格压缩为一个。"""
    assert normalize_text("A.   房颤") == "A. 房颤"


def test_normalize_keeps_chinese_period() -> None:
    """中文句号保留原样（不转为半角点，避免被误认为题号）。"""
    assert normalize_text("正常成人腋温 36.0℃。") == "正常成人腋温 36.0℃。"


# ---------------------------------------------------------------------------
# 2. splitter
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("1. 题干", "1"),
        ("12、题干", "12"),
        ("003）题干", "3"),
        # v1.1 D3-1：括号题号与「第N题」题号
        ("（79）为患者进行鼻饲前应首先评估的是?", "79"),
        ("(80) 心肺复苏时开放气道最常用的手法是?", "80"),
        ("第80题. 心肺复苏时开放气道最常用的手法是?", "80"),
        ("第 81 题 静脉输液时选择血管的原则是?", "81"),
        ("第82题: 正常成人腋温范围是?", "82"),
        # 不应被误判为题号
        ("（ ）填空题的括号", None),
        ("（A）选项式括号", None),
        ("（12345）超长数字", None),
        ("A. 选项", None),
        ("120次/分", None),
        ("第 12 题", "12"),
    ],
)
def test_match_no(line: str, expected: str | None) -> None:
    """题号正则识别（后缀式 / 括号式 / 第N题式）。"""
    assert match_no(normalize_text(line)) == expected


def test_split_creates_blocks_and_drops_preamble() -> None:
    """按题号切块，首个题号之前的标题被丢弃。"""
    blocks = _blocks(
        "护理学基础试题",
        "1. 第一题",
        "A. 甲",
        "2. 第二题",
        "答案：对",
    )
    assert [block.no_text for block in blocks] == ["1", "2"]
    assert len(blocks[0].lines) == 2
    assert len(blocks[1].lines) == 2


def test_split_without_number_returns_pseudo_block() -> None:
    """完全无法识别题号时返回伪块，交给 validator 标记 parse_error。"""
    blocks = _blocks("没有任何题号的一段文本", "还是文本")
    assert len(blocks) == 1
    assert blocks[0].no_text == ""
    assert len(blocks[0].lines) == 2


def test_strip_no_prefix() -> None:
    """题号前缀剥离。"""
    assert strip_no_prefix("3. 正常成人腋温") == "正常成人腋温"
    assert strip_no_prefix("3、正常成人腋温") == "正常成人腋温"
    # v1.1 D3-1：括号 / 第N题前缀必须整段剥离，不能把 （79） 留在题干里
    assert strip_no_prefix("(79) 为患者进行鼻饲前应首先评估的是?") == "为患者进行鼻饲前应首先评估的是?"
    assert strip_no_prefix("第80题. 心肺复苏时开放气道最常用的手法是?") == "心肺复苏时开放气道最常用的手法是?"


def test_split_parenthesized_numbering() -> None:
    """通篇用 （1）（2）（3） 编号时切成 3 块（v1.1 D3-1 回归）。"""
    blocks = _blocks(
        "（1）脉搏短绌常见于哪种心律失常?",
        "A. 房颤",
        "B. 室颤",
        "C. 房扑",
        "D. 期前收缩",
        "答案：A",
        "（2）无菌盘有效期为?",
        "A. 4小时",
        "B. 8小时",
        "C. 12小时",
        "D. 24小时",
        "答案：A",
        "（3）氧气筒内氧气可用尽后再更换。",
        "答案：错",
    )
    assert [block.no_text for block in blocks] == ["1", "2", "3"]
    assert len(blocks) == 3


def test_split_chapter_numbering() -> None:
    """「第N题」编号也能切块（v1.1 D3-1 回归）。"""
    blocks = _blocks(
        "第1题. 脉搏短绌常见于哪种心律失常?",
        "A. 房颤",
        "B. 室颤",
        "C. 房扑",
        "D. 期前收缩",
        "答案：A",
        "第2题 无菌盘有效期为?",
        "答案：24小时",
    )
    assert [block.no_text for block in blocks] == ["1", "2"]


# ---------------------------------------------------------------------------
# 3. extractor —— 题型判定与答案规范化
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


# ---------------------------------------------------------------------------
# 4. extractor —— 块级抽取
# ---------------------------------------------------------------------------
def test_extract_standard_block() -> None:
    """标准排版：题干 + 独立选项行 + 答案行 + 解析行。"""
    blocks = _blocks(
        "1. 脉搏短绌常见于下列哪种心律失常？",
        "A. 房颤",
        "B. 室颤",
        "C. 房扑",
        "D. 期前收缩",
        "答案：A",
        "解析：常见于心房颤动。",
    )
    parsed = extract(blocks[0])
    assert parsed.no == "1"
    assert parsed.stem == "脉搏短绌常见于下列哪种心律失常?"
    assert parsed.options == {"A": "房颤", "B": "室颤", "C": "房扑", "D": "期前收缩"}
    assert parsed.answer == "A"
    assert parsed.qtype == "single"
    assert "心房颤动" in parsed.analysis
    assert parsed.parse_error is False


def test_extract_inline_options_on_stem_line() -> None:
    """题干行内联选项被拆分。"""
    blocks = _blocks("2. 心肺复苏有效指征包括？ A.大动脉搏动 B.面色转红 C.瞳孔变小 D.呼吸恢复")
    parsed = extract(blocks[0])
    assert parsed.stem == "心肺复苏有效指征包括?"
    assert parsed.options == {
        "A": "大动脉搏动",
        "B": "面色转红",
        "C": "瞳孔变小",
        "D": "呼吸恢复",
    }


def test_extract_single_line_question() -> None:
    """单行题：题干 + 内联选项 + 内联答案 + 内联解析。"""
    blocks = _blocks(
        "3. 无菌技术操作原则是？ A.操作前洗手 B.无菌物品与非无菌物品分放 "
        "C.无菌包打开后 24h 内有效 D.以上都是 答案：D 解析：以上均为基本原则。"
    )
    parsed = extract(blocks[0])
    assert parsed.stem == "无菌技术操作原则是?"
    assert parsed.options["D"] == "以上都是"
    assert parsed.answer == "D"
    assert parsed.qtype == "single"
    assert "基本原则" in parsed.analysis


def test_extract_judge_question() -> None:
    """判断题：无选项，答案归一为 √/×。"""
    blocks = _blocks("3. 正常成人腋温范围是 36.0~37.0℃。", "答案：对")
    parsed = extract(blocks[0])
    assert parsed.qtype == "judge"
    assert parsed.answer == "√"
    assert parsed.options == {}


def test_extract_multiple_with_bracket_option_letters() -> None:
    """括号式选项字母 `A)` 也能识别。"""
    blocks = _blocks(
        "4. 下列属于医院感染的有？",
        "A) 交叉感染",
        "B) 环境感染",
        "C) 自身感染",
        "D) 以上均是",
        "答案：ABC",
    )
    parsed = extract(blocks[0])
    assert parsed.qtype == "multiple"
    assert parsed.answer == "ABC"
    assert len(parsed.options) == 4


def test_extract_continuation_line_merges_into_option() -> None:
    """选项的续行会合并到上一个选项文本。"""
    blocks = _blocks(
        "5. 压疮好发部位是？",
        "A. 骶尾部",
        "B. 足跟部及",
        "外踝",
        "C. 髂前上棘",
        "D. 肩胛部",
        "答案：A",
    )
    parsed = extract(blocks[0])
    assert parsed.options["B"] == "足跟部及 外踝"
    assert parsed.options["A"] == "骶尾部"


def test_extract_flags_missing_number_as_parse_error() -> None:
    """无题号伪块被标记 parse_error。"""
    blocks = _blocks("没有任何题号的文本内容")
    parsed = extract(blocks[0])
    assert parsed.parse_error is True
    assert "题号" in parsed.error_detail


def test_extract_first_answer_wins() -> None:
    """同一块内出现多条答案行时取**第一条**（v1.1 D3-2 回归）。

    场景：后一题的题号未被识别时，它的「答案：A」会混进当前块；
    旧实现取最后一条，导致真实答案（ABCDE）被覆盖成 A。
    """
    block = Block(
        no_text="78",
        lines=_lines(
            "78. 护理程序包括的步骤有?",
            "A. 评估",
            "B. 诊断",
            "C. 计划",
            "D. 实施",
            "E. 评价",
            "答案：ABCDE",
            "为患者进行鼻饲前应首先评估的是?",
            "A. 胃管是否在胃内",
            "答案：A",
        ),
    )
    parsed = extract(block)
    assert parsed.answer == "ABCDE"
    assert parsed.qtype == "multiple"
    assert parsed.stem.startswith("护理程序包括的步骤有?")


# ---------------------------------------------------------------------------
# 5. validator —— 6 类异常
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


# ---------------------------------------------------------------------------
# 6. reader —— 三格式
# ---------------------------------------------------------------------------
def test_reader_txt_utf8() -> None:
    """UTF-8 TXT 读取。"""
    lines = read(BASIC_TXT)
    assert len(lines) > 10
    assert lines[0].text.startswith("护理学基础试题")


def test_reader_txt_gbk() -> None:
    """GBK TXT 经 chardet 探测后可正确解码。"""
    lines = read(GBK_TXT)
    joined = "\n".join(line.text for line in lines)
    assert "慢性阻塞性肺疾病" in joined


def test_reader_docx() -> None:
    """DOCX 段落可读取。"""
    lines = read(SAMPLE_DOCX)
    joined = "\n".join(line.text for line in lines)
    assert "外科护理学试题" in joined


def test_reader_pdf() -> None:
    """PDF 逐页文本可读取，且带页码。"""
    lines = read(SAMPLE_PDF)
    joined = "\n".join(line.text for line in lines)
    assert "妇产科护理学试题" in joined
    assert all(line.page == 1 for line in lines)


def test_reader_rejects_unknown_extension(tmp_path: Path) -> None:
    """不支持的扩展名抛 ReaderError。"""
    target = tmp_path / "a.csv"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(ReaderError):
        read(target)


def test_reader_rejects_empty_file(tmp_path: Path) -> None:
    """空文件抛 ReaderError。"""
    target = tmp_path / "empty.txt"
    target.write_bytes(b"")
    with pytest.raises(ReaderError):
        read(target)


# ---------------------------------------------------------------------------
# 7. pipeline —— 端到端
# ---------------------------------------------------------------------------
def test_pipeline_basic_txt() -> None:
    """TXT 端到端：7 题 + 4 条异常（缺答案 1、选项不足 1、题号重复 2）。"""
    result = run(BASIC_TXT)
    assert result.question_count == 7
    numbers = [item.parsed.no for item in result.items]
    assert numbers == ["1", "2", "3", "4", "5", "6", "6"]

    kinds: list[str] = [anomaly.type for item in result.items for anomaly in item.anomalies]
    assert kinds.count("missing_answer") == 1
    assert kinds.count("too_few_options") == 1
    assert kinds.count("duplicate_number") == 2
    assert result.anomaly_count == 4

    first = result.items[0].parsed
    assert first.stem == "脉搏短绌常见于下列哪种心律失常?"
    assert first.qtype == "single"
    assert first.answer == "A"
    assert first.analysis.startswith("脉搏短绌是")

    second = result.items[1].parsed
    assert second.qtype == "multiple"
    assert second.answer == "ABD"

    third = result.items[2].parsed
    assert third.qtype == "judge"
    assert third.answer == "√"


def test_pipeline_messy_txt_all_parse_error() -> None:
    """无题号文档整体标记 parse_error。"""
    result = run(MESSY_TXT)
    assert result.question_count == 1
    assert result.items[0].parsed.parse_error is True
    assert any(anomaly.type == "parse_error" for anomaly in result.items[0].anomalies)


def test_pipeline_docx() -> None:
    """DOCX 端到端 3 题。"""
    result = run(SAMPLE_DOCX)
    assert result.question_count == 3
    assert [item.parsed.qtype for item in result.items] == ["single", "single", "single"]
    assert result.items[0].parsed.answer == "B"


def test_pipeline_pdf() -> None:
    """PDF 端到端 2 题。"""
    result = run(SAMPLE_PDF)
    assert result.question_count == 2
    assert result.items[0].parsed.answer == "B"
    assert result.items[1].parsed.qtype == "single"


def test_pipeline_gbk() -> None:
    """GBK TXT 端到端 2 题。"""
    result = run(GBK_TXT)
    assert result.question_count == 2
    assert result.items[0].parsed.options["A"] == "1~2L/min"
    assert result.items[1].parsed.answer == "B"


def test_enumeration_inside_question_not_split() -> None:
    """题内 ``（1）（2）`` 列举不得切成新题（v1.1.1 D5 回归）。

    护理试题常用「（1）定时翻身 （2）保持皮肤清洁干燥」在题内列举要点，
    与括号题号同形。当前题还没出现答案/解析行时，括号编号一律并入当前块。
    """
    result = run_lines(
        _lines(
            "5. 预防压疮的护理措施包括?",
            "（1）定时翻身",
            "（2）保持皮肤清洁干燥",
            "A. 定时翻身",
            "B. 保持皮肤清洁干燥",
            "C. 使用减压床垫",
            "D. 局部用力按摩受压皮肤",
            "答案：ABC",
        )
    )
    assert result.block_count == 1
    parsed = result.items[0].parsed
    assert parsed.no == "5"
    assert len(parsed.options) == 4
    assert parsed.answer == "ABC"
    assert parsed.qtype == "multiple"
    assert "missing_answer" not in {anomaly.type for anomaly in result.items[0].anomalies}
    # 列举内容保留在题干里，不丢信息
    assert "(1)定时翻身" in parsed.stem
    assert "(2)保持皮肤清洁干燥" in parsed.stem


def test_bracket_question_with_options_but_no_answer_still_splits() -> None:
    """前一道括号题「有选项、答案空着」时，后一道括号题必须仍切成新题（v1.1.1 D5 扩展）。

    ``missing_answer`` 是 P0-4 主动标记的六类异常之一，「有选项、答案空着」在
    护理题库里非常常见。若这种题不算「已收尾」，后一道题的答案会错位到前题，
    属于内容级错误，因此收尾判定必须包含「已收集 ≥1 个选项行」。
    """
    result = run_lines(
        _lines(
            "（1）无菌盘铺好后有效期为?",
            "A. 4小时",
            "B. 8小时",
            "C. 12小时",
            "D. 24小时",
            "（2）铺备用床的主要目的是?",
            "A. 保持病室整洁",
            "B. 便于观察病情",
            "C. 预防压疮",
            "D. 便于实施治疗",
            "答案：B",
        )
    )
    assert result.block_count == 2
    first, second = (item.parsed for item in result.items)
    assert (first.no, second.no) == ("1", "2")

    # 第 1 题：4 个选项齐全但没有答案 → 必须被标 missing_answer（而不是被并掉）
    assert len(first.options) == 4
    assert first.answer == ""
    first_anomalies = {anomaly.type for anomaly in result.items[0].anomalies}
    assert "missing_answer" in first_anomalies

    # 第 2 题：答案不得串到第 1 题
    assert len(second.options) == 4
    assert (second.answer, second.qtype) == ("B", "single")
    assert second.no == "2"


def test_enumeration_then_real_bracket_question_splits() -> None:
    """反向不变式：题内列举（题干阶段）不切，但随后的真实括号题必须切。

    关键点：看到 ``（1）`` 时块内只有题干、还没有选项行，所以不算收尾 → 并入；
    等选项与答案齐了再遇到 ``（6）`` → 已收尾 → 开新块。
    """
    result = run_lines(
        _lines(
            "5. 预防压疮的护理措施包括?",
            "（1）定时翻身",
            "（2）保持皮肤清洁干燥",
            "A. 定时翻身",
            "B. 保持皮肤清洁干燥",
            "C. 使用减压床垫",
            "D. 局部用力按摩受压皮肤",
            "答案：ABC",
            "（6）压疮最好发的部位是?",
            "A. 骶尾部",
            "B. 足跟",
            "C. 肩胛部",
            "D. 枕部",
            "答案：A",
        )
    )
    assert result.block_count == 2
    first, second = (item.parsed for item in result.items)
    assert first.no == "5"
    assert len(first.options) == 4
    assert (first.answer, first.qtype) == ("ABC", "multiple")
    assert "missing_answer" not in {anomaly.type for anomaly in result.items[0].anomalies}
    assert "(1)定时翻身" in first.stem
    assert "(2)保持皮肤清洁干燥" in first.stem

    assert second.no == "6"
    assert len(second.options) == 4
    assert (second.answer, second.qtype) == ("A", "single")


def test_pipeline_counts_match(tmp_path: Path) -> None:
    """line_count / block_count 统计正确（空行已在 normalize 阶段丢弃）。"""
    target = tmp_path / "t.txt"
    target.write_text("1. 题目内容一\nA. 甲\n\n2. 题目内容二\n", encoding="utf-8")
    result = run_lines(read(target))
    assert result.block_count == 2
    assert result.line_count == 3
