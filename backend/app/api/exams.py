"""练习 / 模拟考试 API（v1.5）。

核心设计：

* **快照试卷**：开考时把抽中的题目（题干/选项/答案/解析/题型）整体复制进
  ``exam_answers``（竞品调研结论：不存快照，删题会让已存试卷残缺）。
  原题被删除后，试卷按快照继续作答与回看；``question_id`` 保留原引用，
  原题还在时可跳详情页对照。
* **抽题口径**：只抽 ``published`` 且**无未处理异常**、题型非 ``unknown`` 的题
  （缺答案/缺选项的题无法判分，会污染成绩）。范围按「题库」聚合语义
  （``(filename, file_type)``）解析，与 ``GET /api/libraries`` 的口径一致。
* **判分**：复用 ``parser.extractor.normalize_answer``（多选字母升序、判断题
  统一 √/×），归一化后精确比对；多选不做半对给分。
* **答案泄露控制**：作答中（``in_progress``）只对**已作答**的题目返回
  ``answer`` / ``analysis`` / ``is_correct``——考试模式靠这一点保证未答题看不到
  答案；练习模式前端答一题提交一题，自然拿到即时判分。交卷后全部放开。
"""

from __future__ import annotations

import json
import random
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from .. import config, db
from ..models import (
    CODE_NOT_FOUND,
    CODE_PARAM_ERROR,
    ApiError,
    ExamAnswerRequest,
    ExamCreateRequest,
    ok,
)
from ..parser.extractor import normalize_answer

router = APIRouter(prefix="/exams", tags=["exams"])

EXAM_MODES: frozenset[str] = frozenset({"practice", "exam"})
QTYPE_FILTERS: frozenset[str] = frozenset({"single", "multiple", "judge"})
MAX_EXAM_COUNT: int = 200
"""单场题数上限（与 ``ExamCreateRequest.count`` 的上界一致）。"""


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------
def exam_to_dict(row: Any) -> Dict[str, Any]:
    """``exams`` 行 → dict。"""
    data = dict(row)
    try:
        scope: List[int] = json.loads(data.get("scope_json") or "[]")
        if not isinstance(scope, list):
            scope = []
        scope = [int(x) for x in scope]
    except (TypeError, ValueError):
        scope = []
    return {
        "id": int(data["id"]),
        "mode": data["mode"],
        "title": data["title"] or "",
        "library_ids": scope,
        "requested_count": int(data["requested_count"] or 0),
        "question_count": int(data["question_count"] or 0),
        "status": data["status"],
        "score": round(float(data["score"] or 0), 1),
        "correct_count": int(data["correct_count"] or 0),
        "answered_count": int(data["answered_count"] or 0),
        "created_at": data.get("created_at"),
        "finished_at": data.get("finished_at"),
    }


def exam_answer_to_dict(row: Any, reveal: bool) -> Dict[str, Any]:
    """``exam_answers`` 行 → dict。

    Args:
        row: 数据库行。
        reveal: 是否放开 ``answer`` / ``analysis`` / ``is_correct``
            （已作答或已交卷时为 True；未作答且考试进行中为 False，防泄题）。
    """
    data = dict(row)
    item: Dict[str, Any] = {
        "id": int(data["id"]),
        "exam_id": int(data["exam_id"]),
        "question_id": None if data["question_id"] is None else int(data["question_id"]),
        "seq": int(data["seq"]),
        "qtype": data["qtype"],
        "stem": data["stem"],
        "options": db.parse_options(data.get("options_json")),
        "answer_given": data["answer_given"] or "",
        "answered": data["answered_at"] is not None,
    }
    if reveal:
        item["answer"] = data["answer"] or ""
        item["analysis"] = data["analysis"] or ""
        item["is_correct"] = None if data["is_correct"] is None else bool(data["is_correct"])
    return item


