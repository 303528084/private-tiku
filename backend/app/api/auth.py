"""门禁登录接口（v1.4 T01）：``/api/auth/login`` / ``logout`` / ``status``。

三个接口都在 :data:`app.auth.PUBLIC_API_PATHS` 白名单里（``logout`` 除外——它
只是清 Cookie，但保留在门禁内可以顺带验证会话仍然有效；前端在 401 时同样会
跳登录页，不会死循环）。

**未启用门禁时**（``TIKU_ACCESS_TOKEN`` 为空）：``login`` 直接返回
``data.enabled = false``，前端据此统一处理，不必区分两种部署形态。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request, Response

from .. import config
from .. import auth as gate
from ..models import ApiError, LoginRequest, ok

logger = logging.getLogger("private_tiku.api.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
def login(payload: LoginRequest, response: Response) -> dict[str, Any]:
    """口令登录：正确则下发 HttpOnly 会话 Cookie。

    Args:
        payload: ``{"token": "..."}``。
        response: FastAPI 注入的响应对象（用于 ``Set-Cookie``）。

    Returns:
        ``{code: 0, message: "登录成功", data: {enabled: true}}``。

    Raises:
        ApiError: 口令错误 → 401 / ``40101``。
    """
    if not gate.gate_enabled():
        return ok({"enabled": False}, "未启用访问门禁")

    if not gate.verify_token(payload.token):
        logger.warning("口令错误，拒绝登录（提交长度 %d）", len(payload.token or ""))
        raise ApiError(gate.BAD_TOKEN_MESSAGE, gate.CODE_BAD_TOKEN, 401)

    response.set_cookie(
        key=config.SESSION_COOKIE,
        value=gate.session_value(),
        max_age=config.SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )
    logger.info("登录成功，已下发会话 Cookie（有效期 %d 秒）", config.SESSION_MAX_AGE)
    return ok({"enabled": True}, "登录成功")


@router.post("/logout")
def logout(response: Response) -> dict[str, Any]:
    """登出：把会话 Cookie 置为过期（``Max-Age=0``）。"""
    response.delete_cookie(
        key=config.SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
    )
    logger.info("已清除会话 Cookie")
    return ok({"enabled": gate.gate_enabled()}, "已退出登录")


@router.get("/status")
def status(request: Request) -> dict[str, Any]:
    """门禁状态：前端启动时据此决定是否跳登录页。

    Returns:
        ``{code: 0, data: {enabled, authenticated}}``；未启用门禁时
        ``authenticated`` 恒为 ``True``（无门禁即视为已通过）。
    """
    enabled = gate.gate_enabled()
    authenticated = True
    if enabled:
        authenticated = gate.session_valid(request.cookies.get(config.SESSION_COOKIE))
    return ok({"enabled": enabled, "authenticated": authenticated})


__all__ = ["router"]
