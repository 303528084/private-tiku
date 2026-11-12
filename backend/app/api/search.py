"""全文搜索 API：FTS5 MATCH + 筛选 + 服务端转义的 <mark> 高亮。

降级策略（架构 §3）：
* ``FTS_TRIGRAM = True`` 且所有关键词长度 ≥3 → ``questions_fts MATCH``；
* 否则（短词/分词器降级）→ ``LIKE`` 全字段匹配。
多关键词以 AND 组合；只检索 ``status='published'`` 的题目。
"""

from __future__ import annotations

import html
import re
import sqlite3
import time
from typing import Any, Dict, List, Sequence, Tuple

from fastapi import APIRouter, Query

from .. import config, db
from ..models import CODE_PARAM_ERROR, QTYPE_VALUES, ApiError, ok
from ..parser.normalize import normalize_text

router = APIRouter(prefix="/search", tags=["search"])

TERM_SPLIT_RE = re.compile(r"[\s,，;；]+")

LIKE_TEXT_COLUMNS: tuple[str, ...] = ("stem", "answer", "analysis")
"""LIKE 降级路径会直接匹配的「纯文本列」。

``options_json`` **不在其中**：它存的是 ``{"A": "房颤", ...}`` 这样的 JSON 原文，
直接 LIKE 会让 ``"`` ``{`` ``:`` ``,`` 这类结构字符命中全库（v1.1 修复 D2）。
选项改为经 SQLite JSON1 的 ``json_each()`` 只匹配 **值**（选项文本）。
"""

JSON_STRUCTURAL_CHARS: frozenset[str] = frozenset('{}[]":,')
"""JSON 结构字符：仅由这些字符组成的检索词不参与选项匹配。"""

_json1_available: bool | None = None


def json1_available() -> bool:
    """检测当前 SQLite 是否内置 JSON1（结果缓存）。"""
    global _json1_available
    if _json1_available is None:
        try:
            with db.get_conn() as conn:
                conn.execute("SELECT value FROM json_each('{\"A\":\"x\"}')").fetchall()
            _json1_available = True
        except sqlite3.OperationalError:
            _json1_available = False
    return _json1_available


def _is_structural_only(term: str) -> bool:
    """检索词是否只由 JSON 结构字符构成（如 ``"`` / ``{`` / ``:`` / ``,``）。"""
    return bool(term) and all(char in JSON_STRUCTURAL_CHARS for char in term)


# ---------------------------------------------------------------------------
# 高亮
# ---------------------------------------------------------------------------
WRAPPING_QUOTE_CHARS: str = "\"'“”‘’"
"""检索词首尾的包裹引号（normalize 后主要剩 ASCII 引号，这里保留全角以便兜底）。"""


def _strip_wrapping_quotes(term: str) -> str:
    """剥掉检索词首尾的引号，使 ``"房颤"`` 与 ``房颤`` 等价（v1.1.1 D4）。

    背景：FTS 表索引的是 ``options_json`` 原文（``{"A":"房颤",...}``），
    trigram 分词器会让含引号的查询命中 JSON 标点；而 LIKE 降级路径只匹配选项**值**，
    同一查询在两条路径上结果不一致。剥掉首尾引号后两条路径的检索词完全相同，
    因此命中集合必然一致（不需要把引号词降级成慢路径）。
    """
    stripped = (term or "").strip()
    while stripped and stripped[0] in WRAPPING_QUOTE_CHARS:
        stripped = stripped[1:]
    while stripped and stripped[-1] in WRAPPING_QUOTE_CHARS:
        stripped = stripped[:-1]
    return stripped.strip()


def parse_terms(query: str) -> List[str]:
    """把查询串拆成去重关键词（保持原顺序，剥除首尾引号）。"""
    text = normalize_text(query or "")
    if not text:
        return []
    terms: List[str] = []
    for chunk in TERM_SPLIT_RE.split(text):
        term = _strip_wrapping_quotes(chunk)
        # 剥完引号后为空（例如查询就是 "" ）→ 丢弃该词
        if term and term not in terms:
            terms.append(term)
    return terms


