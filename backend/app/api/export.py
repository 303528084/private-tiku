"""导出 API：JSON / Markdown 文件流。"""

from __future__ import annotations

import io
import json
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from .. import db
from ..models import CODE_PARAM_ERROR, ApiError, ok

router = APIRouter(prefix="/export", tags=["export"])

QTYPE_LABEL: Dict[str, str] = {
    "single": "单选题",
    "multiple": "多选题",
    "judge": "判断题",
    "unknown": "未知题型",
}
ANOMALY_LABEL: Dict[str, str] = {
    "missing_answer": "缺答案",
    "missing_options": "缺选项",
    "too_few_options": "选项不足",
    "empty_stem": "无题干",
    "duplicate_number": "题号重复",
    "parse_error": "解析异常",
}


def _fetch_questions() -> List[Dict[str, Any]]:
    """取全部已入库题目（含异常与来源文件名）。"""
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT q.*, f.filename AS filename
            FROM questions q JOIN import_files f ON f.id = q.import_file_id
            WHERE q.status = 'published'
            ORDER BY q.import_file_id ASC, q.id ASC
            """
        ).fetchall()
        grouped = db.fetch_anomalies(conn, [int(row["id"]) for row in rows])
    return [
        db.question_to_dict(row, grouped.get(int(row["id"]), []), filename=dict(row).get("filename"))
        for row in rows
    ]


def _render_markdown(questions: List[Dict[str, Any]]) -> str:
    """渲染为结构化 Markdown。"""
    lines: List[str] = ["# 私有护理题库导出", ""]
    lines.append(f"- 导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 题目总数：{len(questions)}")
    lines.append("")
    for question in questions:
        title = f"## {question['question_no']}. [{QTYPE_LABEL.get(question['qtype'], question['qtype'])}]"
        lines.append(title)
        lines.append("")
        lines.append(question["stem"] or "（题干为空）")
        lines.append("")
        for letter, value in question["options"].items():
            lines.append(f"- **{letter}.** {value}")
        if question["options"]:
            lines.append("")
        answer = question["answer"] or "（无答案）"
        lines.append(f"**答案**：{answer}")
        if question["analysis"]:
            lines.append("")
            lines.append(f"**解析**：{question['analysis']}")
        if question["anomalies"]:
            labels = "、".join(
                ANOMALY_LABEL.get(item["type"], item["type"]) for item in question["anomalies"]
            )
            lines.append("")
            lines.append(f"**异常标记**：{labels}")
        if question.get("filename"):
            lines.append("")
            lines.append(f"**来源**：{question['filename']}")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


@router.get("")
def export_questions(
    format: str = Query(default="json", pattern="^(json|md)$", description="json|md")
) -> Any:
    """导出全部已入库题目。JSON 走统一响应外壳，Markdown 直接返回文件流。"""
    questions = _fetch_questions()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    if format == "md":
        content = _render_markdown(questions)
        buffer = io.BytesIO(content.encode("utf-8"))
        filename = f"tiku-{stamp}.md"
        return StreamingResponse(
            buffer,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Total-Count": str(len(questions)),
            },
        )

    if format != "json":  # pragma: no cover - Query pattern 已拦截
        raise ApiError("format 仅支持 json 或 md", CODE_PARAM_ERROR, 400)

    payload = json.dumps(
        {"total": len(questions), "items": questions}, ensure_ascii=False, indent=2
    )
    buffer = io.BytesIO(payload.encode("utf-8"))
    filename = f"tiku-{stamp}.json"
    return StreamingResponse(
        buffer,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Total-Count": str(len(questions)),
        },
    )


@router.get("/download")
def export_download(format: str = Query(default="md", pattern="^(json|md)$")) -> Dict[str, Any]:
    """辅助端点：返回导出文件的可访问 URL（供前端 window.open 使用）。"""
    return ok({"url": f"/api/export?format={format}"})
