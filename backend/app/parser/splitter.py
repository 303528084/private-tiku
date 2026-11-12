"""题目切分层：按题号正则把行序列切成题目块。

支持三种题号形态（架构 §5 的后缀式 + v1.1 增补的两种常见排版）::

    （79）题干              ← 括号前缀（括号内必须是纯数字）
    第80题. 题干            ← 「第N题」前缀
    12. 题干 / 12、题干      ← 原后缀式（含 12） 12． 12。 等）

命中即开新块；首个题号之前的散落文本（如文档标题）被丢弃，
但若整篇文本完全无法识别题号，则返回一个「伪块」交给 validator 标记 parse_error。

**误判防护**：括号分支要求括号内为 1–4 位纯数字，因此填空题的 `（  ）`、
选项字母 `（A）`、超长数字 `（12345）` 都不会被当作题号。

**括号编号的条件切分（v1.1.1 D5）**：护理试题里常用 `（1）（2）` 在题内列举要点，
与「括号题号」形态完全同形。因此括号题号只在**当前题已收尾**时才开新块，即满足任一：

* 当前还没有任何块（文档里的第一条题号）；
* 当前块已经出现过答案行或解析行（上一题已答完/已解析）；
* 当前块已经收集到至少 1 个选项行（v1.1.1 扩展：`missing_answer` 是 P0-4 主动标记的
  异常类型，「有选项、答案空着」在护理题库里很常见；若不把这种情况视为收尾，
  紧跟其后的括号编号题会被并进来，后题答案错位到前题，属内容级错误）。

其余情况（当前块只有题干、正在攒选项）一律视为题内列举，并入当前块。
`第N题` 与后缀式 `1.` 没有这种列举歧义，仍**无条件**开新块。

**已知限制**：若前一道括号编号题**既无选项又无答案**（判断题缺答案、或文本残缺），
上述三条都不满足，后一道括号题仍会被并入。这类文档本身数据残缺，人工校对时可见。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .reader import Line

# 三种题号前缀（顺序即优先级：括号 → 第N题 → 后缀式）
_BRACKET_NO = r"[（(]\s*(\d{1,4})\s*[）)]"
_CHAPTER_NO = r"第\s*(\d{1,4})\s*题(?:\s*[.、。．)）:：])?"
_PLAIN_NO = r"(?:第\s*)?(\d{1,4})\s*[.、。．)）]"

# 分组顺序必须与 KIND_BY_GROUP 一致
NO_PREFIX_RE = re.compile(
    r"^\s*(?:" + _BRACKET_NO + r"|" + _CHAPTER_NO + r"|" + _PLAIN_NO + r")\s*"
)

NO_KIND_BRACKET = "bracket"
NO_KIND_CHAPTER = "chapter"
NO_KIND_PLAIN = "plain"
KIND_BY_GROUP: Tuple[str, ...] = (NO_KIND_BRACKET, NO_KIND_CHAPTER, NO_KIND_PLAIN)

MAX_QUESTION_NO = 4000
"""题号上限：超过该值多半是年份/数值误判，不作为题号。"""

_closing_patterns: Optional[Tuple[re.Pattern[str], ...]] = None


@dataclass
class Block:
    """一个题目块。"""

    no_text: str
    lines: List[Line] = field(default_factory=list)


def _match_detail(text: str) -> Optional[Tuple[str, str]]:
    """返回 (归一化前题号串, 形态)，未命中返回 None。"""
    matched = NO_PREFIX_RE.match(text)
    if not matched:
        return None
    for index, group in enumerate(matched.groups()):
        if group:
            return group, KIND_BY_GROUP[index]
    return None  # pragma: no cover - 正则保证必有分组命中


def _closing_regexes() -> Tuple[re.Pattern[str], ...]:
    """取「答案行 / 解析行 / 选项行」正则（延迟导入，避免 splitter ↔ extractor 循环依赖）。"""
    global _closing_patterns
    if _closing_patterns is None:
        from .extractor import ANALYSIS_RE, ANSWER_RE, OPTION_RE

        _closing_patterns = (ANSWER_RE, ANALYSIS_RE, OPTION_RE)
    return _closing_patterns


def is_block_closed(block: Block) -> bool:
    """当前块是否已收尾（判定「括号编号是题号还是题内列举」的唯一依据）。

    以下任一条件满足即视为收尾（v1.1.1 D5）：

    1. 块内已出现答案行或解析行；
    2. 块内已收集到至少 1 个选项行 —— 用于覆盖「有选项、答案空着」的常见题型
       （``missing_answer`` 正是 P0-4 要主动标记的异常之一）。

    只看到题干、还没攒到选项时不算收尾，因此
    ``5. 题干? / （1）… / （2）… / A-D 选项 / 答案：ABC`` 仍是 1 块。
    """
    patterns = _closing_regexes()
    for line in block.lines:
        for pattern in patterns:
            if pattern.match(line.text):
                return True
    return False


def match_no(text: str) -> str | None:
    """若该行以题号开头，返回归一化题号字符串，否则 None。"""
    detail = _match_detail(text)
    if detail is None:
        return None
    no_text, _kind = detail
    try:
        value = int(no_text)
    except ValueError:  # pragma: no cover - 正则已保证是数字
        return None
    if value <= 0 or value > MAX_QUESTION_NO:
        return None
    # 归一化题号（"03" -> "3"），便于同文件内重复题号统计
    return str(value)


def strip_no_prefix(text: str) -> str:
    """去掉行首题号前缀（含括号 / 「第N题」整段），返回剩余内容。"""
    return NO_PREFIX_RE.sub("", text, count=1).strip()


def split(lines: List[Line]) -> List[Block]:
    """把行序列切分为题目块列表。"""
    blocks: List[Block] = []
    current: Block | None = None

    for line in lines:
        detail = _match_detail(line.text)
        no_text = match_no(line.text) if detail is not None else None

        if no_text is not None:
            kind = detail[1] if detail is not None else NO_KIND_PLAIN
            # 括号编号可能只是题内列举：当前题还没收尾（无答案/解析/选项）时并入当前块
            # （v1.1.1 D5：收尾判定含「已收集 ≥1 个选项行」）
            if (
                kind == NO_KIND_BRACKET
                and current is not None
                and not is_block_closed(current)
            ):
                current.lines.append(line)
                continue
            if current is not None:
                blocks.append(current)
            current = Block(no_text=no_text, lines=[line])
            continue

        if current is None:
            # 首个题号之前的标题/说明，丢弃
            continue
        current.lines.append(line)

    if current is not None:
        blocks.append(current)

    if not blocks and lines:
        # 完全无法识别题号：整篇作为一个块，交由 validator 标记 parse_error
        return [Block(no_text="", lines=list(lines))]
    return blocks
