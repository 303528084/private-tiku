"""Markdown 题库解析器：行级状态机 → :class:`jsonbank.RawBank`。

设计要点（架构 §2）：

* **一个 ``.md`` 恒产出 1 个题库分片**（``P2-6`` 一文件多题库不在本期范围），
  ``name`` 取 ``display_name``（上传原始文件名）或磁盘文件名 —— 不按文件内
  标题命名，避免标题相同导致题库分裂；
* **逐行状态机**：``HEADER → STEM → OPTION → ANSWER → ANALYSIS``，每行先过
  :func:`app.parser.normalize.normalize_text`（全角→半角、BOM、空白折叠），
  再按固定优先级匹配，报错信息带 1-based 行号；
* **必须吃下自家导出的 Markdown**（``api/export.py::_render_markdown`` 的输出），
  包括文件头 ``# 私有护理题库导出`` / ``- 导出时间：`` / ``- 题目总数：``、
  ``## 1. [单选题]``、``- **A.** xxx``、``**答案**：A``、``**解析**：…``、
  ``**异常标记**：…``、``**来源**：…``、``---``；
* **往返收敛**：``**来源**`` 与 ``**异常标记**`` 行导入时**丢弃**（来源统一取
  文件名、异常由 :func:`app.parser.validator.validate` 重算），``（题干为空）``
  与 ``（无答案）`` 两个导出占位符还原为空值，从而「导出 → 导入 → 再导出」
  逐字节相等；
* **单题缺陷不抛异常**：缺题干/缺选项/缺答案落 ``Parsed`` 字段，由 validator
  转成 ``empty_stem``/``missing_options``/``missing_answer``；只有「读不出文件」
  「全文无题目标题」「有标题但 0 题」才抛 :class:`ReaderError`。

题型与答案归一一律复用 :mod:`app.parser.jsonbank` 的 ``map_qtype`` /
``resolve_qtype`` / ``fold_judge_options`` 与 :mod:`app.parser.extractor` 的
``decide_qtype`` / ``normalize_answer``，保证与 JSON 导入同一口径。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import jsonbank
from .extractor import Parsed, normalize_answer
from .normalize import normalize_text
from .reader import ReaderError, decode_bytes

# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------
STATE_HEADER = "header"
STATE_STEM = "stem"
STATE_OPTION = "option"
STATE_ANSWER = "answer"
STATE_ANALYSIS = "analysis"
STATE_TAIL = "tail"
"""``---`` 之后、下一个 ``## `` 之前的尾部区域：整块丢弃。

导出文件里 ``---`` 是分题分隔线，其后的内容（若有）属于版式尾巴而非解析内容。
若在 ANALYSIS 阶段继续追加，会把「分隔线之后的行」误并进上一题的解析（v1.3 QA 一般-3）。
"""

# ---------------------------------------------------------------------------
# 正则（全部作用于 normalize_text() 之后的行）
# ---------------------------------------------------------------------------
HEADING_RE = re.compile(r"^##\s+(?P<rest>.+)$")
"""题目标题：``## 1. [单选题]``。"""

TYPE_TAG_RE = re.compile(r"[\[(](?P<tag>[^\])]{1,12})[\])]")
"""题型标记：``[单选题]`` / ``(单选题)`` / ``【单选题】``（normalize 后成为 ``[..]``）。"""

HEADING_NO_RE = re.compile(r"^(?:第\s*)?(?P<no>\d{1,4})\s*(?:题\s*)?(?:[.、:：)）]|\s|$)")
"""题号：``1.`` / ``1、`` / ``1`` / ``第1题.`` / ``第 1 题``。"""

MD_OPTION_RE = re.compile(
    r"^(?:[-*+]\s+)?\*{0,2}(?P<l>[A-H])\s*\*{0,2}[.、:：)）]\*{0,2}\s*(?P<t>.*)$"
)
"""选项行：``- **A.** x`` / ``- A. x`` / ``- **A、** x`` / ``A. x`` / ``A：x``。

注意导出格式是 ``- **A.** 文本``（加粗覆盖「字母+点」，收尾的 ``**`` 紧跟在点后面），
因此分隔符之后还要再吸收一次 ``\\*{0,2}``；该 ``\\*{0,2}`` 紧贴分隔符（中间不插空白），
以免把选项正文里的加粗（``- **A.** **重点**``）一起吃掉。
"""

