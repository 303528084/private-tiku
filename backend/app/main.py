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

from . import auth, config, db, migrate
from .api import install_exception_handlers, router as api_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("private_tiku.main")

APP_TITLE = "私有护理题库系统"
APP_VERSION = config.APP_VERSION
"""版本号唯一来源是 ``config.APP_VERSION``（分享包 manifest 也用同一份）。"""



@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """启动时初始化数据库与目录，关闭时打印提示。"""
    config.ensure_dirs()
    db.init_db()
    logger.info("数据目录：%s", config.DATA_DIR)
    logger.info("数据库：%s", config.DB_PATH)
    logger.info(
        "schema_version = %d（代码期望 v%d）",
        migrate.read_version(),
        migrate.SCHEMA_VERSION,
    )
    logger.info("FTS trigram 分词器：%s", "可用" if db.FTS_TRIGRAM else "不可用（降级 LIKE）")
    logger.info(
        "访问门禁：%s",
        "已启用（未登录将返回 401）" if auth.gate_enabled() else "未启用（TIKU_ACCESS_TOKEN 为空）",
    )
    logger.info("前端产物：%s（%s）", config.FRONTEND_DIST, "已找到" if config.FRONTEND_DIST.is_dir() else "未构建")
    yield
    logger.info("应用已停止")


app = FastAPI(
    title=APP_TITLE,
    version=APP_VERSION,
    description="DOCX/TXT/PDF 批量导入 → 规则解析 → 异常标记 → FTS5 检索 → 导出",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# 中间件注册顺序（v1.4 T01，最容易踩的坑）
# ---------------------------------------------------------------------------
# 1) add_middleware 只能在 app 启动前调用，因此紧跟 app 创建之后。
# 2) Starlette 的中间件栈固定为：
#      ServerErrorMiddleware → [user middleware…] → ExceptionMiddleware → Router
#    也就是说「用户中间件」永远在 ExceptionMiddleware 的**外层**，与它跟
#    install_exception_handlers() 的调用先后无关。
# 3) 因此 AccessGateMiddleware 的 401 **不会**流经 ExceptionMiddleware，也就不会
#    被 install_exception_handlers 里的 StarletteHTTPException 处理器改写成
#    {"detail": ...}。为了彻底不依赖这个细节，中间件内部直接构造
#    JSONResponse({"code": 40100, "message": ..., "data": None})，见 app/auth.py。
app.add_middleware(auth.AccessGateMiddleware)

install_exception_handlers(app)
app.include_router(api_router, prefix="/api")


# ---------------------------------------------------------------------------
# 前端静态产物托管（SPA 回退）
# ---------------------------------------------------------------------------
def _mount_frontend(application: FastAPI) -> None:
    """挂载 frontend/dist；未构建时给 / 返回可读的提示。

    **路径穿越防护（v1.4 P0-1）**：``/{full_path:path}`` 拿到的 ``full_path`` 是原始
    请求路径，``dist / full_path`` 在 ``full_path`` 含 ``..`` 时会解析到 dist 之外
    （``/../../data/tiku.db`` → 数据库文件，``/../../backend/app/config.py`` → 源码），
    而门禁 ``auth.is_public_path()`` 对非 ``/api/*`` 一律放行，因此这里**必须自己做
    边界校验**，否则等于未认证任意文件读取。

    规则：``(dist / full_path).resolve()`` 必须仍在 ``dist.resolve()`` 内，否则 404。
    * 用 ``resolve()``（非 strict）而不是字符串判断，才能同时覆盖 ``..``、``.``、
      Windows 反斜杠、符号链接父目录、盘符绝对路径；
    * 越界返回 **404 而不是 SPA 壳**：回退 index.html 虽然不泄数据，但会让攻击探测
      「看起来成功」，也让回归测试无法断言；
    * ``resolved_dist`` 提到闭包外只算一次，避免每请求 syscall。
    """
    dist = config.FRONTEND_DIST
    index_file = dist / "index.html"
    resolved_dist = dist.resolve()

    if (dist / "assets").is_dir():
        application.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    def _not_found(message: str = "接口不存在") -> JSONResponse:
        """统一的 404 响应（与 ``install_exception_handlers`` 同结构）。"""
        return JSONResponse(
            status_code=404, content={"code": 40401, "message": message, "data": None}
        )

    @application.get("/{full_path:path}", include_in_schema=False, response_model=None)
    def spa_fallback(full_path: str):
        """静态文件优先，其余路径回退到 index.html（前端路由）。"""
        # 用「归一化后的第一段」判 /api 命名空间，而不是裸 startswith("api/")：
        # ``/API/nope``、``/./api/nope``、``/api\\nope`` 都要落到同一个 404，
        # 与门禁 auth.is_public_path() 的分类口径保持一致。
        segments = [
            segment
            for segment in full_path.replace("\\", "/").split("/")
            if segment not in ("", ".")
        ]
        if segments and segments[0].lower() == "api":
            # full_path 已含 "api/" 前缀，这里不能再拼一次（v1.4 修复：原先会输出
            # 「接口不存在：/api/api/nope」）。
            return _not_found(f"接口不存在：/{full_path}")
        if full_path:
            # 反斜杠在 Windows 上也是分隔符（``..\..\data\tiku.db``）：先归一成 ``/``
            # 再解析，保证各平台判定一致。
            relative = full_path.replace("\\", "/")
            try:
                candidate = (dist / relative).resolve()
            except (OSError, ValueError):  # 非法路径（NUL 字节 / 超长路径等）
                logger.warning("SPA 回退收到非法路径：%r", full_path)
                return _not_found()
            if candidate != resolved_dist and resolved_dist not in candidate.parents:
                logger.warning(
                    "SPA 回退拒绝越界路径：%r（解析为 %s，dist 为 %s）",
                    full_path,
                    candidate,
                    resolved_dist,
                )
                return _not_found()
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
