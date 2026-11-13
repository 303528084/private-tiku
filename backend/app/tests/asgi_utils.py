"""测试共用的 ASGI 直连请求工具。

**为什么不用 ``TestClient.get()``**：httpx 在客户端就把 URL 里的 ``..`` 归一化掉了，
``client.get("/../../README.md")`` 实际发出的是 ``GET /README.md``，用来测路径穿越会
**假通过**。这里直接构造 ASGI ``scope`` 调用应用，绕开所有客户端归一化，能精确控制
``path`` / ``raw_path`` / 请求头。

``scope["path"]`` 按 ASGI 规范必须是**已解码**的路径（uvicorn 会把 ``%2f`` 解成 ``/``
再放进 ``path``，原文另存 ``raw_path``）。

``spec_version="2.4"`` 是必须的：Starlette 的 ``FileResponse`` 在 2.0~2.3 下会死等
``http.disconnect``，手工构造 scope 时会挂住整个测试会话。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.main import app

Headers = Sequence[Tuple[bytes, bytes]]


async def asgi_get_async(
    path: str, raw_path: Optional[bytes] = None, headers: Optional[Headers] = None
) -> Tuple[int, Dict[str, str], bytes]:
    """以 ASGI scope 直接发起一次 GET，返回 ``(status, headers, body)``。

    Args:
        path: 已解码的请求路径。
        raw_path: 原始字节路径；None 表示取 ``path`` 的 UTF-8 编码。
        headers: 额外的请求头。

    Returns:
        状态码、响应头（键已小写）、响应体。
    """
    scope: Dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": raw_path if raw_path is not None else path.encode("utf-8"),
        "query_string": b"",
        "root_path": "",
        "headers": list(headers or [(b"host", b"testserver")]),
        "client": ("127.0.0.1", 51234),
        "server": ("testserver", 80),
    }
    messages: List[Dict[str, Any]] = []
    state = {"received": False}

    async def receive() -> Dict[str, Any]:
        if not state["received"]:
            state["received"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: Dict[str, Any]) -> None:
        messages.append(message)

    await app(scope, receive, send)

    status = 0
    response_headers: Dict[str, str] = {}
    chunks: List[bytes] = []
    for message in messages:
        if message["type"] == "http.response.start":
            status = int(message["status"])
            response_headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in message.get("headers", [])
            }
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))
    return status, response_headers, b"".join(chunks)


def asgi_get(
    path: str, raw_path: Optional[bytes] = None, headers: Optional[Headers] = None
) -> Tuple[int, Dict[str, str], bytes]:
    """同步包装 :func:`asgi_get_async`。"""
    return asyncio.run(asgi_get_async(path, raw_path, headers))