MD_ANSWER_RE = re.compile(
    r"^\*{0,2}(?:参考答案|正确答案|标准答案|答案)\*{0,2}\s*[:：]\s*(?P<v>.*)$"
)
"""答案行：``**答案**：`` / ``**答案**:`` / ``答案：`` / ``正确答案：`` / ``参考答案：``。"""

MD_ANALYSIS_RE = re.compile(
    r"^\*{0,2}(?:答案解析|试题解析|题目解析|解析|分析|解答)\*{0,2}\s*[:：]\s*(?P<v>.*)$"
)
"""解析行：``**解析**：`` / ``解析：`` / ``**分析**：`` / ``**解答**：``。"""

IGNORE_RE = re.compile(r"^\*{0,2}(?:来源|异常标记)\*{0,2}\s*[:：]")
"""导入时丢弃的行：``**来源**：``（来源统一取文件名）、``**异常标记**：``（异常重算）。"""

RULE_RE = re.compile(r"^---+\s*$")
"""分隔线 ``---``（分题以 ``## `` 标题为准，故直接丢弃）。"""

H1_RE = re.compile(r"^#\s")
"""文件头 ``# 私有护理题库导出``（``## `` 已在状态机最前面命中，不会落到这里）。"""

ANSWER_EMPTY_PLACEHOLDER = "(无答案)"
"""导出时「答案为空」的占位符（normalize 后全角括号已转半角）。"""

STEM_EMPTY_PLACEHOLDER = "(题干为空)"
"""导出时「题干为空」的占位符。"""

_LETTER_ONLY_RE = re.compile(r"^[A-Za-z0-9\s,，、;；/|]+$")
_NON_LETTER_RE = re.compile(r"[^A-Za-z]")

_EMPHASIS_PAIRS: tuple[tuple[str, str], ...] = (("**", "**"), ("__", "__"), ("*", "*"), ("_", "_"))
"""选项正文整体被加粗/斜体包裹时剥掉的成对标记（``- **A.** **重点**`` → ``重点``）。"""


# ---------------------------------------------------------------------------
# 单题草稿
# ---------------------------------------------------------------------------
@dataclass
class _Draft:
    """一题在解析过程中的草稿（标题行 → EOF 之间累积）。"""

    heading_line: int
    declared_qtype: Optional[str] = None
    explicit_no: Optional[str] = None
    stem_parts: List[str] = field(default_factory=list)
    options: Dict[str, str] = field(default_factory=dict)
    answer_raw: str = ""
    analysis_parts: List[str] = field(default_factory=list)
    last_letter: Optional[str] = None
    saw_content: bool = False
    """是否出现过任何有效内容行（用于区分「空题目块」与「合法空题干」）。"""


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------
def load_banks(
    path: Path | str, display_name: Optional[str] = None
) -> List[jsonbank.RawBank]:
    """读取 Markdown 文件并解析为题库列表（**恒 1 个分片**）。

    Args:
        path: Markdown 文件路径。
        display_name: 展示用题库名（上传时的原始文件名）；缺省用磁盘文件名。

    Returns:
        单元素列表，元素为 :class:`jsonbank.RawBank`。

    Raises:
        ReaderError: 文件不可读、内容为空、全文无 ``## `` 标题或识别不到任何题目。
    """
    file_path = Path(path)
    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        raise ReaderError(f"读取 Markdown 文件失败：{exc}") from exc
    if not raw:
        raise ReaderError("文件内容为空")

    text, _encoding = decode_bytes(raw)
    if not text.strip():
        raise ReaderError("文件内容为空")

    name = display_name or file_path.name
    return [parse_bank(text, name)]


