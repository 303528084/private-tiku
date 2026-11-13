"""测试会话级环境隔离。

门禁的开关完全由环境变量 ``TIKU_ACCESS_TOKEN`` 决定（见 ``app/config.py``）。
如果开发者或 CI 机器上恰好 export 了这个变量，既有的 192 项测试会突然全部 401。
这里在测试会话开始时把它摘掉、结束时原样恢复，保证**测试结果不依赖宿主环境**。

需要门禁的用例自己在用例内用 ``monkeypatch.setenv`` 打开（见 ``test_auth.py``）。
"""

from __future__ import annotations

import os
from typing import Iterator

import pytest

TOKEN_ENV: str = "TIKU_ACCESS_TOKEN"


@pytest.fixture(autouse=True, scope="session")
def _isolate_access_token_env() -> Iterator[None]:
    """整个测试会话期间摘除 ``TIKU_ACCESS_TOKEN``，结束后恢复。"""
    saved = os.environ.pop(TOKEN_ENV, None)
    try:
        yield
    finally:
        if saved is not None:
            os.environ[TOKEN_ENV] = saved
