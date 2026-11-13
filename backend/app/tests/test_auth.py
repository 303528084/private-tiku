"""门禁测试（T01）：中间件放行规则、Cookie 会话、口令比对与状态接口。

对应架构 §9.1 T01 验收标准 ②–⑧：

* ② 设了口令、无 Cookie → ``/api/search`` 401 且响应体是 ``{code,message,data}``
  （不是 FastAPI 默认的 ``{"detail": ...}``）；
* ③ ``/api/health`` 始终 200（compose healthcheck 依赖）；
* ④ ``/``、``/assets/*``、``/manifest.webmanifest``、``/sw.js`` 等静态与 SPA 路由
  不返回 401；
* ⑤ ``/docs``、``/openapi.json``、``/redoc``、``/api/stats`` 401；
* ⑥ 正确口令 → ``HttpOnly`` Cookie；错误口令 → 401；两者耗时接近；
* ⑦ 改 ``TIKU_ACCESS_TOKEN`` 后旧 Cookie 失效；
* ⑧ 每次请求都读环境变量（``monkeypatch.setenv`` 即时生效）。

另加：① 未设口令时全站与 v1.3 一致（本文件第一条用例）。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from app import auth, config, db
from app.main import app

TOKEN: str = "s3cret-token"
"""测试口令（12 字符，便于用等长错误口令做时序对照）。"""

WRONG_TOKEN: str = "xxxxxxxxxxxx"
"""与 :data:`TOKEN` 等长的错误口令（排除长度差异对耗时的干扰）。"""


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterable[TestClient]:
    """独立临时库 + **门禁关闭**（每个用例自行按需 setenv 打开）。"""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "DB_PATH", data_dir / "tiku.db")
    monkeypatch.setattr(config, "UPLOAD_DIR", data_dir / "uploads")
    monkeypatch.delenv(config.ACCESS_TOKEN_ENV, raising=False)
    db.reset_init_flag()
    db.init_db()
    with TestClient(app) as test_client:
        yield test_client
    db.reset_init_flag()


def envelope(response: Any) -> Dict[str, Any]:
    """断言统一外壳的三个键都存在，并返回响应体。"""
    body = response.json()
    assert set(body) == {"code", "message", "data"}, body
    return body


def assert_unauthorized(response: Any, code: int = auth.CODE_UNAUTHORIZED) -> Dict[str, Any]:
    """断言 401 + 统一结构（**不是** ``{"detail": ...}``）。"""
    assert response.status_code == 401, response.text
    assert response.headers["content-type"].startswith("application/json"), response.headers
    body = envelope(response)
    assert "detail" not in body, body
    assert body["code"] == code, body
    assert body["data"] is None
    assert body["message"]
    return body


# ===========================================================================
# ① 未启用门禁 → 与 v1.3 完全一致
# ===========================================================================
def test_gate_disabled_keeps_v13_behaviour(client: TestClient) -> None:
    """未设 ``TIKU_ACCESS_TOKEN``：接口、文档、SPA 全部照旧可访问。"""
    assert client.get("/api/search").status_code == 200
    assert client.get("/api/stats").status_code == 200
    assert client.get("/api/health").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200

    status = envelope(client.get("/api/auth/status"))
    assert status["code"] == 0
    assert status["data"] == {"enabled": False, "authenticated": True}


def test_login_when_disabled_returns_enabled_false(client: TestClient) -> None:
    """未启用门禁时调用 login：直接成功并告知前端 ``enabled=false``。"""
    response = client.post("/api/auth/login", json={"token": "随便填"})
    assert response.status_code == 200
    body = envelope(response)
    assert body["code"] == 0
    assert body["data"] == {"enabled": False}
    assert "set-cookie" not in {key.lower() for key in response.headers}


# ===========================================================================
# ② / ③ / ⑤ 中间件放行与拦截
# ===========================================================================
def test_api_requires_session(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """设了口令、无 Cookie → 业务接口 401 且为统一结构。"""
    monkeypatch.setenv(config.ACCESS_TOKEN_ENV, TOKEN)

    body = assert_unauthorized(client.get("/api/search"))
    assert body["message"] == auth.UNAUTHORIZED_MESSAGE
    assert body["code"] == 40100

    # 写操作同样被拦
    assert_unauthorized(client.delete("/api/imports/1"))
    # 既有导出接口结构不受影响，但需要登录
    assert_unauthorized(client.get("/api/export?format=json"))


def test_health_is_always_open(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """``/api/health`` 不受门禁影响（compose healthcheck 依赖它）。"""
    monkeypatch.setenv(config.ACCESS_TOKEN_ENV, TOKEN)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert envelope(response)["code"] == 0


def test_docs_and_openapi_are_blocked(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/docs`` / ``/openapi.json`` / ``/redoc`` / ``/api/stats`` 一律 401。"""
    monkeypatch.setenv(config.ACCESS_TOKEN_ENV, TOKEN)
    for path in ("/docs", "/openapi.json", "/redoc", "/docs/oauth2-redirect", "/api/stats"):
        assert_unauthorized(client.get(path))


