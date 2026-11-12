"""题目字段规则层：题型判定、答案规范化与 ``Parsed`` 数据类。

v1.3 起 DOCX/TXT/PDF 的「题目块抽取」已下线（``extract`` / ``_extract_inner`` /
``_split_trailing`` / ``_take_answer_token`` 一并删除），本模块只保留**跨解析器
共用**的规则与工具：

* :class:`Parsed`：所有解析器（JSON / Markdown / 手录）统一的题目中间表示；
* :func:`decide_qtype` / :func:`normalize_answer`：题型判定与答案规范化（架构 §6）；
* :func:`make_parsed`：按字段构造 ``Parsed``（手录与题目编辑复用，保证同一口径）；
* ``OPTION_RE`` / ``ANSWER_RE`` / ``ANALYSIS_RE`` / ``JUDGE_*``：行级正则与判断
  词表（Markdown 解析器与测试共用，避免多处重复定义造成规则漂移）；
* :func:`split_inline_options`：一行内联选项切分（JSON ``options`` 文本字段用到）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# 正则（作用于 normalize_text() 之后的行）
# ---------------------------------------------------------------------------
OPTION_RE = re.compile(r"^([A-H])\s*[.、。．)）:：]\s*(.*)$")

_ANALYSIS_PREFIX = r"[\[\(【]?\s*(?:答案解析|试题解析|题目解析|解析|分析|解答)\s*[\]\)】]?\s*[:：]?\s*"
ANSWER_PREFIX = r"[\[\(【]?\s*(?:参考答案|正确答案|标准答案|答案)\s*[\]\)】]?\s*[:：]?\s*"

ANALYSIS_RE = re.compile(r"^" + _ANALYSIS_PREFIX + r"(.*)$")
ANSWER_RE = re.compile(r"^" + ANSWER_PREFIX + r"(.*)$")

INLINE_OPTION_RE = re.compile(r"(?:^|[\s，,;；、])([A-H])[.、。．)）]\s*")

LETTERS_RE = re.compile(r"[A-H]")

JUDGE_TRUE: frozenset[str] = frozenset({"对", "正确", "√", "✓", "是", "T", "TRUE", "Y"})
JUDGE_FALSE: frozenset[str] = frozenset({"错", "错误", "×", "✗", "X", "F", "FALSE", "N", "否"})
JUDGE_TRUE_MARK = "√"
JUDGE_FALSE_MARK = "×"


@dataclass
class Parsed:
    """单题解析结果（JSON / Markdown / 手录共用的中间表示）。"""

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
    """判断一个短 token 是否像答案（供需要内联识别的调用方复用）。"""
    text = (token or "").strip()
    if not text:
        return False
    if re.fullmatch(r"[A-Ha-h]{1,8}", text):
        return True
    return text.upper() in JUDGE_TRUE or text.upper() in JUDGE_FALSE


# ---------------------------------------------------------------------------
# 内联选项切分（JSON options 文本字段复用）
# ---------------------------------------------------------------------------
def split_inline_options(text: str) -> Tuple[str, Dict[str, str]]:
    """把一行内联的选项拆出来（要求字母连续升序，且至少 2 个）。

    返回 ``(选项之前的文本, 选项字典)``。字母可以不从 A 开始（例如续行 ``C. x D. y``）。
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


# ---------------------------------------------------------------------------
# 构造 Parsed
# ---------------------------------------------------------------------------
def make_parsed(
    no: str,
    stem: str,
    options: Dict[str, str],
    answer: str,
    analysis: str,
    qtype: Optional[str] = None,
) -> Parsed:
    """按给定字段构造 Parsed（供手录新增与题目编辑后重算异常复用）。"""
    resolved_qtype = qtype or decide_qtype(options, answer)
    return Parsed(
        no=str(no),
        stem=(stem or "").strip(),
        options=dict(sorted({str(k).upper(): str(v) for k, v in (options or {}).items()}.items())),
        answer=normalize_answer(answer, resolved_qtype),
        analysis=(analysis or "").strip(),
        qtype=resolved_qtype,
    )