def parse_bank(text: str, name: str) -> jsonbank.RawBank:
    """把 Markdown 全文解析为一个题库（纯函数，便于单测）。

    Args:
        text: Markdown 全文（可含 CRLF / BOM / 全角标点）。
        name: 题库名称（通常为文件名）。

    Returns:
        :class:`jsonbank.RawBank`。

    Raises:
        ReaderError: 无 ``## `` 标题，或（防御分支）识别不到任何题目内容。
    """
    body = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = body.split("\n")

    drafts: List[_Draft] = []
    current: Optional[_Draft] = None
    state = STATE_HEADER
    first_nonempty: Optional[int] = None
    first_heading: Optional[int] = None

    for index, raw_line in enumerate(lines, start=1):
        line = normalize_text(raw_line)
        if line and first_nonempty is None:
            first_nonempty = index

        # 1) 题目标题：任何状态下的最高优先级
        heading = HEADING_RE.match(line)
        if heading:
            if current is not None:
                drafts.append(current)
            if first_heading is None:
                first_heading = index
            current = _new_draft(heading.group("rest"), index)
            state = STATE_STEM
            continue

        # 2) 空行：所有状态统一忽略
        if not line:
            continue

        # 3) 全局丢弃行：来源 / 异常标记 / 文件头 "# "
        if IGNORE_RE.match(line) or H1_RE.match(line):
            continue

        # 3.1) 分隔线：分题线本身无内容；进入 TAIL，其后的行丢弃到下一个标题
        if RULE_RE.match(line):
            state = STATE_TAIL
            continue

        # 4) 文件头 / 尾部区域：其余内容整块忽略
        if state in (STATE_HEADER, STATE_TAIL) or current is None:
            continue

        draft = current

        # 5) 解析行优先判定，避免「答案解析：…」被答案行截走
        matched_analysis = MD_ANALYSIS_RE.match(line)
        if matched_analysis:
            tail = (matched_analysis.group("v") or "").strip()
            if tail:
                draft.analysis_parts.append(tail)
            draft.saw_content = True
            draft.last_letter = None
            state = STATE_ANALYSIS
            continue

        # 6) 答案行：只取第一条（后续「答案：」行不覆盖已识别答案）
        matched_answer = MD_ANSWER_RE.match(line)
        if matched_answer:
            tail = (matched_answer.group("v") or "").strip()
            if tail and not draft.answer_raw:
                draft.answer_raw = tail
            draft.saw_content = True
            draft.last_letter = None
            state = STATE_ANSWER
            continue

        # 7) 选项行 / 选项续行（仅 STEM 与 OPTION 阶段参与）
        if state in (STATE_STEM, STATE_OPTION):
            matched_option = MD_OPTION_RE.match(line)
            if matched_option:
                letter = matched_option.group("l").upper()
                draft.options[letter] = _strip_emphasis(matched_option.group("t") or "")
                draft.last_letter = letter
                draft.saw_content = True
                state = STATE_OPTION
                continue
            if state == STATE_OPTION and draft.last_letter is not None:
                # 选项软换行：并入最后一个选项
                merged = f"{draft.options.get(draft.last_letter, '')} {line}".strip()
                draft.options[draft.last_letter] = merged
                draft.saw_content = True
                continue
            draft.stem_parts.append(line)
            draft.saw_content = True
            continue

        # 8) ANSWER 阶段：其它行丢弃（避免「答案：A 是否正确」类尾注污染答案）
        if state == STATE_ANSWER:
            continue

        # 9) ANALYSIS 阶段：普通文本追加（单空格拼接，保证二次往返收敛）
        draft.analysis_parts.append(line)
        draft.saw_content = True

    if current is not None:
        drafts.append(current)

    if not drafts:
        position = first_nonempty or 1
        raise ReaderError(
            f"第 {position} 行：未找到题目标题，需要 "
            f"`## 1. [单选题]` 形式"
        )

    questions = [_finalize(draft, offset) for offset, draft in enumerate(drafts, start=1)]
    if not questions:  # pragma: no cover - drafts 非空时必然产出题目
        raise ReaderError(f"第 {first_heading or 1} 行：未识别到任何题目内容")

    return jsonbank.RawBank(name=name or "未命名题库", questions=questions)


