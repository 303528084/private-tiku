"""异常校对 API：异常列表（含题目快照）与标记已处理。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from .. import config, db
from ..models import (
    ANOMALY_TYPE_VALUES,
    CODE_NOT_FOUND,
    CODE_PARAM_ERROR,
    AnomalyPatchRequest,
    ApiError,
    ok,
)

router = APIRouter(prefix="/anomalies", tags=["anomalies"])


def _anomaly_dict_from_row(row: Any) -> Dict[str, Any]:
    """从联表查询行构造 anomaly dict（列已加别名）。"""
    data = dict(row)
    return {
        "id": int(data["anomaly_id"]),
        "question_id": int(data["a_question_id"]),
        "type": data["anomaly_type"],
        "detail": data["anomaly_detail"] or "",
        "handled": int(data["anomaly_handled"] or 0),
        "created_at": data.get("anomaly_created_at"),
    }


@router.get("")
def list_anomalies(
    type: Optional[str] = Query(default=None, description="异常类型枚举"),
    handled: Optional[int] = Query(default=None, ge=0, le=1, description="0 未处理 / 1 已处理"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=config.DEFAULT_PAGE_SIZE, ge=1, le=config.MAX_PAGE_SIZE),
) -> Dict[str, Any]:
    """异常列表，附带题目信息便于直接校对。"""
    clauses: List[str] = ["1 = 1"]
    params: List[Any] = []
    if type:
        if type not in ANOMALY_TYPE_VALUES:
            raise ApiError(f"非法的异常类型：{type}", CODE_PARAM_ERROR, 400)
        clauses.append("a.type = ?")
        params.append(type)
    if handled is not None:
        clauses.append("a.handled = ?")
        params.append(int(handled))
    where_sql = " AND ".join(clauses)
    offset = (page - 1) * size

    sql = f"""
        SELECT a.id AS anomaly_id, a.question_id AS a_question_id, a.type AS anomaly_type,
               a.detail AS anomaly_detail, a.handled AS anomaly_handled,
               a.created_at AS anomaly_created_at, q.*, f.filename AS filename
        FROM anomalies a
        JOIN questions q ON q.id = a.question_id
        JOIN import_files f ON f.id = q.import_file_id
        WHERE {where_sql}
        ORDER BY a.handled ASC, a.id DESC
        LIMIT ? OFFSET ?
    """

    with db.get_conn() as conn:
        total_row = conn.execute(
            f"SELECT COUNT(*) AS c FROM anomalies a WHERE {where_sql}", params
        ).fetchone()
        rows = conn.execute(sql, [*params, size, offset]).fetchall()
        question_ids = sorted({int(row["a_question_id"]) for row in rows})
        grouped = db.fetch_anomalies(conn, question_ids)
        summary_row = conn.execute(
            """
            SELECT SUM(CASE WHEN handled = 0 THEN 1 ELSE 0 END) AS open_count,
                   SUM(CASE WHEN handled = 1 THEN 1 ELSE 0 END) AS done_count
            FROM anomalies
            """
        ).fetchone()

    items: List[Dict[str, Any]] = []
    for row in rows:
        question = db.question_to_dict(
            row,
            grouped.get(int(row["a_question_id"]), []),
            filename=dict(row).get("filename"),
        )
        items.append({"anomaly": _anomaly_dict_from_row(row), "question": question})

    return ok(
        {
            "items": items,
            "total": int(total_row["c"]),
            "summary": {
                "unhandled": int(summary_row["open_count"] or 0),
                "handled": int(summary_row["done_count"] or 0),
            },
            "page": page,
            "size": size,
        }
    )


@router.patch("/{anomaly_id}")
def patch_anomaly(anomaly_id: int, payload: AnomalyPatchRequest) -> Dict[str, Any]:
    """标记异常为已处理/未处理。"""
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM anomalies WHERE id = ?", (int(anomaly_id),)).fetchone()
        if row is None:
            raise ApiError(f"异常记录 {anomaly_id} 不存在", CODE_NOT_FOUND, 404)
        conn.execute(
            "UPDATE anomalies SET handled = ? WHERE id = ?",
            (1 if payload.handled else 0, int(anomaly_id)),
        )
        updated = conn.execute(
            "SELECT * FROM anomalies WHERE id = ?", (int(anomaly_id),)
        ).fetchone()
    return ok({"anomaly": db.anomaly_to_dict(updated)})
