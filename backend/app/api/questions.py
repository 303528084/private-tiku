"""题目 API：详情 / 编辑（重算异常）/ 删除。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from .. import db
from ..models import CODE_NOT_FOUND, ApiError, QuestionUpdateRequest, ok
from ..parser.extractor import make_parsed
from ..parser.validator import validate

router = APIRouter(prefix="/questions", tags=["questions"])


def _load_question(conn: Any, question_id: int) -> Dict[str, Any]:
    """读取题目，不存在则抛 404。"""
    question = db.fetch_question(conn, int(question_id))
    if question is None:
        raise ApiError(f"题目 {question_id} 不存在", CODE_NOT_FOUND, 404)
    return question


@router.get("/{question_id}")
def get_question(question_id: int) -> Dict[str, Any]:
    """题目详情（含异常列表）。"""
    with db.get_conn() as conn:
        question = _load_question(conn, question_id)
    anomalies = question.pop("anomalies", [])
    question["anomalies"] = anomalies
    return ok({"question": question, "anomalies": anomalies})


@router.put("/{question_id}")
def update_question(question_id: int, payload: QuestionUpdateRequest) -> Dict[str, Any]:
    """编辑题目并按最新内容全量重建异常（架构 §8 异常翻新）。"""
    with db.get_conn() as conn:
        existing = _load_question(conn, question_id)
        qtype = payload.qtype.value if payload.qtype is not None else None
        parsed = make_parsed(
            no=existing["question_no"],
            stem=payload.stem,
            options=payload.options,
            answer=payload.answer,
            analysis=payload.analysis,
            qtype=qtype,
        )
        if not parsed.stem:
            raise ApiError("题干不能为空", 40001, 400)

        conn.execute(
            """
            UPDATE questions
            SET stem = ?, options_json = ?, answer = ?, analysis = ?, qtype = ?,
                updated_at = datetime('now','localtime')
            WHERE id = ?
            """,
            (
                parsed.stem,
                db.dump_options(parsed.options),
                parsed.answer,
                parsed.analysis,
                parsed.qtype,
                int(question_id),
            ),
        )

        # 全量重建异常，避免脏数据残留
        conn.execute("DELETE FROM anomalies WHERE question_id = ?", (int(question_id),))
        same_number_count = db.count_same_number(
            conn, int(existing["import_file_id"]), parsed.no
        )
        counter = {parsed.no: max(same_number_count, 1)}
        anomalies = validate(parsed, counter)
        for anomaly in anomalies:
            conn.execute(
                "INSERT INTO anomalies (question_id, type, detail, handled) VALUES (?, ?, ?, 0)",
                (int(question_id), anomaly.type, anomaly.detail),
            )

        question = await_fresh(conn, int(question_id))
    return ok({"question": question, "anomalies": question["anomalies"]})


def await_fresh(conn: Any, question_id: int) -> Dict[str, Any]:
    """重新读取题目（同步函数，命名沿用「取最新」语义）。"""
    question = db.fetch_question(conn, question_id)
    if question is None:  # pragma: no cover - 理论上不会发生
        raise ApiError(f"题目 {question_id} 不存在", CODE_NOT_FOUND, 404)
    return question


@router.delete("/{question_id}")
def delete_question(question_id: int) -> Dict[str, Any]:
    """删除题目（AFTER DELETE 触发器同步清理 FTS 索引）。"""
    with db.get_conn() as conn:
        _load_question(conn, question_id)
        conn.execute("DELETE FROM questions WHERE id = ?", (int(question_id),))
    return ok({"deleted": 1})