# ---------------------------------------------------------------------------
# 内部：标题解析与结算
# ---------------------------------------------------------------------------
def _new_draft(rest: str, heading_line: int) -> _Draft:
    """按标题行文本创建题目草稿（解析题型标记与题号）。"""
    declared, explicit_no = _split_heading(rest)
    return _Draft(
        heading_line=heading_line,
        declared_qtype=declared,
        explicit_no=explicit_no,
    )


def _split_heading(rest: str) -> tuple[Optional[str], Optional[str]]:
    """从标题行文本里取「声明题型」与「显式题号」。

    ``## 1. [单选题]`` → ``("single", "1")``；
    ``## 第2题 (多选题)`` → ``("multiple", "2")``；
    ``## 说明`` → ``(None, None)``（题型交给答案推断、题号按顺序补）。
    """
    text = (rest or "").strip()
    tag_match = TYPE_TAG_RE.search(text)
    declared: Optional[str] = None
    if tag_match:
        declared = jsonbank.map_qtype(tag_match.group("tag"))
        text = f"{text[: tag_match.start()]} {text[tag_match.end():]}".strip()

    no_match = HEADING_NO_RE.match(text)
    explicit_no = no_match.group("no") if no_match else None
    return declared, explicit_no


def _finalize(draft: _Draft, fallback_no: int) -> Parsed:
    """把草稿结算为 :class:`Parsed`（题型/答案归一与占位符还原）。"""
    no = draft.explicit_no or str(fallback_no)

    stem = " ".join(part for part in draft.stem_parts if part).strip()
    if stem == STEM_EMPTY_PLACEHOLDER:
        stem = ""

    raw_answer = (draft.answer_raw or "").strip()
    if raw_answer == ANSWER_EMPTY_PLACEHOLDER:
        raw_answer = ""

    analysis = " ".join(part for part in draft.analysis_parts if part).strip()
    options = dict(sorted(draft.options.items()))

    if not draft.saw_content:
        return Parsed(
            no=no,
            stem="",
            options={},
            answer="",
            analysis="",
            qtype=draft.declared_qtype or "unknown",
            parse_error=True,
            error_detail=f"第 {draft.heading_line} 行起的题目块无有效内容",
        )

    candidate = _normalize_answer_token(raw_answer)
    qtype = jsonbank.resolve_qtype(draft.declared_qtype, options, candidate)
    if qtype == "judge":
        options, candidate = jsonbank.fold_judge_options(options, candidate)

    return Parsed(
        no=no,
        stem=stem,
        options=options,
        answer=normalize_answer(candidate, qtype),
        analysis=analysis,
        qtype=qtype,
    )


def _normalize_answer_token(raw: str) -> str:
    """答案 token 归一：去字母分隔符并大写；判断词（对/错/T/F…）保持原样。

    形如 ``A、B`` / ``A, B`` / ``abd`` 一律压成 ``AB`` / ``ABD``；
    ``√``/``×``/``对``/``错``/``正确`` 等判断答案不参与字母清洗。
    """
    text = (raw or "").strip()
    if not text:
        return ""
    if _LETTER_ONLY_RE.match(text):
        letters = _NON_LETTER_RE.sub("", text).upper()
        if letters:
            return letters
    return text


def _strip_emphasis(text: str) -> str:
    """剥掉选项正文最外层的成对加粗/斜体标记。

    Args:
        text: 选项正文（已从行里切出）。

    Returns:
        去掉外侧 ``**`` / ``__`` / ``*`` / ``_`` 成对标记后的正文。
    """
    value = (text or "").strip()
    for left, right in _EMPHASIS_PAIRS:
        if len(value) > len(left) + len(right) and value.startswith(left) and value.endswith(right):
            return value[len(left) : -len(right)].strip()
    return value
