"""字段抽取层：把题目块解析为题干/选项/答案/解析/题型。

支持的常见排版：
1. 选项独占一行：``A. 房颤`` / ``A、房颤`` / ``A）房颤``
2. 选项内联在题干行：``……？ A.房颤 B.室颤 C.房扑 D.早搏``
3. 答案行：``答案：A`` / ``参考答案：ABD`` / ``答案: 对``
4. 解析行：``解析：……`` / ``答案解析: ……``
5. 单行题：题干 + 内联选项 + 内联答案/解析

答案规范化与题型判定严格按架构 §6 执行。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .reader import Line
from .splitter import Block, strip_no_prefix

# ---------------------------------------------------------------------------
# 正则
# ---------------------------------------------------------------------------
OPTION_RE = re.compile(r"^([A-H])\s*[.、。．)）:：]\s*(.*)$")

_ANALYSIS_PREFIX = r"[\[\(【]?\s*(?:答案解析|试题解析|题目解析|解析|分析)\s*[\]\)】]?\s*[:：]?\s*"
ANSWER_PREFIX = r"[\[\(【]?\s*(?:参考答案|正确答案|标准答案|答案)\s*[\]\)】]?\s*[:：]?\s*"

ANALYSIS_RE = re.compile(r"^" + _ANALYSIS_PREFIX + r"(.*)$")
ANSWER_RE = re.compile(r"^" + ANSWER_PREFIX + r"(.*)$")

INLINE_OPTION_RE = re.compile(r"(?:^|[\s，,;；、])([A-H])[.、。．)）]\s*")
INLINE_ANSWER_RE = re.compile(r"(?:参考答案|正确答案|标准答案|答案)\s*[:：]\s*")
INLINE_ANALYSIS_RE = re.compile(r"(?:答案解析|试题解析|解析|分析)\s*[:：]\s*")

LETTERS_RE = re.compile(r"[A-H]")

JUDGE_TRUE: frozenset[str] = frozenset({"对", "正确", "√", "✓", "是", "T", "TRUE", "Y"})
JUDGE_FALSE: frozenset[str] = frozenset({"错", "错误", "×", "✗", "X", "F", "FALSE", "N", "否"})
JUDGE_TRUE_MARK = "√"
JUDGE_FALSE_MARK = "×"


@dataclass
class Parsed:
    """单题解析结果。"""

    no: str
    stem: str
    options: Dict[str, str] = field(default_factory=dict)
    answer: str = ""
    analysis: str = ""
    qtype: str = "unknown"
    parse_error: bool = False
    error_detail: str = ""


# ---------------------------------------------------------------------------
# 题型判定与答案规范化（架构 §6）
# ---------------------------------------------------------------------------
def decide_qtype(options: Dict[str, str], answer: str) -> str:
    """按优先级判定题型：judge → multiple → single → unknown。"""
    token = (answer or "").strip().upper()
    if not options and token and (token in JUDGE_TRUE or token in JUDGE_FALSE):
        return "judge"
    letters = LETTERS_RE.findall(token)
    if len(letters) >= 2:
        return "multiple"
    if len(letters) == 1:
        return "single"
    return "unknown"


def normalize_answer(answer: str, qtype: str) -> str:
    """答案规范化：多选字母升序大写；判断统一 √/×。"""
    raw = (answer or "").strip()
    if not raw:
        return ""
    token = raw.upper()
    if qtype == "judge":
        if token in JUDGE_TRUE:
            return JUDGE_TRUE_MARK
        if token in JUDGE_FALSE:
            return JUDGE_FALSE_MARK
        return raw
    if qtype == "multiple":
        letters = sorted(set(LETTERS_RE.findall(token)))
        return "".join(letters) if letters else raw
    if qtype == "single":
        letters = LETTERS_RE.findall(token)
        return letters[0] if letters else raw
    return raw


def looks_like_answer(token: str) -> bool:
    """判断一个短 token 是否像答案（用于内联答案识别，避免误切题干）。"""
    text = (token or "").strip()
    if not text:
        return False
    if re.fullmatch(r"[A-Ha-h]{1,8}", text):
        return True
    return text.upper() in JUDGE_TRUE or text.upper() in JUDGE_FALSE


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _split_inline_options(text: str) -> Tuple[str, Dict[str, str]]:
    """把一行内联的选项拆出来（要求字母连续升序，且至少 2 个）。

    返回 (选项之前的文本, 选项字典)。字母可以不从 A 开始（例如续行 ``C. x D. y``）。
    """
    matches = list(INLINE_OPTION_RE.finditer(text))
    if len(matches) < 2:
        return text, {}
    letters = [matched.group(1) for matched in matches]
    offsets = [ord(letter) for letter in letters]
    for index in range(1, len(offsets)):
        if offsets[index] != offsets[index - 1] + 1:
            return text, {}
    stem = text[: matches[0].start()].strip()
    options: Dict[str, str] = {}
    for index, matched in enumerate(matches):
        start = matched.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        options[matched.group(1)] = text[start:end].strip(" \t，,;；、")
    return stem, options


def _take_answer_token(text: str) -> Tuple[str, str]:
    """从答案区文本里取答案 token 与内联解析，返回 (token, analysis)。"""
    content = (text or "").strip()
    analysis = ""
    inline = INLINE_ANALYSIS_RE.search(content)
    if inline:
        analysis = content[inline.end():].strip()
        content = content[: inline.start()].strip()
    parts = content.split()
    token = parts[0] if parts else ""
    token = token.strip("。；;,.、:：")
    return token, analysis


def _split_trailing(text: str) -> Tuple[str, str, str]:
    """剥离行尾的内联「答案/解析」，返回 (剩余文本, 答案 token, 解析文本)。"""
    remainder = text or ""
    answer_token = ""
    analysis_text = ""

    matched_analysis = INLINE_ANALYSIS_RE.search(remainder)
    if matched_analysis:
        analysis_text = remainder[matched_analysis.end():].strip()
        remainder = remainder[: matched_analysis.start()].strip()

    matched_answer = INLINE_ANSWER_RE.search(remainder)
    if matched_answer:
        head = remainder[: matched_answer.start()].strip(" \t，,;；、")
        token, extra_analysis = _take_answer_token(remainder[matched_answer.end():])
        if looks_like_answer(token):
            remainder = head
            answer_token = token
            if extra_analysis:
                analysis_text = (extra_analysis + " " + analysis_text).strip()
    return remainder, answer_token, analysis_text


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def extract(block: Block) -> Parsed:
    """把一个题目块抽取为 Parsed。"""
    parsed = Parsed(no=block.no_text, stem="", options={}, answer="", analysis="", qtype="unknown")
    if not block.lines:
        parsed.parse_error = True
        parsed.error_detail = "题目块为空，无法抽取"
        return parsed

    try:
        return _extract_inner(block, parsed)
    except Exception as exc:  # 任何抽取异常都收敛为 parse_error
        parsed.parse_error = True
        parsed.error_detail = f"抽取异常：{exc}"
        return parsed


def _extract_inner(block: Block, parsed: Parsed) -> Parsed:
    """抽取主流程（异常由 extract 统一捕获）。"""
    texts: List[str] = []
    for index, line in enumerate(block.lines):
        texts.append(strip_no_prefix(line.text) if index == 0 else line.text)

    options: Dict[str, str] = {}
    stem_parts: List[str] = []
    analysis_parts: List[str] = []
    answer_raw = ""
    section = "stem"
    last_letter: Optional[str] = None

    for index, raw in enumerate(texts):
        text = raw.strip()
        if not text:
            continue

        # 解析行优先判定，避免「答案解析：…」被答案行截走
        matched_analysis = ANALYSIS_RE.match(text)
        if matched_analysis:
            tail = (matched_analysis.group(1) or "").strip()
            if tail:
                analysis_parts.append(tail)
            section = "analysis"
            last_letter = None
            continue

        matched_answer = ANSWER_RE.match(text)
        if matched_answer:
            tail = (matched_answer.group(1) or "").strip()
            token, inline_analysis = _take_answer_token(tail)
            # 只取「第一条」答案行：同一块内若因题号未被识别而混入后续题目的
            # 「答案：」行，不能被它覆盖真实答案（v1.1 修复 D3-2）。
            if token and not answer_raw:
                answer_raw = token
                if inline_analysis:
                    analysis_parts.append(inline_analysis)
            section = "answer"
            last_letter = None
            continue

        if section in ("stem", "option"):
            matched_option = OPTION_RE.match(text)
            if matched_option:
                _, inline_options = _split_inline_options(text)
                if inline_options:
                    options.update(inline_options)
                    last_letter = sorted(options.keys())[-1]
                    section = "option"
                    continue
                letter = matched_option.group(1).upper()
                options[letter] = (matched_option.group(2) or "").strip()
                last_letter = letter
                section = "option"
                continue
            if section == "option" and last_letter is not None:
                merged = f"{options[last_letter]} {text}".strip()
                options[last_letter] = merged
                continue

        if section in ("analysis", "answer"):
            analysis_parts.append(text)
            continue

        stem_parts.append(text)

    # 首行内联选项（单行题/题干行尾选项）
    if not options and stem_parts:
        new_stem, inline_options = _split_inline_options(stem_parts[0])
        if inline_options:
            stem_parts[0] = new_stem
            options = inline_options
        else:
            remainder, token, inline_analysis = _split_trailing(new_stem)
            if token:
                stem_parts[0] = remainder
                answer_raw = answer_raw or token
                if inline_analysis:
                    analysis_parts.append(inline_analysis)

    # 行尾内联答案可能落在最后一个选项上
    if options:
        last_key = sorted(options.keys())[-1]
        remainder, token, inline_analysis = _split_trailing(options[last_key])
        if token:
            options[last_key] = remainder
            answer_raw = answer_raw or token
        if inline_analysis:
            analysis_parts.append(inline_analysis)

    stem = " ".join(part for part in stem_parts if part).strip()
    analysis = " ".join(part for part in analysis_parts if part).strip()

    if not answer_raw and stem:
        remainder, token, inline_analysis = _split_trailing(stem)
        if token:
            stem = remainder
            answer_raw = token
        if inline_analysis:
            analysis = (inline_analysis + " " + analysis).strip()

    qtype = decide_qtype(options, answer_raw)
    parsed.stem = stem
    parsed.options = dict(sorted(options.items()))
    parsed.answer = normalize_answer(answer_raw, qtype)
    parsed.analysis = analysis
    parsed.qtype = qtype

    if not block.no_text:
        parsed.parse_error = True
        parsed.error_detail = "未识别到题号，无法切分题目"

    return parsed


def make_parsed(
    no: str,
    stem: str,
    options: Dict[str, str],
    answer: str,
    analysis: str,
    qtype: Optional[str] = None,
) -> Parsed:
    """按给定字段构造 Parsed（供题目编辑后重算异常复用）。"""
    resolved_qtype = qtype or decide_qtype(options, answer)
    return Parsed(
        no=str(no),
        stem=(stem or "").strip(),
        options=dict(sorted({str(k).upper(): str(v) for k, v in (options or {}).items()}.items())),
        answer=normalize_answer(answer, resolved_qtype),
        analysis=(analysis or "").strip(),
        qtype=resolved_qtype,
    )


def to_lines(text: str) -> List[Line]:  # pragma: no cover - 调试辅助
    """调试辅助：把一段文本转成 Line 列表。"""
    return [Line(seq=i, text=line, page=None) for i, line in enumerate(text.split("\n"))]