# ---------------------------------------------------------------------------
# 抽题
# ---------------------------------------------------------------------------
def _resolve_library_ids(conn: Any, library_ids: List[int]) -> List[int]:
    """把「题库 id」解析为其覆盖的全部 ``import_files.id``。

    ``GET /api/libraries`` 按 ``(filename, file_type)`` 聚合、``id`` 取组内最小值；
    同名库可能对应多条 ``import_files`` 行，直接 ``IN (id)`` 会漏掉其余行的题目。
    """
    if not library_ids:
        return []
    cleaned = sorted({int(x) for x in library_ids if int(x) > 0})
    if not cleaned:
        return []
    placeholders = ",".join("?" for _ in cleaned)
    rows = conn.execute(
        f"SELECT filename, file_type FROM import_files WHERE id IN ({placeholders})",
        tuple(cleaned),
    ).fetchall()
    if not rows:
        return []
    conditions: List[str] = []
    params: List[Any] = []
    for row in rows:
        conditions.append("(filename = ? AND file_type = ?)")
        params.extend([row["filename"], row["file_type"]])
    where = " OR ".join(conditions)
    matched = conn.execute(
        f"SELECT id FROM import_files WHERE {where}", tuple(params)
    ).fetchall()
    return sorted({int(r["id"]) for r in matched})


def _pick_questions(
    conn: Any,
    library_ids: List[int],
    qtypes: List[str],
    count: int,
) -> List[Dict[str, Any]]:
    """随机抽题：published、无未处理异常、题型非 unknown。

    Returns:
        ``question_to_dict`` 列表（抽到几条是几条，调用方负责判断不足）。
    """
    params: List[Any] = []
    where = (
        "q.status = 'published'"
        " AND q.qtype != 'unknown'"
        " AND NOT EXISTS ("
        "  SELECT 1 FROM anomalies a"
        "   WHERE a.question_id = q.id AND a.handled = 0)"
    )
    # 子句与参数必须同序追加：先题型，后题库（占位符一一对应）
    if qtypes:
        qs = ",".join("?" for _ in qtypes)
        where += f" AND q.qtype IN ({qs})"
        params.extend(qtypes)
    if library_ids:
        placeholders = ",".join("?" for _ in library_ids)
        where += f" AND q.import_file_id IN ({placeholders})"
        params.extend(library_ids)
    sql = f"""
        SELECT q.*, f.filename AS filename
        FROM questions q
        LEFT JOIN import_files f ON f.id = q.import_file_id
        WHERE {where}
        ORDER BY RANDOM()
        LIMIT ?
    """
    params.append(count)
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [db.question_to_dict(row, [], filename=dict(row).get("filename")) for row in rows]


def _judge(given: str, expected: str, qtype: str) -> bool:
    """归一化后精确比对作答与标准答案。"""
    return normalize_answer(given, qtype) == normalize_answer(expected, qtype)


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------
@router.post("")
def create_exam(payload: ExamCreateRequest) -> Dict[str, Any]:
    """开一场练习/考试：随机抽题并落快照。

    题数不足时按实际可用题数开考（``question_count`` 为实际值）；
    一道题都抽不到时返回参数错误并提示。
    """
    mode = (payload.mode or "").strip()
    if mode not in EXAM_MODES:
        raise ApiError("mode 只能是 practice / exam", CODE_PARAM_ERROR, 400)
    qtypes = [q.strip() for q in (payload.qtypes or []) if q.strip()]
    bad = [q for q in qtypes if q not in QTYPE_FILTERS]
    if bad:
        raise ApiError(
            f"题型筛选只能是 single / multiple / judge：{bad[0]}", CODE_PARAM_ERROR, 400
        )
    count = int(payload.count)
    if count < 1 or count > MAX_EXAM_COUNT:
        raise ApiError(f"题数只能是 1–{MAX_EXAM_COUNT}", CODE_PARAM_ERROR, 400)

    with db.get_conn() as conn:
        file_ids = _resolve_library_ids(conn, payload.library_ids or [])
        picked = _pick_questions(conn, file_ids, qtypes, count)
        if not picked:
            raise ApiError("没有可用的题目：请先入库题目，或放宽题库/题型范围", CODE_PARAM_ERROR, 400)

        cursor = conn.execute(
            """
            INSERT INTO exams (mode, title, scope_json, requested_count, question_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                mode,
                "模拟考试" if mode == "exam" else "练习",
                json.dumps(payload.library_ids or []),
                count,
                len(picked),
            ),
        )
        exam_id = int(cursor.lastrowid)
        for seq, question in enumerate(picked, start=1):
            conn.execute(
                """
                INSERT INTO exam_answers
                  (exam_id, question_id, seq, qtype, stem, options_json, answer, analysis)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    exam_id,
                    int(question["id"]),
                    seq,
                    question["qtype"],
                    question["stem"],
                    db.dump_options(question["options"]),
                    question["answer"] or "",
                    question["analysis"] or "",
                ),
            )
        row = conn.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
    data = exam_to_dict(row)
    data["mode"] = mode
    return ok(data)


