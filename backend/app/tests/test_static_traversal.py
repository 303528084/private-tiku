"""静态产物托管的路径穿越回归测试（P0-1）。

**为什么不用 ``TestClient.get()``**：httpx 在客户端就把 URL 里的 ``..`` 段归一化掉了，
``client.get("/../../README.md")`` 实际发出的是 ``GET /README.md``，测试会**假通过**
而漏洞仍在。这里直接构造 ASGI ``scope`` 调用应用，绕开所有客户端归一化。

``scope["path"]`` 按 ASGI 规范必须是**已解码**的路径（uvicorn 会把 ``%2f`` 解成 ``/``
再放进 ``path``，原文另存 ``raw_path``），所以百分号编码用例传入的是解码后的 path +
原始 raw_path，与真实 uvicorn 行为一致。

断言口径：不只看状态码，必须确认**响应体不含目标文件的内容特征**
（``SQLite format 3`` / ``ACCESS_TOKEN`` / 源码里的 ``from __future__``）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from app import config, db
from app.main import app

# 目标文件的「内容特征」：命中任一条即视为泄漏
LEAK_MARKERS: Tuple[bytes, ...] = (
    b"SQLite format 3",  # data/tiku.db 魔数
    b"ACCESS_TOKEN",  # .env.example
    b"from __future__ import",  # *.py 源码
    "# 私有护理题库".encode("utf-8"),  # README.md 正文
)

# 仓库根（backend/ 的上一级）：穿越目标都在这里
REPO_ROOT = Path(__file__).resolve().parents[3]


async def _asgi_request(
    path: str, raw_path: Optional[bytes] = None, headers: Optional[List[Tuple[bytes, bytes]]] = None
) -> Tuple[int, Dict[str, str], bytes]:
    """直接以 ASGI scope 调用应用，返回 ``(status, headers, body)``。"""
    scope: Dict[str, Any] = {
        "type": "http",
        # spec_version 2.4：服务端**不**保证发送 http.disconnect，Starlette 的
        # FileResponse 因此走「直接写完」分支，不必等 receive() 里的断开事件
        # （2.0~2.3 时它会死等 http.disconnect，手工构造 scope 会挂住）。
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


def request(path: str, raw_path: Optional[bytes] = None) -> Tuple[int, Dict[str, str], bytes]:
    """同步包装 :func:`_asgi_request`。"""
    return asyncio.run(_asgi_request(path, raw_path))


def assert_no_leak(status: int, headers: Dict[str, str], body: bytes, path: str) -> None:
    """断言既没把文件吐出来，也没退化成 SPA 壳（避免「看似成功」掩盖攻击）。"""
    assert status == 404, f"{path} 应返回 404，实际 {status}：{body[:200]!r}"
    assert headers.get("content-type", "").startswith("application/json"), headers
    payload = json.loads(body.decode("utf-8"))
    assert payload["code"] == 40401, payload
    for marker in LEAK_MARKERS:
        assert marker not in body, f"{path} 泄漏了 {marker!r}"
    # 不能回退到 index.html（那会让探测「看起来成功」）
    assert b"<div id=" not in body.lower(), f"{path} 回退成了 SPA 壳"


# ===========================================================================
# 前置：确认穿越目标确实存在（否则用例会「因为文件不存在」而假通过）
# ===========================================================================
@pytest.mark.parametrize(
    "relative",
    ["data/tiku.db", "README.md", "backend/app/config.py", ".env.example"],
)
def test_traversal_targets_exist(relative: str) -> None:
    """这些文件真实存在，且 ``dist / 穿越路径`` 在修复前能解析到它们。

    ``data/`` 在 ``.gitignore`` 里，干净检出（``git worktree`` / CI 首次运行）
    时 ``data/tiku.db`` 尚未生成。此时按真实启动路径（``ensure_dirs`` +
    ``init_db``）初始化一次，而不是跳过——跳过会让下面 4 个穿越用例
    「因为目标不存在」而假通过。
    """
    target = REPO_ROOT / relative
    if not target.is_file() and relative.startswith("data/"):
        config.ensure_dirs()
        db.init_db()
    assert target.is_file(), f"测试样本缺失：{target}"

    dist = config.FRONTEND_DIST
    naive = dist / f"../../{relative}"
    assert naive.resolve() == target.resolve(), naive.resolve()


# ===========================================================================
# P0-1 核心：四个真实穿越 URL
# ===========================================================================
@pytest.mark.parametrize(
    "path",
    [
        "/../../data/tiku.db",
        "/../../README.md",
        "/../../backend/app/config.py",
        "/../../.env.example",
        "/../../../private-tiku/data/tiku.db",  # 多穿一层
        "/./../../data/tiku.db",  # 混入 . 段
    ],
)
def test_traversal_paths_return_404_without_leak(path: str) -> None:
    """``/../../...`` 一律 404，且响应体不含目标文件内容。"""
    status, headers, body = request(path)
    assert_no_leak(status, headers, body, path)


def test_percent_encoded_traversal_returns_404_without_leak() -> None:
    """``/..%2f..%2fREADME.md``：uvicorn 解码后 path 与上一组等价，同样 404。"""
    status, headers, body = request("/../../README.md", raw_path=b"/..%2f..%2fREADME.md")
    assert_no_leak(status, headers, body, "/..%2f..%2fREADME.md")


@pytest.mark.parametrize(
    "path",
    [
        "/..\\..\\data\\tiku.db",
        "/..\\..\\README.md",
        "/..\\..\\backend\\app\\config.py",
        "/..\\..\\.env.example",
    ],
)
def test_backslash_traversal_returns_404_without_leak(path: str) -> None:
    """反斜杠变体（Windows 分隔符）同样 404 —— 修复里显式把 ``\\`` 归一成 ``/``。"""
    status, headers, body = request(path)
    assert_no_leak(status, headers, body, path)


def test_deep_traversal_to_absolute_path_returns_404() -> None:
    """穿到盘符绝对路径（``/C:/Windows/win.ini``）同样 404。"""
    status, headers, body = request("/C:/Windows/win.ini")
    assert_no_leak(status, headers, body, "/C:/Windows/win.ini")


# ===========================================================================
# 功能不能改坏
# ===========================================================================
def test_static_file_still_served() -> None:
    """``/favicon.svg`` 仍能正常返回（``image/svg+xml``）。"""
    status, headers, body = request("/favicon.svg")

    assert status == 200, body[:200]
    assert headers.get("content-type", "").startswith("image/svg+xml"), headers
    assert b"<svg" in body.lower()


def test_spa_route_falls_back_to_index_html() -> None:
    """正常前端路由（``/questions/1``）仍回退到 ``index.html``。"""
    status, headers, body = request("/questions/1")

    assert status == 200, body[:200]
    assert headers.get("content-type", "").startswith("text/html"), headers
    assert b'<div id="root">' in body


def test_root_path_returns_index_html() -> None:
    """``/`` 仍返回 SPA 壳。"""
    status, headers, body = request("/")

    assert status == 200, body[:200]
    assert headers.get("content-type", "").startswith("text/html"), headers


def test_unknown_api_path_message_unchanged() -> None:
    """``/api/nope`` 的文案修复不受本次改动影响。"""
    status, headers, body = request("/api/nope")

    assert status == 404
    payload = json.loads(body.decode("utf-8"))
    assert payload["message"] == "接口不存在：/api/nope"
    assert "/api/api/" not in payload["message"]


@pytest.mark.parametrize("path", ["/API/nope", "/./api/nope", "/api\\nope"])
def test_api_namespace_variants_return_404(path: str) -> None:
    """``/API/nope``、``/./api/nope``、``/api\\nope`` 与门禁分类口径一致 → 404。"""
    status, headers, body = request(path)

    assert status == 404, (path, status, body[:120])
    payload = json.loads(body.decode("utf-8"))
    assert payload["code"] == 40401, payload
    assert payload["message"].startswith("接口不存在：/"), payload
    assert "/api/api/" not in payload["message"], payload


# ===========================================================================
# 与门禁的叠加行为
# ===========================================================================
def test_traversal_blocked_even_when_gate_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """门禁开启时穿越路径仍是 404（不是 401、更不是 200）。"""
    monkeypatch.setenv("TIKU_ACCESS_TOKEN", "p0-secret")

    status, headers, body = request("/../../data/tiku.db")
    assert_no_leak(status, headers, body, "/../../data/tiku.db")

    # 对照：门禁确实生效（/api/* 401），说明不是「门禁被绕过」才 404
    api_status, _api_headers, api_body = request("/api/search")
    assert api_status == 401, api_body[:200]
    assert json.loads(api_body.decode("utf-8"))["code"] == 40100


def test_assets_mount_rejects_traversal() -> None:
    """``/assets/../../README.md`` 由 StaticFiles 自己拦住（404）。"""
    status, _headers, body = request("/assets/../../README.md")

    assert status == 404, body[:200]
    assert "# 私有护理题库".encode("utf-8") not in body