@pytest.mark.parametrize(
    "path",
    ["/", "/login", "/favicon.svg", "/manifest.webmanifest", "/sw.js", "/assets/app.js", "/stats"],
)
def test_static_and_spa_paths_are_open(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """静态资源与 SPA 路由不能返回 401（否则前端白屏、PWA 静默失效）。"""
    monkeypatch.setenv(config.ACCESS_TOKEN_ENV, TOKEN)
    response = client.get(path)
    assert response.status_code != 401, f"{path} 被门禁拦截了"


def test_real_asset_file_is_served(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """若前端已构建，挑一个真实 ``/assets/*`` 文件确认能取到（非 401）。"""
    monkeypatch.setenv(config.ACCESS_TOKEN_ENV, TOKEN)
    assets_dir = config.FRONTEND_DIST / "assets"
    if not assets_dir.is_dir():
        pytest.skip("frontend/dist 未构建，跳过真实静态资源校验")
    files = sorted(item.name for item in assets_dir.iterdir() if item.is_file())
    if not files:
        pytest.skip("frontend/dist/assets 为空")
    response = client.get(f"/assets/{files[0]}")
    assert response.status_code == 200, response.text


# ===========================================================================
# ⑥ 登录成功 / 失败
# ===========================================================================
def test_login_success_sets_httponly_cookie(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """正确口令 → 200 + ``HttpOnly`` 会话 Cookie + 之后可访问接口。"""
    monkeypatch.setenv(config.ACCESS_TOKEN_ENV, TOKEN)

    response = client.post("/api/auth/login", json={"token": TOKEN})
    assert response.status_code == 200, response.text
    body = envelope(response)
    assert body["code"] == 0
    assert body["data"] == {"enabled": True}

    set_cookie = response.headers["set-cookie"]
    lowered = set_cookie.lower()
    assert set_cookie.startswith(f"{config.SESSION_COOKIE}="), set_cookie
    assert "httponly" in lowered, set_cookie
    assert "samesite=lax" in lowered, set_cookie
    assert "path=/" in lowered, set_cookie
    assert f"max-age={config.SESSION_MAX_AGE}" in lowered, set_cookie
    # Cookie 里绝不能是口令明文
    assert TOKEN not in set_cookie

    assert client.get("/api/search").status_code == 200
    status = envelope(client.get("/api/auth/status"))["data"]
    assert status == {"enabled": True, "authenticated": True}


def test_login_wrong_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """错误口令 → 401 / 40101，且不下发 Cookie。"""
    monkeypatch.setenv(config.ACCESS_TOKEN_ENV, TOKEN)

    response = client.post("/api/auth/login", json={"token": WRONG_TOKEN})
    body = assert_unauthorized(response, auth.CODE_BAD_TOKEN)
    assert body["message"] == auth.BAD_TOKEN_MESSAGE
    assert "set-cookie" not in {key.lower() for key in response.headers}

    # 空口令同样失败（不能因为「空串」被当成未启用而放行）
    assert_unauthorized(
        client.post("/api/auth/login", json={"token": ""}), auth.CODE_BAD_TOKEN
    )
    assert_unauthorized(client.get("/api/search"))


def test_login_is_constant_time(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """正确与错误口令的耗时无显著差异（``hmac.compare_digest``）。

    单次 HTTP 往返的抖动远大于比对耗时，故取多次中位数并给足余量；
    真正的保障是 ``test_compare_digest_is_used``（源码级断言）。
    """
    monkeypatch.setenv(config.ACCESS_TOKEN_ENV, TOKEN)
    rounds = 20

    def measure(token: str) -> float:
        samples: List[float] = []
        for _ in range(rounds):
            started = time.perf_counter()
            client.post("/api/auth/login", json={"token": token})
            samples.append(time.perf_counter() - started)
        samples.sort()
        return samples[len(samples) // 2]

    ok_median = measure(TOKEN)
    bad_median = measure(WRONG_TOKEN)
    delta = abs(ok_median - bad_median)
    assert delta < 0.05, f"耗时差异过大：ok={ok_median:.4f}s bad={bad_median:.4f}s"


def test_compare_digest_is_used() -> None:
    """源码级断言：口令与会话比对必须走 ``hmac.compare_digest``（常量时间）。"""
    source = (Path(auth.__file__)).read_text(encoding="utf-8")
    assert "hmac.compare_digest" in source
    # 不允许出现 ``==`` 直接比对口令/会话的实现
    assert "candidate ==" not in source
    assert "cookie_value ==" not in source


# ===========================================================================
# ⑦ 换口令 → 旧 Cookie 失效
# ===========================================================================
def test_changing_token_invalidates_old_cookie(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """改 ``TIKU_ACCESS_TOKEN`` 后，旧 Cookie 立即失效（HMAC 随之改变）。"""
    monkeypatch.setenv(config.ACCESS_TOKEN_ENV, TOKEN)
    assert client.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    assert client.get("/api/search").status_code == 200

    monkeypatch.setenv(config.ACCESS_TOKEN_ENV, f"{TOKEN}-rotated")

    assert_unauthorized(client.get("/api/search"))
    status = envelope(client.get("/api/auth/status"))["data"]
    assert status == {"enabled": True, "authenticated": False}

    # 新口令可正常登录
    assert client.post("/api/auth/login", json={"token": f"{TOKEN}-rotated"}).status_code == 200
    assert client.get("/api/search").status_code == 200


# ===========================================================================
# ⑧ 每次请求都读环境变量
# ===========================================================================
def test_env_is_read_per_request(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """同一进程内切换环境变量即时生效（证明没有模块级缓存）。"""
    monkeypatch.setenv(config.ACCESS_TOKEN_ENV, TOKEN)
    assert client.post("/api/auth/login", json={"token": TOKEN}).status_code == 200

    monkeypatch.setenv(config.ACCESS_TOKEN_ENV, "another-token")
    assert client.post("/api/auth/login", json={"token": TOKEN}).status_code == 401
    assert client.post("/api/auth/login", json={"token": "another-token"}).status_code == 200

    # 关闭门禁后，无 Cookie 也应放行
    monkeypatch.delenv(config.ACCESS_TOKEN_ENV, raising=False)
    assert auth.gate_enabled() is False
    fresh = TestClient(app)
    assert fresh.get("/api/search").status_code == 200


def test_session_value_is_derived_from_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """会话值 = HMAC-SHA256(token, SALT)，不含口令明文且随口令变化。"""
    monkeypatch.setenv(config.ACCESS_TOKEN_ENV, TOKEN)
    first = auth.session_value()
    assert len(first) == 64
    assert TOKEN not in first
    assert auth.session_valid(first) is True
    assert auth.session_valid(first + "0") is False
    assert auth.session_valid(None) is False

    monkeypatch.setenv(config.ACCESS_TOKEN_ENV, f"{TOKEN}x")
    assert auth.session_value() != first


# ===========================================================================
# 登出 / 状态
# ===========================================================================
def test_logout_clears_cookie(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """登出后 Cookie 过期，接口重新需要登录。"""
    monkeypatch.setenv(config.ACCESS_TOKEN_ENV, TOKEN)
    assert client.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    assert client.get("/api/search").status_code == 200

    response = client.post("/api/auth/logout")
    assert response.status_code == 200
    assert envelope(response)["code"] == 0
    set_cookie = response.headers.get("set-cookie", "").lower()
    assert config.SESSION_COOKIE in set_cookie
    assert "max-age=0" in set_cookie, set_cookie

    assert_unauthorized(client.get("/api/search"))


def test_status_before_login(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """``/api/auth/status`` 自身永不被拦，用于前端决定是否跳登录页。"""
    monkeypatch.setenv(config.ACCESS_TOKEN_ENV, TOKEN)
    response = client.get("/api/auth/status")
    assert response.status_code == 200
    assert envelope(response)["data"] == {"enabled": True, "authenticated": False}


# ===========================================================================
# 门禁与异常处理器的共存
# ===========================================================================
def test_exception_handlers_still_work_behind_gate(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """门禁放行后，``install_exception_handlers`` 仍然生效（中间件不吞/不改写错误）。

    这是「中间件写在异常处理器外层」最容易踩的坑：401 由中间件自己构造，
    其余错误必须继续走统一结构。
    """
    monkeypatch.setenv(config.ACCESS_TOKEN_ENV, TOKEN)
    assert client.post("/api/auth/login", json={"token": TOKEN}).status_code == 200

    # 路径参数类型错误 → RequestValidationError → 400 / 40001
    validation = client.get("/api/questions/abc")
    assert validation.status_code == 400
    assert envelope(validation)["code"] == 40001

    # 业务异常（未上传文件）→ 400 / 40001
    no_file = client.post("/api/imports")
    assert no_file.status_code == 400
    assert envelope(no_file)["code"] == 40001

    # 不存在的接口 → 404 / 40401
    missing = client.get("/api/not-exists")
    assert missing.status_code == 404
    assert envelope(missing)["code"] == 40401


# ===========================================================================
# 路径判定单元测试
# ===========================================================================
@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/health", True),
        ("/api/auth/login", True),
        ("/api/auth/status", True),
        ("/api/search", False),
        ("/api", False),
        ("/docs", False),
        ("/docs/oauth2-redirect", False),
        ("/redoc", False),
        ("/openapi.json", False),
        ("/", True),
        ("/login", True),
        ("/assets/index-abc.js", True),
        ("/questions/12", True),
        # v1.4 P1-1：大小写与分隔符变体必须与正体同判（否则将来换路由/加代理即越权）
        ("/API/search", False),
        ("/Api/search", False),
        ("/API", False),
        ("/API/health", True),
        ("/DOCS", False),
        ("/OpenAPI.json", False),
        ("/api\\search", False),
        ("/api\\health", True),
        ("/./api/search", False),
        ("/./api/health", True),
        ("/./docs", False),
        ("/./questions/12", True),
    ],
)
def test_is_public_path(path: str, expected: bool) -> None:
    """放行规则的顺序契约（白名单 → 文档 → /api → 其余放行）。"""
    assert auth.is_public_path(path) is expected


def test_normalize_path_handles_slashes() -> None:
    """重复斜杠与结尾斜杠不能绕过门禁。"""
    assert auth.is_public_path("/api//search") is False
    assert auth.is_public_path("/api/search/") is False
    assert auth.is_public_path("/docs/") is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/api/search", "/api/search"),
        ("/api//search", "/api/search"),
        ("/api/search/", "/api/search"),
        ("/./api/search", "/api/search"),
        ("/api\\search", "/api/search"),
        ("/api\\\\search", "/api/search"),
        ("", "/"),
        ("/", "/"),
        ("///", "/"),
        ("/./", "/"),
        # 刻意不解析 `..`（见 _normalize docstring）：保留原样，交给路由/静态边界判定
        ("/../api/search", "/../api/search"),
        ("/a/../b", "/a/../b"),
    ],
)
def test_normalize_outputs(raw: str, expected: str) -> None:
    """``_normalize`` 的四条规则：反斜杠、重复斜杠、``.`` 段、空路径。"""
    assert auth._normalize(raw) == expected


def test_normalize_does_not_resolve_dotdot() -> None:
    """``..`` 段必须原样保留（解析它反而要跟平台/代理规则博弈）。

    保留之后这类路径不属于 ``/api`` 命名空间，因此门禁**放行**它——安全性由静态
    托管的越界判定兜底：``/../api/search`` 落到 SPA 回退，``resolve()`` 后发现越出
    ``dist`` 直接 404，既不会读到文件也不会命中 API（见
    ``test_dotdot_path_never_reaches_api``）。
    """
    normalized = auth._normalize("/../api/search")
    assert ".." in normalized.split("/")
    assert normalized == "/../api/search"


def test_dotdot_path_never_reaches_api() -> None:
    """``/../api/search`` 拿不到任何数据：不是 200 的 JSON，而是 404。"""
    response = raw_get("/../api/search")

    assert response["status"] == 404, response
    assert response["body"]["code"] == 40401, response


def test_middleware_normalizes_before_classification() -> None:
    """中间件分类前先归一化：``/./api/search`` 与 ``/API/search`` 都要 401。"""
    token = "p1-secret"
    with mock.patch.dict(os.environ, {"TIKU_ACCESS_TOKEN": token}):
        for path in ("/./api/search", "/API/search", "/Api/search", "/api\\search", "/api//search"):
            response = raw_get(path)
            assert response["status"] == 401, (path, response)
            assert response["body"]["code"] == 40100, (path, response)


def raw_get(path: str) -> Dict[str, Any]:
    """用原始 ASGI scope 调一次 GET，返回 ``{"status", "body"}``（绕开 httpx 归一化）。"""
    scope: Dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 51235),
        "server": ("testserver", 80),
    }
    messages: List[Dict[str, Any]] = []

    async def receive() -> Dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Dict[str, Any]) -> None:
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {"raw": body[:200].decode("latin-1")}
    return {"status": status, "body": payload}