def highlight_html(text: str, terms: Sequence[str]) -> str:
    """先 HTML 转义，再把命中词用 <mark> 包裹（重叠区间合并，避免嵌套标签）。"""
    if not text:
        return ""
    escaped = html.escape(text, quote=False)
    if not terms:
        return escaped

    spans: List[Tuple[int, int]] = []
    for term in terms:
        escaped_term = html.escape(term, quote=False)
        if not escaped_term:
            continue
        for matched in re.finditer(re.escape(escaped_term), escaped, flags=re.IGNORECASE):
            spans.append((matched.start(), matched.end()))
    if not spans:
        return escaped

    spans.sort()
    merged: List[Tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))

    parts: List[str] = []
    cursor = 0
    for start, end in merged:
        parts.append(escaped[cursor:start])
        parts.append("<mark>" + escaped[start:end] + "</mark>")
        cursor = end
    parts.append(escaped[cursor:])
    return "".join(parts)


def _like_pattern(term: str) -> str:
    """构造 LIKE 模式串（转义 % 与 _）。"""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _fts_query(terms: Sequence[str]) -> str:
    """构造 FTS5 MATCH 表达式：``"kw1" AND "kw2"``。"""
    return " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)


def _can_use_fts(terms: Sequence[str]) -> bool:
    """trigram 分词器下 MATCH 需要每个关键词长度 ≥ 3。

    仅由 JSON 结构字符构成的检索词也不走 MATCH：trigram 会在 ``options_json``
    原文里命中大量结构片段（v1.1 D2）。
    """
    if not db.FTS_TRIGRAM or not terms:
        return False
    if any(len(term) < config.FTS_MIN_TOKEN_LEN for term in terms):
        return False
    return not any(_is_structural_only(term) for term in terms)


def _like_clause(term: str, use_json: bool) -> Tuple[str, List[Any]]:
    """构造单个关键词的 LIKE 子句：纯文本列 + 选项**文本值**（不含 JSON 结构）。"""
    like = _like_pattern(term)
    parts = [f"q.{column} LIKE ? ESCAPE '\\'" for column in LIKE_TEXT_COLUMNS]
    params: List[Any] = [like] * len(LIKE_TEXT_COLUMNS)

    if use_json:
        # JSON1 可用：只匹配 json_each 展开后的「值」，结构字符天然不命中
        parts.append(
            "EXISTS (SELECT 1 FROM json_each("
            "CASE WHEN json_valid(q.options_json) THEN q.options_json ELSE '{}' END"
            ") WHERE json_each.value LIKE ? ESCAPE '\\')"
        )
        params.append(like)
    elif not _is_structural_only(term):
        # 兜底（无 JSON1 的构建）：结构字符词直接不匹配选项原文
        parts.append("q.options_json LIKE ? ESCAPE '\\'")
        params.append(like)

    return "(" + " OR ".join(parts) + ")", params


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def _build_where(
    terms: Sequence[str],
    qtype: str | None,
    only_anomaly: bool,
    doc_id: int | None,
    force_like: bool = False,
    use_json: bool = True,
) -> Tuple[List[str], List[Any], bool]:
    """返回 (WHERE 片段, 参数, 是否使用 FTS)。"""
    clauses: List[str] = ["q.status = 'published'"]
    params: List[Any] = []

    if qtype:
        if qtype not in QTYPE_VALUES:
            raise ApiError(f"非法的题型筛选值：{qtype}", CODE_PARAM_ERROR, 400)
        clauses.append("q.qtype = ?")
        params.append(qtype)
    if only_anomaly:
        clauses.append(
            "EXISTS (SELECT 1 FROM anomalies a WHERE a.question_id = q.id AND a.handled = 0)"
        )
    if doc_id:
        clauses.append("q.import_file_id = ?")
        params.append(int(doc_id))

    use_fts = (not force_like) and _can_use_fts(terms)
    if terms:
        if use_fts:
            clauses.append("questions_fts MATCH ?")
            params.append(_fts_query(terms))
        else:
            for term in terms:
                clause, clause_params = _like_clause(term, use_json)
                clauses.append(clause)
                params.extend(clause_params)
    return clauses, params, use_fts