@router.get("")
def list_exams(
    mode: Optional[str] = Query(default=None, description="practice / exam"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=config.DEFAULT_PAGE_SIZE, ge=1, le=config.MAX_PAGE_SIZE),
) -> Dict[str, Any]:
    """练习/考试历史列表（按开始时间倒序）。"""
    clauses: List[str] = ["1 = 1"]
    params: List[Any] = []
    if mode:
        if mode not in EXAM_MODES:
            raise ApiError("mode 只能是 practice / exam", CODE_PARAM_ERROR, 400)
        clauses.append("mode = ?")
        params.append(mode)
    where_sql = " AND ".join(clauses)
    offset = (page - 1) * size
    with db.get_conn() as conn:
        total = int(
            conn.execute(
                f"SELECT COUNT(*) AS c FROM exams WHERE {where_sql}", params
            ).fetchone()["c"]
        )
        rows = conn.execute(
            f"SELECT * FROM exams WHERE {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
            [*params, size, offset],
        ).fetchall()
    return ok(
        {
            "items": [exam_to_dict(row) for row in rows],
            "total": total,
            "page": page,
            "size": size,
        }
    )


@router.get("/{exam_id}")
def get_exam(exam_id: int) -> Dict[str, Any]:
    """试卷详情：exam 元信息 + 全部题目快照。

    答案放开规则：已交卷 → 全部放开；作答中 → 仅已作答的题放开
    （练习模式前端逐题提交作答，天然逐题拿到判分结果）。
    """
    with db.get_conn() as conn:
        exam_row = conn.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
        if exam_row is None:
            raise ApiError(f"试卷 {exam_id} 不存在", CODE_NOT_FOUND, 404)
        answer_rows = conn.execute(
            "SELECT * FROM exam_answers WHERE exam_id = ? ORDER BY seq ASC",
            (exam_id,),
        ).fetchall()
        finished = exam_row["status"] == "finished"
        practice = exam_row["mode"] == "practice"
    exam = exam_to_dict(exam_row)
    # 答案放开规则（防泄题）：
    # * 已交卷 → 全部放开；
    # * 练习模式进行中 → 仅已作答的题放开（前端提交作答后立刻回看判分）；
    # * 考试模式进行中 → 一律不放开（哪怕已作答，也要等交卷统一判分）。
    items = [
        exam_answer_to_dict(
            row,
            reveal=finished or (practice and row["answered_at"] is not None),
        )
        for row in answer_rows
    ]
    return ok({"exam": exam, "items": items})


