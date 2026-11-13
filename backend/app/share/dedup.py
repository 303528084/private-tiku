"""题目内容指纹与查重。

**为什么不含 ``analysis`` / ``id`` / ``question_no``**：

* ``analysis``（解析）属于讲解文本，同一道题在不同来源里解析详略不同（有的写
  100 字、有的空着），把它算进指纹会让同一道题被当成两道题重复入库；
* ``id`` 是库内自增主键，跨库必然不同；
* ``question_no`` 是编号习惯（很多题库按题型分段各自从 1 编号），同一道题换个
  编号还是同一道题。

因此指纹 = ``stem`` + 排序后的 ``options`` + ``answer`` + ``qtype`` 的规范化拼接。
字段值先过 :func:`app.parser.normalize.normalize_text`（全角→半角、压缩空白），
避免「同一题只因多了一个全角空格就算不同题」。

**v1.4 不加列**（架构 §9.1 T03 风险）：指纹只在内存里现算现比，不写
``questions.content_hash``。1 万题的现场计算约 200ms，可接受。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Iterable, Mapping, Sequence, Set

from .. import db
from ..parser.normalize import normalize_text

FIELD_SEPARATOR: str = "\x1f"
"""字段分隔符（ASCII US，正文里几乎不可能出现，避免拼接歧义）。"""


def _get(question: Any, key: str) -> Any:
    """从 dict / 对象里取字段（``Parsed`` 与 ``question_to_dict`` 的输出都能吃）。"""
    if isinstance(question, Mapping):
        return question.get(key)
    return getattr(question, key, None)


def content_hash(question: Any) -> str:
    """计算一道题的内容指纹。

    Args:
        question: 支持两种形态 —— ``db.question_to_dict`` 的 dict（键 ``stem`` /
            ``options`` / ``answer`` / ``qtype``），或 :class:`app.parser.Parsed`
            （同名属性）。

    Returns:
        64 位小写十六进制 SHA-256。
    """
    stem = normalize_text(str(_get(question, "stem") or ""))
    raw_options = _get(question, "options") or {}
    options = {
        str(key).strip().upper(): normalize_text(str(value or ""))
        for key, value in dict(raw_options).items()
    }
    answer = normalize_text(str(_get(question, "answer") or ""))
    qtype = str(_get(question, "qtype") or "").strip()
    payload = FIELD_SEPARATOR.join(
        [
            stem,
            json.dumps(dict(sorted(options.items())), ensure_ascii=False),
            answer,
            qtype,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def existing_hashes(conn: sqlite3.Connection) -> Set[str]:
    """对库内**全部**题目现场计算指纹（含 pending，避免与待入库题重复）。

    Args:
        conn: 数据库连接。

    Returns:
        指纹集合。
    """
    rows = conn.execute(
        "SELECT stem, options_json, answer, qtype FROM questions"
    ).fetchall()
    return {
        content_hash(
            {
                "stem": row["stem"],
                "options": db.parse_options(row["options_json"]),
                "answer": row["answer"],
                "qtype": row["qtype"],
            }
        )
        for row in rows
    }


def find_duplicates(
    conn: sqlite3.Connection, hashes: Iterable[str]
) -> Set[str]:
    """在库内查找与给定指纹重复的题目。

    Args:
        conn: 数据库连接。
        hashes: 待检查的指纹集合（分享包内新题的指纹）。

    Returns:
        ``hashes`` 中**已存在于库内**的那部分。
    """
    wanted = {value for value in hashes if value}
    if not wanted:
        return set()
    return wanted & existing_hashes(conn)


def count_duplicates(
    conn: sqlite3.Connection, hashes: Sequence[str]
) -> int:
    """按**条数**统计重复（同一指纹在包内出现两次算两条）。

    Args:
        conn: 数据库连接。
        hashes: 按题目顺序排列的指纹列表。

    Returns:
        与库内重复的题目条数。
    """
    if not hashes:
        return 0
    known = existing_hashes(conn)
    return sum(1 for value in hashes if value in known)


__all__ = ["content_hash", "count_duplicates", "existing_hashes", "find_duplicates"]