def _execute_query(
    conn: sqlite3.Connection,
    from_sql: str,
    where_sql: str,
    params: Sequence[Any],
    order_sql: str,
    size: int,
    offset: int,
) -> Tuple[int, List[sqlite3.Row]]:
    """执行计数 + 分页查询。"""
    total_row = conn.execute(f"SELECT COUNT(*) AS c {from_sql} WHERE {where_sql}", list(params)).fetchone()
    rows = conn.execute(
        f"SELECT q.*, f.filename AS filename {from_sql} WHERE {where_sql} "
        f"{order_sql} LIMIT ? OFFSET ?",
        [*params, size, offset],
    ).fetchall()
    return int(total_row["c"]), list(rows)


def search_questions(
    q: str = "",
    qtype: str | None = None,
    only_anomaly: bool = False,
    doc_id: int | None = None,
    page: int = 1,
    size: int = config.DEFAULT_PAGE_SIZE,
) -> Dict[str, Any]:
    """执行检索（供 API 与测试复用）。"""
    started = time.perf_counter()
    terms = parse_terms(q)
    page = max(1, int(page))
    size = max(1, min(int(size), config.MAX_PAGE_SIZE))
    offset = (page - 1) * size

    # 查询串非空但拆不出任何关键词（例如只有逗号/分号等分隔符）：
    # 视为「无效检索词」返回空集，而不是退化成「列出全库」（v1.1 D2 的语义收敛）。
    if (q or "").strip() and not terms:
        return {
            "items": [],
            "total": 0,
            "took_ms": int(round((time.perf_counter() - started) * 1000)),
            "page": page,
            "size": size,
        }

    use_json = json1_available()
    clauses, params, use_fts = _build_where(
        terms, qtype, only_anomaly, doc_id, use_json=use_json
    )
    where_sql = " AND ".join(clauses)

    from_fts = (
        "FROM questions q "
        "JOIN questions_fts ON questions_fts.rowid = q.id "
        "JOIN import_files f ON f.id = q.import_file_id "
    )
    from_plain = "FROM questions q JOIN import_files f ON f.id = q.import_file_id "

    with db.get_conn() as conn:
        try:
            if use_fts:
                total, rows = _execute_query(
                    conn, from_fts, where_sql, params, "ORDER BY bm25(questions_fts), q.id ASC", size, offset
                )
            else:
                total, rows = _execute_query(
                    conn, from_plain, where_sql, params, "ORDER BY q.id ASC", size, offset
                )
        except sqlite3.OperationalError:
            if not use_fts:
                raise
            # MATCH 表达式异常时退化为 LIKE，保证搜索不 500
            like_clauses, like_params, _ = _build_where(
                terms, qtype, only_anomaly, doc_id, force_like=True, use_json=use_json
            )
            total, rows = _execute_query(
                conn,
                from_plain,
                " AND ".join(like_clauses),
                like_params,
                "ORDER BY q.id ASC",
                size,
                offset,
            )

        grouped = db.fetch_anomalies(conn, [int(row["id"]) for row in rows])

    items: List[Dict[str, Any]] = []
    for row in rows:
        item = db.question_to_dict(
            row,
            grouped.get(int(row["id"]), []),
            filename=dict(row).get("filename"),
        )
        items.append(
            {
                **item,
                "highlight": {
                    "stem": highlight_html(item["stem"], terms),
                    "options": {
                        letter: highlight_html(value, terms)
                        for letter, value in item["options"].items()
                    },
                    "answer": highlight_html(item["answer"], terms),
                    "analysis": highlight_html(item["analysis"], terms),
                },
            }
        )

    took_ms = int(round((time.perf_counter() - started) * 1000))
    return {"items": items, "total": total, "took_ms": took_ms, "page": page, "size": size}


# ---------------------------------------------------------------------------
# GET /api/search
# ---------------------------------------------------------------------------
@router.get("")
def search(
    q: str = Query(default="", description="关键词，空格分隔多关键词"),
    type: str | None = Query(default=None, description="题型筛选 single|multiple|judge|unknown"),
    only_anomaly: bool = Query(default=False, description="仅看有未处理异常的题"),
    doc_id: int | None = Query(default=None, description="按来源文件过滤（import_file_id）"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=config.DEFAULT_PAGE_SIZE, ge=1, le=config.MAX_PAGE_SIZE),
) -> Dict[str, Any]:
    """全文搜索 + 筛选 + 高亮。"""
    result = search_questions(
        q=q, qtype=type, only_anomaly=only_anomaly, doc_id=doc_id, page=page, size=size
    )
    return ok(result)
