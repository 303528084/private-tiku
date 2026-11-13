"""导出 API：JSON / Markdown 文件流 + 分享包（ZIP，v1.4 T02）。

三条路由：

* ``GET /api/export?format=json|md`` —— v1.0 起的导出，**返回结构不可变**（有测试覆盖）；
* ``GET /api/export/download`` —— 辅助端点，返回上面那条的 URL；
* ``GET /api/export/share-pack`` —— v1.4 新增，导出 ZIP 分享包（manifest + bank.md）。

分享包的正文渲染与 manifest 构造在 :mod:`app.share` 里实现，本模块只负责
「查库 → 打包 → 设响应头」。
"""

from __future__ import annotations

import io
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Response
from fastapi.responses import StreamingResponse

from .. import db
from ..models import CODE_PARAM_ERROR, QTYPE_VALUES, ApiError, ok
from ..share.markdown_render import ANOMALY_LABEL, QTYPE_LABEL, render_markdown
from ..share.pack import build_share_pack

router = APIRouter(prefix="/export", tags=["export"])

DEFAULT_BANK_NAME: str = "私有护理题库"
"""无法从来源推导题库名时使用的缺省名（进 manifest 的 ``bank_name``）。"""

__all__ = ["ANOMALY_LABEL", "QTYPE_LABEL", "router"]


def _fetch_questions(
    qtype: Optional[str] = None,
    only_anomaly: bool = False,
    doc_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """取已入库题目（含异常与来源文件名），支持与 ``/api/search`` 同口径的筛选。

    筛选语义与 :func:`app.api.search._build_where` 保持一致（同一套 SQL 片段
    写法、同一条错误文案），但这里是**无关键词**的全量导出场景，因此不涉及
    FTS / LIKE 选路。

    Args:
        qtype: 题型筛选（``single/multiple/judge/unknown``）。
        only_anomaly: 只看有**未处理**异常的题。
        doc_id: 按来源文件（``import_file_id``）筛选。

    Returns:
        题目 dict 列表，按 ``(import_file_id, id)`` 升序。

    Raises:
        ApiError: 题型筛选值非法（40001 / 400）。
    """
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

    sql = f"""
        SELECT q.*, f.filename AS filename
        FROM questions q JOIN import_files f ON f.id = q.import_file_id
        WHERE {' AND '.join(clauses)}
        ORDER BY q.import_file_id ASC, q.id ASC
    """
    with db.get_conn() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
        grouped = db.fetch_anomalies(conn, [int(row["id"]) for row in rows])
    return [
        db.question_to_dict(row, grouped.get(int(row["id"]), []), filename=dict(row).get("filename"))
        for row in rows
    ]


def _render_markdown(questions: List[Dict[str, Any]]) -> str:
    """渲染为结构化 Markdown（实现见 :func:`app.share.markdown_render.render_markdown`）。

    保留这个名字是因为 ``tests/test_markdown.py`` 直接 import 它做往返测试。
    """
    return render_markdown(questions)


def _bank_name(questions: List[Dict[str, Any]]) -> str:
    """推导分享包的题库名：单一来源文件 → 用其文件名（去扩展名），否则用缺省名。"""
    names = {str(item.get("filename") or "").strip() for item in questions}
    names.discard("")
    if len(names) == 1:
        name = names.pop()
        stem = name.rsplit(".", 1)[0] if "." in name else name
        if stem.strip():
            return stem.strip()
    return DEFAULT_BANK_NAME


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


@router.get("/share-pack")
def export_share_pack(
    type: Optional[str] = Query(default=None, description="题型筛选（同 /api/search）"),
    only_anomaly: bool = Query(default=False, description="仅看有未处理异常的题"),
    doc_id: Optional[int] = Query(default=None, description="按来源文件过滤"),
) -> Response:
    """导出 ZIP 分享包（``manifest.json`` + ``bank.md`` + ``media/``）。

    文件名固定为 URL 安全的 ``tiku-share-{时间戳}.zip``；中文题库名只出现在
    manifest 里（``Content-Disposition`` 带中文在部分浏览器/代理上会乱码）。

    Args:
        type: 题型筛选（与 ``/api/search`` 同名参数）。
        only_anomaly: 仅导出有未处理异常的题。
        doc_id: 按来源文件筛选。

    Returns:
        ``application/zip`` 响应。

    Raises:
        ApiError: 空结果集 → 400 / 40001。
    """
    questions = _fetch_questions(qtype=type, only_anomaly=only_anomaly, doc_id=doc_id)
    if not questions:
        raise ApiError("没有可导出的题目", CODE_PARAM_ERROR, 400)

    payload = build_share_pack(questions, _bank_name(questions))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="tiku-share-{stamp}.zip"',
            "X-Total-Count": str(len(questions)),
        },
    )


@router.get("/download")
def export_download(format: str = Query(default="md", pattern="^(json|md)$")) -> Dict[str, Any]:
    """辅助端点：返回导出文件的可访问 URL（供前端 window.open 使用）。"""
    return ok({"url": f"/api/export?format={format}"})