@router.post("/{exam_id}/answers")
def submit_answer(exam_id: int, payload: ExamAnswerRequest) -> Dict[str, Any]:
    """提交单题作答。

    * 练习模式：立即判分，返回 ``is_correct`` + 正确答案 + 解析。
    * 考试模式：只记录作答（``is_correct`` 置 NULL，交卷时统一判分），
      响应不含答案（防泄题）。
    * 已交卷的试卷不允许再作答。
    """
    with db.get_conn() as conn:
        exam_row = conn.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
        if exam_row is None:
            raise ApiError(f"试卷 {exam_id} 不存在", CODE_NOT_FOUND, 404)
        if exam_row["status"] != "in_progress":
            raise ApiError("该试卷已交卷，不能再作答", CODE_PARAM_ERROR, 400)
        item = conn.execute(
            "SELECT * FROM exam_answers WHERE exam_id = ? AND seq = ?",
            (exam_id, int(payload.seq)),
        ).fetchone()
        if item is None:
            raise ApiError(
                f"试卷 {exam_id} 不存在第 {payload.seq} 题", CODE_NOT_FOUND, 404
            )
        qtype = item["qtype"]
        answer_given = normalize_answer(payload.answer or "", qtype)
        is_correct: Optional[int] = None
        if exam_row["mode"] == "practice":
            is_correct = 1 if _judge(answer_given, item["answer"] or "", qtype) else 0
        conn.execute(
            """
            UPDATE exam_answers
               SET answer_given = ?, is_correct = ?,
                   answered_at  = datetime('now','localtime')
             WHERE id = ?
            """,
            (answer_given, is_correct, int(item["id"])),
        )
        # 已作答数实时聚合（幂等：重复提交同一题不重复计数）
        stats = conn.execute(
            """
            SELECT COUNT(*) AS answered, SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) AS correct
            FROM exam_answers WHERE exam_id = ? AND answered_at IS NOT NULL
            """,
            (exam_id,),
        ).fetchone()
        conn.execute(
            "UPDATE exams SET answered_count = ?, correct_count = COALESCE(?, correct_count) WHERE id = ?",
            (int(stats["answered"]), stats["correct"], exam_id),
        )
        updated = conn.execute(
            "SELECT * FROM exam_answers WHERE id = ?", (int(item["id"]),)
        ).fetchone()

    reveal = exam_row["mode"] == "practice"
    data = exam_answer_to_dict(updated, reveal=reveal)
    return ok(data)


@router.post("/{exam_id}/finish")
def finish_exam(exam_id: int) -> Dict[str, Any]:
    """交卷：未作答按错计，统一判分并算得分（百分制，保留 1 位小数）。"""
    with db.get_conn() as conn:
        exam_row = conn.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
        if exam_row is None:
            raise ApiError(f"试卷 {exam_id} 不存在", CODE_NOT_FOUND, 404)
        if exam_row["status"] == "finished":
            raise ApiError("该试卷已交卷", CODE_PARAM_ERROR, 400)
        items = conn.execute(
            "SELECT * FROM exam_answers WHERE exam_id = ? ORDER BY seq ASC",
            (exam_id,),
        ).fetchall()
        total = len(items)
        correct = 0
        answered = 0
        for item in items:
            if item["answered_at"] is None:
                # 未作答：判为错误（考试语义）；练习模式同样按错计入成绩
                conn.execute(
                    "UPDATE exam_answers SET is_correct = 0 WHERE id = ?",
                    (int(item["id"]),),
                )
                continue
            answered += 1
            if exam_row["mode"] == "exam":
                # 考试模式交卷时才判分（作答期间不判，防前端轮询试探）
                correct_flag = _judge(
                    item["answer_given"] or "", item["answer"] or "", item["qtype"]
                )
            else:
                correct_flag = int(item["is_correct"] or 0) == 1
            conn.execute(
                "UPDATE exam_answers SET is_correct = ? WHERE id = ?",
                (1 if correct_flag else 0, int(item["id"])),
            )
            if correct_flag:
                correct += 1
        score = round(correct * 100.0 / total, 1) if total else 0.0
        conn.execute(
            """
            UPDATE exams
               SET status = 'finished', score = ?, correct_count = ?, answered_count = ?,
                   finished_at = datetime('now','localtime')
             WHERE id = ?
            """,
            (score, correct, answered, exam_id),
        )
        updated = conn.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
    return ok(exam_to_dict(updated))


@router.delete("/{exam_id}")
def delete_exam(exam_id: int) -> Dict[str, Any]:
    """删除一场练习/考试记录（作答明细级联删除）。"""
    with db.get_conn() as conn:
        exam_row = conn.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
        if exam_row is None:
            raise ApiError(f"试卷 {exam_id} 不存在", CODE_NOT_FOUND, 404)
        conn.execute("DELETE FROM exams WHERE id = ?", (exam_id,))
    return ok({"id": int(exam_id), "deleted": True})
