"""SPA 回退路由的 404 文案回归测试（v1.4 修复）。

修复前 ``app/main.py`` 的 SPA 回退里写的是 ``f"接口不存在：/api/{full_path}"``，
而 ``full_path`` 本身**已含** ``api/`` 前缀，于是 ``GET /api/nope`` 返回
「接口不存在：/api/api/nope」。修复后改为 ``f"接口不存在：/{full_path}"``。

本文件用真实请求锁定该文案，避免以后改动路由时再次出现重复前缀。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.main import app

CODE_NOT_FOUND = 40401
"""``install_exception_handlers`` 里 404 的业务码。"""


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """独立临时库 + TestClient（不污染仓库 ``data/``）。"""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "DB_PATH", data_dir / "tiku.db")
    monkeypatch.setattr(config, "UPLOAD_DIR", data_dir / "uploads")
    db.reset_init_flag()
    db.init_db()
    with TestClient(app) as test_client:
        yield test_client
    db.reset_init_flag()


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/nope", "接口不存在：/api/nope"),
        ("/api/nope/deep/path", "接口不存在：/api/nope/deep/path"),
        ("/api/questions/1/unknown", "接口不存在：/api/questions/1/unknown"),
    ],
)
def test_unknown_api_path_message_has_no_duplicated_prefix(
    client: TestClient, path: str, expected: str
) -> None:
    """未知 ``/api/*`` 路径 → 404 + 业务码 40401，且文案不重复 ``/api`` 前缀。"""
    response = client.get(path)

    assert response.status_code == 404, response.text
    body = response.json()
    assert body["code"] == CODE_NOT_FOUND
    assert body["data"] is None
    assert body["message"] == expected
    assert "/api/api/" not in body["message"], body["message"]


def test_unknown_api_path_with_trailing_slash(client: TestClient) -> None:
    """带尾斜杠的未知路径同样只有一层 ``/api``。"""
    response = client.get("/api/nope/")
    assert response.status_code == 404, response.text
    message = response.json()["message"]
    assert "/api/api" not in message, message
    assert message.startswith("接口不存在：/api/")


def test_frontend_fallback_still_returns_hint(client: TestClient) -> None:
    """非 ``/api`` 路径不返回 404（已构建 → index.html；未构建 → 可读提示）。"""
    response = client.get("/some/spa/route")

    assert response.status_code == 200, response.text
    content_type = response.headers["content-type"]
    if content_type.startswith("application/json"):
        # 前端未构建：返回提示 JSON
        body = response.json()
        assert body["code"] == 0
        assert "前端未构建" in body["message"]
    else:
        # 前端已构建：回退到 index.html
        assert "text/html" in content_type
        assert "<div id=" in response.text.lower() or "<!doctype html" in response.text.lower()
