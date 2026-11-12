"""FastAPI 应用入口：初始化数据库、挂载 /api 路由、托管前端构建产物。

启动：``uvicorn app.main:app --host 0.0.0.0 --port 8000``（工作目录 backend/）
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db
from .api import install_exception_handlers, router as api_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("private_tiku.main")

APP_TITLE = "私有护理题库系统"
APP_VERSION = "1.0.0"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """启动时初始化数据库与目录，关闭时打印提示。"""
    config.ensure_dirs()
    db.init_db()
    logger.info("数据目录：%s", config.DATA_DIR)
    logger.info("数据库：%s", config.DB_PATH)
    logger.info("FTS trigram 分词器：%s", "可用" if db.FTS_TRIGRAM else "不可用（降级 LIKE）")
    logger.info("前端产物：%s（%s）", config.FRONTEND_DIST, "已找到" if config.FRONTEND_DIST.is_dir() else "未构建")
    yield
    logger.info("应用已停止")


app = FastAPI(
    title=APP_TITLE,
    version=APP_VERSION,
    description="DOCX/TXT/PDF 批量导入 → 规则解析 → 异常标记 → FTS5 检索 → 导出",
    lifespan=lifespan,
)

install_exception_handlers(app)
app.include_router(api_router, prefix="/api")


# ---------------------------------------------------------------------------
# 前端静态产物托管（SPA 回退）
# ---------------------------------------------------------------------------
def _mount_frontend(application: FastAPI) -> None:
    """挂载 frontend/dist；未构建时给 / 返回可读的提示。"""
    dist = config.FRONTEND_DIST
    index_file = dist / "index.html"

    if (dist / "assets").is_dir():
        application.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @application.get("/{full_path:path}", include_in_schema=False, response_model=None)
    def spa_fallback(full_path: str):
        """静态文件优先，其余路径回退到 index.html（前端路由）。"""
        if full_path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content={"code": 40401, "message": f"接口不存在：/api/{full_path}", "data": None},
            )
        if full_path:
            candidate = dist / full_path
            if candidate.is_file():
                return FileResponse(candidate)
        if index_file.is_file():
            return FileResponse(index_file)
        return JSONResponse(
            status_code=200,
            content={
                "code": 0,
                "message": "前端未构建：请执行 cd frontend && npm install && npm run build",
                "data": {"api_docs": "/docs", "health": "/api/health"},
            },
        )


_mount_frontend(app)


if __name__ == "__main__":  # pragma: no cover - 手工调试入口
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=config.APP_PORT, reload=False)
