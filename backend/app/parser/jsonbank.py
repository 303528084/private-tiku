"""结构化 JSON 题库读取层：字段直接映射为 ``Parsed``，跳过文本切分。

为什么单独一层：为「排版不规整的 Word/PDF 文本」设计的那套切分逻辑已在 v1.3
下线，但 JSON 与 Markdown 两种格式仍需要**同一套**题型/答案归一规则。本模块
只做**字段映射与归一**，6 类异常判定仍由 :mod:`app.parser.validator` 统一负责
（同一套规则、同一个入口，避免两套标准）。

支持的三种形态（同一批导出文件里可能混用）：

1. 单题库包（最常见）
   ``{"title": "…", "author": "…", "source_file": "…", "questions": [ {...} ]}``
2. 扁平数组
   ``[ {"title": "…", "type": "single_choice", "options": "A: …\\rB: …", "answer": "A"} ]``
3. 多题库合并包
   ``{"banks": [ {"title": "…", "author": "…", "questions": [ ... ]}, ... ]}``

题目对象字段别名见 ``STEM_KEYS`` / ``NUMBER_KEYS`` / ``ANALYSIS_KEYS`` 等常量；
题型支持中英文（判断题/单选题/多选题、judge/single_choice/multiple_choice）；
选项支持 dict、``A: xx\\rB: yy`` 分隔文本、``A. xx`` 内联单行、列表；答案支持
字符串与列表（``["A","C"]`` 与 ``"AC"`` 等价）。

归一约定：
* **判断题**常写成 ``A: 正确 / B: 错误`` 两个选项，本系统判断题约定为
  「无选项 + 答案 √/×」，因此这组二选一会折叠掉并把 A/B 映射为 √/×。
* 选择题的题型以**答案字母个数**为准（声明为单选却给 ``AB`` 时按多选处理），
  避免 ``normalize_answer`` 静默丢字母造成答案信息损失。
* 题号缺省时按出现顺序补 ``1..n``；多题库包内每个题库单独计数（不同题库的
  「第 1 题」互不影响，不会误报 duplicate_number）。

对外可复用的归一函数（Markdown 解析器与手录接口共用，防止规则漂移）：
:func:`map_qtype`、:func:`fold_judge_options`、:func:`resolve_qtype`。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .extractor import (
    JUDGE_FALSE,
    JUDGE_TRUE,
    Parsed,
    decide_qtype,
    normalize_answer,
    split_inline_options,  # 复用同一套内联选项切分，避免两处字母规则漂移
)
from .reader import ReaderError, decode_bytes

# ---------------------------------------------------------------------------
# 字段别名
# ---------------------------------------------------------------------------
STEM_KEYS: tuple[str, ...] = ("content", "stem", "title", "question", "question_content", "body", "text")
NUMBER_KEYS: tuple[str, ...] = ("number", "no", "num", "index", "seq", "order", "question_no")
ANALYSIS_KEYS: tuple[str, ...] = ("explanation", "analysis", "analyse", "explain", "comment", "remark", "解析")
OPTION_KEYS: tuple[str, ...] = ("options", "option", "choices", "selects")
ANSWER_KEYS: tuple[str, ...] = ("answer", "answers", "correct_answer", "standard_answer", "key")
TYPE_KEYS: tuple[str, ...] = ("type", "qtype", "question_type", "category")
TITLE_KEYS: tuple[str, ...] = ("title", "name", "bank_title", "book_title")
AUTHOR_KEYS: tuple[str, ...] = ("author", "teacher", "owner")
QUESTION_LIST_KEYS: tuple[str, ...] = ("questions", "items", "rows", "list", "question_list")

# 题型别名：key 已去掉空格/下划线/连字符并转小写
TYPE_MAP: Dict[str, str] = {
    "判断题": "judge",
    "判断": "judge",
    "是非题": "judge",
    "judge": "judge",
    "judgement": "judge",
    "judgment": "judge",
    "truefalse": "judge",
    "tf": "judge",
    "bool": "judge",
    "单选题": "single",
    "单选": "single",
    "单项选择题": "single",
    "single": "single",
    "singlechoice": "single",
    "radio": "single",
    "choice": "single",
    "多选题": "multiple",
    "多选": "multiple",
    "多项选择题": "multiple",
    "multiple": "multiple",
    "multiplechoice": "multiple",
    "multi": "multiple",
    "multipleselect": "multiple",
    "checkbox": "multiple",
    "填空题": "unknown",
    "填空": "unknown",
    "简答题": "unknown",
    "问答题": "unknown",
    "unknown": "unknown",
    "未知题型": "unknown",  # 本系统导出 Markdown 时的原样标签，保证往返可解
}

_OPT_LINE_RE = re.compile(r"^\s*([A-H])\s*[:：.、．)）]\s*(.*)$")
_TYPE_CLEAN_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


@dataclass
class RawBank:
    """一个题库（JSON 内的一个 ``bank``；单题库文件就是一个）。"""

    name: str
    """展示用名称：单题库文件用原始文件名，多题库包用「标题（作者）.json」。"""

    questions: List[Parsed] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------
def load_banks(path: Path | str) -> List[RawBank]:
    """读取 JSON 题库文件，返回题库列表（解析失败抛 ``ReaderError``）。"""
    file_path = Path(path)
    try:
        raw = file_path.read_bytes()
    except OSError as exc:  # pragma: no cover - 磁盘异常
        raise ReaderError(f"读取 JSON 文件失败：{exc}") from exc
    if not raw:
        raise ReaderError("文件内容为空")

    text, encoding = decode_bytes(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReaderError(
            f"JSON 格式错误（第 {exc.lineno} 行第 {exc.colno} 列）：{exc.msg}"
        ) from exc
    except Exception as exc:  # pragma: no cover - 极端编码问题
        raise ReaderError(f"JSON 解析失败（按 {encoding} 解码）：{exc}") from exc

    bank_dicts = list(_iter_bank_dicts(data))
    if not bank_dicts:
        raise ReaderError("JSON 中未找到题目：需要 questions 数组或 banks 数组")

    multi = len(bank_dicts) > 1
    banks: List[RawBank] = []
    for index, bank in enumerate(bank_dicts):
        items = _question_items(bank)
        questions = [_to_parsed(item, offset) for offset, item in enumerate(items)]
        questions = [question for question in questions if question is not None]
        if not questions:
            continue
        banks.append(
            RawBank(name=_bank_name(bank, file_path, index, multi), questions=questions)
        )

    if not banks:
        raise ReaderError("JSON 中未找到可识别的题目数据")
    return banks


# ---------------------------------------------------------------------------
# 形态识别
# ---------------------------------------------------------------------------
def _iter_bank_dicts(data: Any) -> Iterable[Dict[str, Any]]:
    """把任意受支持的顶层结构归一为「题库 dict」序列。"""
    if isinstance(data, dict):
        banks = data.get("banks")
        if isinstance(banks, list):
            for item in banks:
                if isinstance(item, dict):
                    yield item
            return
        nested = data.get("data")
        if isinstance(nested, (dict, list)) and not _question_items(data):
            yield from _iter_bank_dicts(nested)
            return
        yield data
        return
    if isinstance(data, list):
        yield {"questions": data}


def _question_items(bank: Dict[str, Any]) -> List[Any]:
    """取题库下的题目列表（兼容 list 与 id→题目 的 dict）。"""
    for key in QUESTION_LIST_KEYS:
        value = bank.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return list(value.values())
    return []


def _bank_name(bank: Dict[str, Any], file_path: Path, index: int, multi: bool) -> str:
    """题库展示名：单题库沿用文件名，多题库用「标题（作者）.json」。"""
    if not multi:
        return file_path.name
    title = _first_str(bank, TITLE_KEYS)
    author = _first_str(bank, AUTHOR_KEYS)
    if title and author:
        return f"{title}（{author}）.json"
    if title:
        return f"{title}.json"
    return f"{file_path.stem}-{index + 1}.json"


# ---------------------------------------------------------------------------
# 单题映射
# ---------------------------------------------------------------------------
def _to_parsed(item: Any, offset: int) -> Optional[Parsed]:
    """把一个题目对象映射为 ``Parsed``（异常收敛为 parse_error，不中断整批）。"""
    no = _number_text(item, offset)
    if not isinstance(item, dict):
        return Parsed(no=no, stem="", parse_error=True, error_detail="题目不是对象结构，无法解析")

    try:
        stem = _text_of(_first_value(item, STEM_KEYS))
        options = _options_of(_first_value(item, OPTION_KEYS))
        answer_raw = _answer_of(_first_value(item, ANSWER_KEYS))
        analysis = _text_of(_first_value(item, ANALYSIS_KEYS))
        declared = map_qtype(_first_value(item, TYPE_KEYS))
    except Exception as exc:  # pragma: no cover - 结构异常兜底
        return Parsed(no=no, stem="", parse_error=True, error_detail=f"字段解析异常：{exc}")

    qtype = resolve_qtype(declared, options, answer_raw)
    if qtype == "judge":
        options, answer_raw = fold_judge_options(options, answer_raw)

    return Parsed(
        no=no,
        stem=stem,
        options=dict(sorted(options.items())),
        answer=normalize_answer(answer_raw, qtype),
        analysis=analysis,
        qtype=qtype,
    )


def resolve_qtype(
    declared: Optional[str], options: Dict[str, str], answer: str
) -> str:
    """综合「声明题型 + 选项 + 答案」判定最终题型（JSON / Markdown 同一口径）。

    规则：
    * 声明缺失（``None`` / 空串）→ 用 :func:`decide_qtype` 按选项与答案推断；
    * 声明为 ``single`` / ``multiple`` 但与答案字母个数冲突时**以答案为准**
      （例如声明单选却给 ``ABD``，按多选存，避免 ``normalize_answer`` 静默丢字母）；
    * 声明为 ``judge`` / ``unknown`` 时保留声明值。

    Args:
        declared: ``map_qtype`` 的结果（``single``/``multiple``/``judge``/``unknown``/``None``）。
        options: 选项字典。
        answer: 原始答案文本。

    Returns:
        最终题型字符串（``single``/``multiple``/``judge``/``unknown``）。
    """
    qtype = declared or decide_qtype(options, answer)
    if declared in ("single", "multiple"):
        decided = decide_qtype(options, answer)
        if decided in ("single", "multiple"):
            qtype = decided
    return qtype


def fold_judge_options(options: Dict[str, str], answer: str) -> tuple[Dict[str, str], str]:
    """判断题：把 A/B「正确/错误」选项折叠为「无选项 + √/×」。

    仅当选项恰好是 ``A/B`` 两个、且内容确实为「正确/错误」（或对/错、T/F 等
    同义词）时才折叠，并把答案里的 ``A``/``B`` 映射为 ``√``/``×``；
    其它情况原样返回，避免误伤真实的二选项选择题。

    Args:
        options: 原选项字典。
        answer: 原答案文本。

    Returns:
        ``(折叠后的选项, 折叠后的答案)``。
    """
    if set(options) != {"A", "B"}:
        return options, answer

    first = options["A"].strip().upper()
    second = options["B"].strip().upper()
    token = (answer or "").strip().upper()

    if first in JUDGE_TRUE and second in JUDGE_FALSE:
        return {}, {"A": "√", "B": "×"}.get(token, answer)
    if first in JUDGE_FALSE and second in JUDGE_TRUE:
        return {}, {"A": "×", "B": "√"}.get(token, answer)
    return options, answer


# ---------------------------------------------------------------------------
# 字段取值工具
# ---------------------------------------------------------------------------
def _first_value(item: Any, keys: Iterable[str]) -> Any:
    """按别名取第一个非空字段（大小写不敏感）。"""
    if not isinstance(item, dict):
        return None
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    lowered = {str(key).strip().lower(): value for key, value in item.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value not in (None, ""):
            return value
    return None


def _first_str(item: Any, keys: Iterable[str]) -> str:
    return _text_of(_first_value(item, keys))


def _text_of(value: Any) -> str:
    """把任意标量/列表折成单行文本（与文本管线的「多行合并为一行」保持一致）。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return _collapse(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        parts = [_text_of(item) for item in value if item is not None]
        return _collapse(" ".join(part for part in parts if part))
    if isinstance(value, dict):
        return _collapse(" ".join(_text_of(item) for item in value.values()))
    return _collapse(str(value))


def _collapse(text: str) -> str:
    """空白折叠：``\\r\\n``、全角空格、连续空格统一为单个半角空格。"""
    return re.sub(r"\s+", " ", (text or "").replace("\u3000", " ")).strip()


def _number_text(item: Any, offset: int) -> str:
    """题号：优先取原字段，缺省按顺序补 1..n。"""
    value = _first_value(item, NUMBER_KEYS)
    if value is None or (isinstance(value, str) and not value.strip()):
        return str(offset + 1)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def map_qtype(value: Any) -> Optional[str]:
    """题型归一；无法识别时返回 None（交由答案/选项推断）。"""
    if value is None:
        return None
    key = _TYPE_CLEAN_RE.sub("", str(value).lower())
    return TYPE_MAP.get(key)


def _answer_of(value: Any) -> str:
    """答案归一：列表拼接、布尔转 √/×、其余转字符串。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "√" if value else "×"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        parts: List[str] = []
        for item in value:
            if isinstance(item, bool):
                parts.append("√" if item else "×")
            elif isinstance(item, dict):
                nested = _first_value(item, ANSWER_KEYS)
                parts.append(_answer_of(nested if nested is not None else list(item.values())))
            elif item is not None:
                parts.append(str(item).strip())
        return "".join(part for part in parts if part)
    return str(value).strip()


def _options_of(value: Any) -> Dict[str, str]:
    """选项归一：dict / 分隔文本 / 列表（字符串或 ``{key,value}`` 对象）。"""
    if value is None:
        return {}
    if isinstance(value, dict):
        result: Dict[str, str] = {}
        for key, item in value.items():
            letter = str(key).strip().upper()
            text = _text_of(item)
            if re.fullmatch(r"[A-H]", letter) and text:
                result[letter] = text
        return result
    if isinstance(value, (list, tuple)):
        result = {}
        for item in value:
            if isinstance(item, dict):
                letter = _first_str(item, ("key", "letter", "label", "option", "index"))
                text = _first_str(item, ("value", "text", "content", "detail", "option_content"))
                if re.fullmatch(r"[A-H]", letter.strip().upper()) and text:
                    result[letter.strip().upper()] = text
                else:
                    result.update(_options_of(item))
            else:
                result.update(_options_of_text(str(item)))
        return result or _options_of_text("\n".join(str(item) for item in value))
    return _options_of_text(str(value))


def _options_of_text(text: str) -> Dict[str, str]:
    """解析 ``A: xx\\rB: yy`` / ``A. xx`` 多行文本，或单行内联 ``A. x B. y``。"""
    if not text.strip():
        return {}
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    result: Dict[str, str] = {}
    current: Optional[str] = None
    for line in normalized.split("\n"):
        matched = _OPT_LINE_RE.match(line)
        if matched:
            current = matched.group(1).upper()
            result[current] = matched.group(2).strip()
        elif current and line.strip():
            # 选项文本换行续写（PDF/Word 导出常见）
            result[current] = f"{result[current]} {line.strip()}".strip()
    if result:
        return {letter: _collapse(text) for letter, text in result.items()}

    _, inline = split_inline_options(normalized)
    return {letter: _collapse(text) for letter, text in inline.items()}
