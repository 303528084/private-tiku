"""API 路由聚合与统一异常处理。

所有子路由前缀均为 ``/api``（在 main.py 中统一挂载），
响应体统一为 ``{code, message, data}``。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import db
from ..models import (
    CODE_INTERNAL_ERROR,
    CODE_NOT_FOUND,
    CODE_PARAM_ERROR,
    ApiError,
    fail,
    ok,
)

from . import anomalies, export, imports, libraries, questions, search

logger = logging.getLogger("private_tiku.api")

router = APIRouter()
router.include_router(imports.router)
router.include_router(questions.router)
router.include_router(search.router)
router.include_router(anomalies.router)
router.include_router(libraries.router)
router.include_router(export.router)


# ---------------------------------------------------------------------------
# 健康检查 / 统计面板（架构 §4，无独立模块）
# ---------------------------------------------------------------------------
@router.get("/health", tags=["system"])
def health() -> dict[str, Any]:
    """健康检查。"""
    return ok({"status": "ok", "fts_trigram": db.FTS_TRIGRAM})


@router.get("/stats", tags=["system"])
def stats() -> dict[str, Any]:
    """统计面板：题库总量 / 待入库 / 未处理异常 / 导入批次。"""
    with db.get_conn() as conn:
        row = conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM questions WHERE status = 'published') AS total_published,
              (SELECT COUNT(*) FROM questions WHERE status = 'pending')   AS total_pending,
              (SELECT COUNT(*) FROM anomalies WHERE handled = 0)          AS anomaly_open,
              (SELECT COUNT(*) FROM imports)                              AS total_imports,
              (SELECT COUNT(*) FROM import_files)                         AS total_files,
              (SELECT COUNT(*) FROM questions)                            AS total_questions
            """
        ).fetchone()
    return ok(
        {
            "total_published": int(row["total_published"]),
            "total_pending": int(row["total_pending"]),
            "anomaly_open": int(row["anomaly_open"]),
            "total_imports": int(row["total_imports"]),
            "total_files": int(row["total_files"]),
            "total_questions": int(row["total_questions"]),
        }
    )


def install_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器，保证任何错误都返回统一响应结构。"""

    @app.exception_handler(ApiError)
    async def _handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
        """业务异常。"""
        return JSONResponse(
            status_code=exc.http_status,
            content=fail(exc.message, exc.code, exc.data),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """参数校验失败 → 40001。"""
        errors: list[dict[str, Any]] = []
        for item in exc.errors():
            errors.append(
                {
                    "loc": [str(part) for part in item.get("loc", ())],
                    "msg": item.get("msg", ""),
                    "type": item.get("type", ""),
                }
            )
        message = "参数校验失败"
        if errors:
            first = errors[0]
            message = f"参数校验失败：{'.'.join(first['loc'])} {first['msg']}".strip()
        return JSONResponse(
            status_code=400, content=fail(message, CODE_PARAM_ERROR, {"errors": errors})
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_error(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """HTTP 状态码异常（404 等）→ 统一结构。"""
        if exc.status_code == 404:
            code = CODE_NOT_FOUND
        elif exc.status_code == 405:
            code = CODE_PARAM_ERROR
        elif exc.status_code >= 500:
            code = CODE_INTERNAL_ERROR
        else:
            code = CODE_PARAM_ERROR
        message = exc.detail if isinstance(exc.detail, str) else "请求失败"
        return JSONResponse(status_code=exc.status_code, content=fail(message, code))

    @app.exception_handler(Exception)
    async def _handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        """兜底异常 → 50000。"""
        logger.exception("未捕获异常: %s", exc)
        return JSONResponse(
            status_code=500, content=fail(f"服务器内部错误：{exc}", CODE_INTERNAL_ERROR)
        )


__all__ = ["install_exception_handlers", "router"]
