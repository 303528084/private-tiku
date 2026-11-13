"""访问门禁：会话 Cookie 的 HMAC 派生、口令比对与 ASGI 中间件。

设计要点（架构 §9.1 T01）：

* **未设 ``TIKU_ACCESS_TOKEN`` = 关闭门禁**：中间件第一个判断就是「口令为空则
  直接放行」，因此不启用时与 v1.3 行为**逐字一致**，既有 192 项测试零改动。
* **每次请求都读环境变量**（:func:`config.get_access_token` 不做缓存）：
  ``monkeypatch.setenv`` 切换口令能即时生效，容器里改 ``.env`` 重启后旧 Cookie
  立刻失效。
* **Cookie 里不存口令**：只存 ``HMAC-SHA256(token, SALT)``。换口令 → HMAC 变 →
  旧 Cookie 自动失效，无需维护会话表。
* **常量时间比对**：用 :func:`hmac.compare_digest` 比对字节串，避免逐字符提前返回
  造成的时序侧信道（口令是中文时 ``str`` 版 compare_digest 会抛 TypeError，故先
  ``encode()``）。
* **中间件是纯 ASGI**（不用 ``BaseHTTPMiddleware``）：
  - 不缓冲响应体，``FileResponse``（前端静态产物）照常流式输出；
  - 401 响应由中间件**自己构造 JSONResponse**，不经过 FastAPI 的异常处理器，
    因此不可能被改写成 ``{"detail": ...}``，也不会被二次包装。

放行规则（**顺序重要**，见 :func:`is_public_path`）：

============================  ==========================================
路径                          行为
============================  ==========================================
``/api/health``               放行（compose healthcheck 依赖）
``/api/auth/login``           放行（否则无法登录）
``/api/auth/status``          放行（前端启动时据此决定是否跳登录页）
其它 ``/api/*``                需要有效 Cookie，否则 401
``/docs`` ``/openapi.json``  401（不对外暴露接口文档）
``/redoc``
其余（``/``、``/assets/*``、  放行（静态资源与 SPA 路由由前端自己处理）
``/login``、``/sw.js`` 等）
============================  ==========================================
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from . import config

logger = logging.getLogger("private_tiku.auth")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
SESSION_SALT: bytes = b"private-tiku-session"
"""会话 HMAC 的固定盐（改它会一次性作废所有在线会话）。"""

CODE_UNAUTHORIZED: int = 40100
"""业务码：未登录 / 会话失效（中间件 401）。"""

CODE_BAD_TOKEN: int = 40101
"""业务码：口令错误（``POST /api/auth/login`` 401）。"""

UNAUTHORIZED_MESSAGE: str = "未登录或会话已失效"
BAD_TOKEN_MESSAGE: str = "口令错误"

PUBLIC_API_PATHS: frozenset[str] = frozenset(
    {"/api/health", "/api/auth/login", "/api/auth/status"}
)
"""无需 Cookie 即可访问的 ``/api`` 路径（精确匹配）。"""

PROTECTED_DOC_PATHS: Sequence[str] = ("/docs", "/redoc", "/openapi.json")
"""门禁开启时需要 401 的接口文档路径（前缀匹配）。"""


# ---------------------------------------------------------------------------
# 口令 / 会话
# ---------------------------------------------------------------------------
def gate_enabled() -> bool:
    """门禁是否启用（即 ``TIKU_ACCESS_TOKEN`` 是否非空）。每次调用都读环境变量。"""
    return bool(config.get_access_token())


def session_value() -> str:
    """由当前口令派生会话值：``HMAC-SHA256(token, SALT)`` 的十六进制串。

    Returns:
        64 位十六进制字符串；门禁未启用时返回空串。
    """
    token = config.get_access_token()
    if not token:
        return ""
    return hmac.new(token.encode("utf-8"), SESSION_SALT, hashlib.sha256).hexdigest()


def verify_token(candidate: str) -> bool:
    """常量时间比对用户提交的口令与当前配置口令。

    Args:
        candidate: 用户提交的口令（可能为空串）。

    Returns:
        ``True`` 表示口令正确。门禁未启用时返回 ``False``——调用方应先判
        :func:`gate_enabled`（未启用时无需校验，直接放行）。
    """
    expected = config.get_access_token()
    if not expected:
        return False
    return hmac.compare_digest(
        (candidate or "").encode("utf-8"), expected.encode("utf-8")
    )


def session_valid(cookie_value: Optional[str]) -> bool:
    """校验会话 Cookie 是否与当前口令匹配（常量时间）。"""
    expected = session_value()
    if not expected:
        return False
    if not cookie_value:
        return False
    return hmac.compare_digest(cookie_value.encode("utf-8"), expected.encode("utf-8"))


# ---------------------------------------------------------------------------
# Cookie 解析（不依赖第三方库）
# ---------------------------------------------------------------------------
def parse_cookies(headers: Iterable[Tuple[bytes, bytes]]) -> Dict[str, str]:
    """从 ASGI 请求头里解析 Cookie（只取第一个 ``cookie`` 头，够用且无歧义）。"""
    for name, value in headers:
        if name.lower() != b"cookie":
            continue
        raw = value.decode("latin-1")
        cookies: Dict[str, str] = {}
        for chunk in raw.split(";"):
            key, _, val = chunk.partition("=")
            key = key.strip()
            if not key:
                continue
            cookies.setdefault(key, val.strip().strip('"'))
        return cookies
    return {}


# ---------------------------------------------------------------------------
# 路径分类
# ---------------------------------------------------------------------------
def _normalize(path: str) -> str:
    """归一化路径，使「看起来不同、路由到同一处」的写法得到同一分类。

    处理四件事：

    1. 反斜杠 → 斜杠（``/api\\search`` ≡ ``/api/search``，Windows 上二者等价）；
    2. 折叠重复斜杠（``/api//search`` → ``/api/search``）；
    3. 丢弃空段与 ``.`` 段（``/./api/search`` → ``/api/search``，``/api/search/``
       → ``/api/search``）；
    4. 结果为空时返回 ``/``。

    **刻意不解析 ``..``**（v1.4 P1-1 取舍）：解析 ``..`` 需要跟平台/代理的路径
    解析规则博弈（Windows 与 POSIX 对 ``..\\`` 的处理不同，反向代理可能先归一
    一次），解析错了反而会「把攻击路径洗成正常路径」而放行。保留 ``..`` 原样后，
    这类请求要么被 FastAPI 路由判为不存在、要么落到 SPA 回退，而静态托管的越界
    判定由 ``main.spa_fallback`` 的 ``resolve()`` 边界检查兜底。

    Args:
        path: 原始请求路径（``scope["path"]``）。

    Returns:
        归一化后的路径，恒以 ``/`` 开头。
    """
    value = (path or "/").replace("\\", "/")
    segments = [segment for segment in value.split("/") if segment not in ("", ".")]
    if not segments:
        return "/"
    return "/" + "/".join(segments)


def is_api_path(path: str) -> bool:
    """是否 ``/api`` 命名空间下的路径（含裸 ``/api``，大小写不敏感）。"""
    value = (path or "").lower()
    return value == "/api" or value.startswith("/api/")


def is_public_path(path: str) -> bool:
    """门禁开启时该路径是否**无需 Cookie**即可访问。

    顺序要点：
    1. 先判 ``/api`` 白名单（health / login / status），这三个必须放行；
    2. 再判文档路径（``/docs`` 等）→ 必须拦（它不在 ``/api`` 下）；
    3. 其它 ``/api/*`` → 需要 Cookie；
    4. 剩下的一律放行（静态资源、SPA 路由、``/login``、``/sw.js`` …）。

    比对**统一小写**（v1.4 P1-1）：``/API/search`` 与 ``/api/search`` 必须得到同一
    结论。当前 FastAPI 路由本身大小写敏感、``/API/search`` 会落到 SPA 回退，看起来
    「零泄露」；但这只是运气 —— 一旦将来引入大小写不敏感路由、或在前面加一层会做
    归一化的反向代理，分类不一致立刻变成真实越权。

    本函数**自己调 :func:`_normalize`**（幂等），这样无论调用方有没有先归一化，
    分类结论都一致，不会因为漏调一次归一化就放行。

    Args:
        path: 请求路径（不含 query），可以是原始写法。

    Returns:
        ``True`` = 放行。
    """
    value = _normalize(path).lower()
    if value in PUBLIC_API_PATHS:
        return True
    if any(value == doc or value.startswith(f"{doc}/") for doc in PROTECTED_DOC_PATHS):
        return False
    if is_api_path(value):
        return False
    return True


def unauthorized_body() -> Dict[str, Any]:
    """401 统一响应体（与 ``models.fail`` 结构一致，这里直接构造以免 import 环）。"""
    return {"code": CODE_UNAUTHORIZED, "message": UNAUTHORIZED_MESSAGE, "data": None}


# ---------------------------------------------------------------------------
# 中间件
# ---------------------------------------------------------------------------
class AccessGateMiddleware:
    """纯 ASGI 门禁中间件：门禁关闭时零开销直通。

    放在 ``app.add_middleware()`` 里注册后，它位于 Starlette 中间件栈中
    ``ExceptionMiddleware`` 的**外层**：401 由本类直接构造响应，不经过异常处理器，
    因此响应体一定是 ``{code, message, data}`` 结构。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI 入口。"""
        if scope.get("type") != "http":
            # websocket / lifespan 一律放行（本项目未使用 websocket）
            await self.app(scope, receive, send)
            return

        if not gate_enabled():
            await self.app(scope, receive, send)
            return

        path = _normalize(str(scope.get("path") or "/"))
        if is_public_path(path):
            await self.app(scope, receive, send)
            return

        cookies = parse_cookies(scope.get("headers") or [])
        if session_valid(cookies.get(config.SESSION_COOKIE)):
            await self.app(scope, receive, send)
            return

        logger.info("门禁拦截未授权请求：%s %s", scope.get("method"), path)
        response = JSONResponse(status_code=401, content=unauthorized_body())
        await response(scope, receive, send)


__all__ = [
    "AccessGateMiddleware",
    "BAD_TOKEN_MESSAGE",
    "CODE_BAD_TOKEN",
    "CODE_UNAUTHORIZED",
    "PROTECTED_DOC_PATHS",
    "PUBLIC_API_PATHS",
    "SESSION_SALT",
    "UNAUTHORIZED_MESSAGE",
    "gate_enabled",
    "is_api_path",
    "is_public_path",
    "parse_cookies",
    "session_valid",
    "session_value",
    "unauthorized_body",
    "verify_token",
]
