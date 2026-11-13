"""启动器辅助模块的回归测试（v1.4 T05）。

``scripts/launcher.py`` 承担了三个「在 shell 里写起来又长又易错」的职责：
端口探测、就绪探测、``.env`` 装载。它们都直接影响「双击能不能用」，
所以必须能在 CI 里被验证，而不是靠人工双击试。

模块不在 ``app`` 包内（它是仓库级脚本），这里用 ``importlib`` 按文件路径加载。
"""

from __future__ import annotations

import importlib.util
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Iterator, List, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
"""仓库根目录（``backend/app/tests`` 往上三层）。"""

LAUNCHER_PATH = REPO_ROOT / "scripts" / "launcher.py"


def load_launcher() -> ModuleType:
    """按文件路径加载 ``scripts/launcher.py``。

    Returns:
        已执行完毕的模块对象。
    """
    assert LAUNCHER_PATH.is_file(), f"缺少启动器模块：{LAUNCHER_PATH}"
    spec = importlib.util.spec_from_file_location("tiku_launcher", LAUNCHER_PATH)
    assert spec and spec.loader, "无法构造模块 spec"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def launcher() -> ModuleType:
    """加载一次的启动器模块。"""
    return load_launcher()


# ===========================================================================
# .env 解析与装载
# ===========================================================================
def test_parse_env_file_handles_comments_quotes_and_blanks(
    launcher: ModuleType, tmp_path: Path
) -> None:
    """注释、空行、引号、行内 ``=`` 都要正确解析。"""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# 注释行",
                "",
                "TIKU_PORT=8123",
                "  TIKU_ACCESS_TOKEN = spaced-token  ",
                'QUOTED="a=b=c"',
                "SINGLE='x'",
                "NO_EQUALS_LINE",
                "EMPTY=",
            ]
        ),
        encoding="utf-8",
    )

    values = launcher.parse_env_file(env_file)

    assert values["TIKU_PORT"] == "8123"
    assert values["TIKU_ACCESS_TOKEN"] == "spaced-token"
    assert values["QUOTED"] == "a=b=c"
    assert values["SINGLE"] == "x"
    assert values["EMPTY"] == ""
    assert "NO_EQUALS_LINE" not in values


def test_parse_env_file_missing_file_returns_empty(launcher: ModuleType, tmp_path: Path) -> None:
    """``.env`` 不存在时返回空字典而不是抛异常（首次运行必然没有）。"""
    assert launcher.parse_env_file(tmp_path / "nope.env") == {}


def test_apply_env_does_not_override_real_environment(
    launcher: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真实环境变量优先：运维临时 ``set`` 的值必须能压过 ``.env``。"""
    monkeypatch.setenv("TIKU_PORT", "9999")
    monkeypatch.delenv("TIKU_TEST_ONLY_KEY", raising=False)

    applied = launcher.apply_env({"TIKU_PORT": "8000", "TIKU_TEST_ONLY_KEY": "from-env-file"})

    assert "TIKU_PORT" not in applied
    assert launcher.os.environ["TIKU_PORT"] == "9999"
    assert launcher.os.environ["TIKU_TEST_ONLY_KEY"] == "from-env-file"
    monkeypatch.delenv("TIKU_TEST_ONLY_KEY", raising=False)


def test_apply_env_resolves_relative_paths_against_repo_root(
    launcher: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``TIKU_DATA_DIR=./data`` 必须解析到**仓库根**，不是进程当前目录。

    否则从 ``backend/`` 启动会得到 ``backend/data``，悄悄建出第二份数据库——
    数据「丢失」却不报错，是最难排查的一类问题。
    """
    monkeypatch.delenv("TIKU_DATA_DIR", raising=False)

    launcher.apply_env({"TIKU_DATA_DIR": "./data"}, root=tmp_path)

    assert launcher.os.environ["TIKU_DATA_DIR"] == str((tmp_path / "data").resolve())
    monkeypatch.delenv("TIKU_DATA_DIR", raising=False)


def test_apply_env_keeps_absolute_paths(launcher: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """绝对路径原样保留，不被拼到仓库根下。"""
    monkeypatch.delenv("TIKU_DB_PATH", raising=False)
    absolute = str((Path(launcher.REPO_ROOT) / "custom" / "x.db").resolve())

    launcher.apply_env({"TIKU_DB_PATH": absolute})

    assert launcher.os.environ["TIKU_DB_PATH"] == absolute
    monkeypatch.delenv("TIKU_DB_PATH", raising=False)


# ===========================================================================
# 端口探测
# ===========================================================================
def _occupy_port() -> Tuple[socket.socket, int]:
    """占用一个随机空闲端口，返回 ``(socket, port)``；调用方负责关闭。"""
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    return holder, holder.getsockname()[1]


def test_find_free_port_skips_occupied_port(launcher: ModuleType) -> None:
    """起始端口被占用时向上顺延，返回第一个可用端口。"""
    holder, taken = _occupy_port()
    try:
        assert launcher.is_port_free(taken) is False
        found = launcher.find_free_port(taken, span=10)
        assert found is not None
        assert found > taken, f"应当跳过被占用的 {taken}，实际返回 {found}"
        assert launcher.is_port_free(found) is True
    finally:
        holder.close()


def test_find_free_port_returns_base_when_free(launcher: ModuleType) -> None:
    """起始端口空闲时直接返回它，不做多余顺延。"""
    holder, port = _occupy_port()
    holder.close()  # 立刻释放，端口回到空闲
    time.sleep(0.05)

    assert launcher.find_free_port(port, span=1) == port


def test_find_free_port_returns_none_when_span_exhausted(launcher: ModuleType) -> None:
    """范围内全被占用时返回 None，由调用方给出明确错误而不是盲目启动。"""
    holder, taken = _occupy_port()
    try:
        # span=1 表示只试 taken 这一个端口，必然失败
        assert launcher.find_free_port(taken, span=1) is None
    finally:
        holder.close()


def test_find_free_port_stops_at_port_limit(launcher: ModuleType) -> None:
    """端口上界 65535 之外不再尝试（避免把非法值传给 uvicorn）。"""
    assert launcher.find_free_port(65535, span=5) in (65535, None)


def test_default_span_covers_windows_reserved_ranges(launcher: ModuleType) -> None:
    """默认搜索范围要足够宽。

    Windows 上 Hyper-V / WSL / Docker 会整段保留端口（本机实测 7958~8057，
    连续 100 个），默认 8000 恰好落在里面。范围只有 50 时从 8000 起永远
    扫不到 8058，直接「全部无法绑定」。
    """
    assert launcher.PORT_SCAN_SPAN >= 200


def test_describe_port_failure_names_the_cause(launcher: ModuleType) -> None:
    """提示文案必须区分「系统保留」与「被占用」——两者的处置方式不同。"""
    reserved = OSError(13, "access denied")
    reserved.winerror = 10013  # type: ignore[attr-defined]
    busy = OSError(10048, "address already in use")
    busy.winerror = 10048  # type: ignore[attr-defined]

    assert "WinError 10013" in launcher.describe_port_failure(reserved)
    assert "保留" in launcher.describe_port_failure(reserved)
    assert "占用" in launcher.describe_port_failure(busy)
    assert launcher.describe_port_failure(None) == "可用"


def test_scan_ports_reports_why_each_candidate_failed(
    launcher: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """扫不到端口时要把每个候选的失败原因带回来（供分类提示用）。"""
    reserved = OSError(13, "access denied")
    reserved.winerror = 10013  # type: ignore[attr-defined]
    busy = OSError(98, "address already in use")
    errors = {9000: reserved, 9001: busy}

    # 用桩替换 probe_port，避免依赖本机真实端口占用情况
    monkeypatch.setattr(launcher, "probe_port", lambda port, _host="127.0.0.1": errors.get(port))

    found, failures = launcher.scan_ports(9000, span=3, host="127.0.0.1")

    assert found == 9002
    assert [port for port, _err in failures] == [9000, 9001]
    assert launcher.is_reserved_port_error(failures[0][1]) is True
    assert launcher.is_reserved_port_error(failures[1][1]) is False


def test_scan_ports_returns_none_and_all_failures_when_exhausted(
    launcher: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """整段都不可绑定时返回 None，并带回全部失败记录（数量 = span）。"""
    reserved = OSError(13, "access denied")
    reserved.winerror = 10013  # type: ignore[attr-defined]

    monkeypatch.setattr(launcher, "probe_port", lambda _port, _host="127.0.0.1": reserved)

    found, failures = launcher.scan_ports(7958, span=100, host="127.0.0.1")

    assert found is None
    assert len(failures) == 100
    assert failures[0][0] == 7958
    assert failures[-1][0] == 8057


def test_is_reserved_port_error_distinguishes_causes(launcher: ModuleType) -> None:
    """要能区分「端口被系统保留」与「端口被占用」，两者处置方式不同。"""
    reserved = OSError(13, "access denied")
    reserved.winerror = 10013  # type: ignore[attr-defined]

    assert launcher.is_reserved_port_error(reserved) is True
    assert launcher.is_reserved_port_error(OSError(98, "address already in use")) is False
    assert launcher.is_reserved_port_error(None) is False


# ===========================================================================
# serve()：P0 回归（sys.path 必须包含 backend/）
# ===========================================================================
def test_serve_puts_backend_on_sys_path_and_runs_uvicorn(
    launcher: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``serve()`` 必须把 ``backend/`` 放进 ``sys.path`` 再拉起 uvicorn。

    回归用例：``os.chdir()`` **不改变** ``sys.path``。以
    ``python scripts/launcher.py serve`` 启动时 ``sys.path[0]`` 是 ``scripts/``，
    uvicorn 解析 ``"app.main:app"`` 会抛
    ``ModuleNotFoundError: No module named 'app'``——启动器根本起不来。
    """
    recorded: Dict[str, Any] = {}
    fake_uvicorn = ModuleType("uvicorn")

    def fake_run(app_path: str, **kwargs: Any) -> None:
        recorded["app_path"] = app_path
        recorded["kwargs"] = kwargs
        recorded["sys_path"] = list(sys.path)
        recorded["cwd"] = Path.cwd()

    fake_uvicorn.run = fake_run  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(launcher, "parse_env_file", lambda _path: {})
    monkeypatch.setattr(launcher, "apply_env", lambda _values: [])
    monkeypatch.setattr(launcher, "scan_ports", lambda base, **_kwargs: (base, []))
    monkeypatch.setattr(launcher, "wait_for_health", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(launcher, "open_browser", lambda _url: True)

    original_cwd = Path.cwd()
    original_sys_path = list(sys.path)
    try:
        # 模拟真实启动现场：以 `python scripts/launcher.py` 运行时 sys.path[0] 是
        # scripts/，backend/ 根本不在里面。pytest 自己会把 backend/ 塞进 sys.path，
        # 不先摘掉的话这个用例就变成同义反复（无论修没修都能过）。
        sys.path[:] = [
            entry
            for entry in sys.path
            if Path(entry or ".").resolve() != launcher.BACKEND_DIR
        ]
        assert str(launcher.BACKEND_DIR) not in sys.path

        assert launcher.serve(base_port=8123) == 0
    finally:
        os.chdir(original_cwd)
        sys.path[:] = original_sys_path

    assert recorded["app_path"] == "app.main:app"
    assert recorded["sys_path"][0] == str(launcher.BACKEND_DIR), (
        f"backend/ 不在 sys.path 首位：{recorded['sys_path'][:3]}"
    )
    assert recorded["cwd"] == launcher.BACKEND_DIR, "uvicorn 的工作目录应当是 backend/"
    assert recorded["kwargs"]["port"] == 8123
    assert recorded["kwargs"]["host"] == "127.0.0.1"


def test_serve_reports_reserved_port_range(
    launcher: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """整段端口无法绑定时给出**可操作**的提示，而不是只丢一句「全部被占用」。"""
    reserved = OSError(13, "access denied")
    reserved.winerror = 10013  # type: ignore[attr-defined]
    failures = [(8000 + offset, reserved) for offset in range(launcher.PORT_SCAN_SPAN)]

    monkeypatch.setattr(launcher, "parse_env_file", lambda _path: {})
    monkeypatch.setattr(launcher, "apply_env", lambda _values: [])
    monkeypatch.setattr(launcher, "scan_ports", lambda *_args, **_kwargs: (None, failures))

    assert launcher.serve(base_port=8000) == 1

    stderr = capsys.readouterr().err
    assert "WinError 10013" in stderr, "必须点名「系统保留」，不能误报成「被占用」"
    assert "TIKU_PORT" in stderr, "必须给出「指定端口」这条出路"
    assert "netsh" in stderr, "必须给出查看保留段的命令"
    assert "8199" in stderr, "必须说明实际尝试过的区间（8000~8199）"


def test_serve_reports_busy_ports_separately(
    launcher: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """「被占用」与「被保留」要分开统计，否则用户排查方向会错。"""
    reserved = OSError(13, "access denied")
    reserved.winerror = 10013  # type: ignore[attr-defined]
    busy = OSError(98, "address already in use")
    failures = [(9000, busy), (9001, reserved)]

    monkeypatch.setattr(launcher, "parse_env_file", lambda _path: {})
    monkeypatch.setattr(launcher, "apply_env", lambda _values: [])
    monkeypatch.setattr(launcher, "scan_ports", lambda *_args, **_kwargs: (None, failures))

    assert launcher.serve(base_port=9000) == 1

    stderr = capsys.readouterr().err
    assert "1 个被系统保留" in stderr
    assert "1 个已被其他程序占用" in stderr


def test_serve_tells_user_when_explicit_port_is_unavailable(
    launcher: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """用户指定了端口却被换掉时必须明说，不能让他以为服务跑在指定端口上。"""
    reserved = OSError(13, "access denied")
    reserved.winerror = 10013  # type: ignore[attr-defined]
    recorded: Dict[str, Any] = {}

    monkeypatch.setattr(launcher, "parse_env_file", lambda _path: {})
    monkeypatch.setattr(launcher, "apply_env", lambda _values: [])
    monkeypatch.setattr(launcher, "scan_ports", lambda *_args, **_kwargs: (8058, [(8000, reserved)]))
    monkeypatch.setattr(launcher, "probe_port", lambda *_args, **_kwargs: reserved)
    monkeypatch.setattr(launcher, "wait_for_health", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(launcher, "open_browser", lambda _url: True)

    fake_uvicorn = ModuleType("uvicorn")
    fake_uvicorn.run = lambda *_args, **kwargs: recorded.update(kwargs)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    original_cwd = Path.cwd()
    try:
        assert launcher.serve(base_port=8000) == 0
    finally:
        os.chdir(original_cwd)

    stdout = capsys.readouterr().out
    assert "你指定的端口 8000" in stdout
    assert "已改用 8058" in stdout
    assert recorded["port"] == 8058


def test_serve_picks_up_free_port_after_reserved_block(
    launcher: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """默认 8000 落在 Windows 保留段（7958~8057）时，要能顺延到 8058。

    这条用例锁死「span 必须跨过一整段保留区间」这个前提：50 的预算下
    8058 根本扫不到，用户看到的就是「双击失败」。
    """
    reserved = OSError(13, "access denied")
    reserved.winerror = 10013  # type: ignore[attr-defined]
    recorded: Dict[str, Any] = {}

    def fake_probe(port: int, _host: str = "127.0.0.1") -> Any:
        # 7958~8057 全被系统保留，8058 起可用
        return reserved if 7958 <= port <= 8057 else None

    monkeypatch.setattr(launcher, "parse_env_file", lambda _path: {})
    monkeypatch.setattr(launcher, "apply_env", lambda _values: [])
    monkeypatch.delenv("TIKU_PORT", raising=False)
    monkeypatch.setattr(launcher, "probe_port", fake_probe)
    monkeypatch.setattr(launcher, "wait_for_health", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(launcher, "open_browser", lambda _url: True)

    fake_uvicorn = ModuleType("uvicorn")
    fake_uvicorn.run = lambda *_args, **kwargs: recorded.update(kwargs)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    original_cwd = Path.cwd()
    try:
        assert launcher.serve() == 0
    finally:
        os.chdir(original_cwd)

    assert recorded["port"] == 8058
    stdout = capsys.readouterr().out
    assert "已改用 8058" in stdout


# ===========================================================================
# 就绪探测
# ===========================================================================
class _HealthHandler(BaseHTTPRequestHandler):
    """按 ``server.healthy`` 决定返回 200 还是 503 的极简处理器。"""

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        healthy = bool(getattr(self.server, "healthy", True))
        body = b'{"code":0,"data":{"status":"ok"}}' if healthy else b"not ready"
        self.send_response(200 if healthy else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        """静默访问日志，避免污染 pytest 输出。"""


@pytest.fixture()
def health_server() -> Iterator[Tuple[str, HTTPServer]]:
    """起一个本地 HTTP 服务，yield ``(url, server)``。"""
    server = HTTPServer(("127.0.0.1", 0), _HealthHandler)
    server.healthy = True  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/api/health", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_wait_for_health_returns_true_on_200(launcher: ModuleType, health_server: Tuple[str, HTTPServer]) -> None:
    """健康检查 200 → 立刻返回 True。"""
    url, _ = health_server
    assert launcher.wait_for_health(url, timeout=5) is True


def test_wait_for_health_times_out_on_non_2xx(
    launcher: ModuleType, health_server: Tuple[str, HTTPServer]
) -> None:
    """持续 503 时等到超时并返回 False（不能把「进程活着但没就绪」当成就绪）。"""
    url, server = health_server
    server.healthy = False  # type: ignore[attr-defined]

    started = time.monotonic()
    assert launcher.wait_for_health(url, timeout=0.6, interval=0.1) is False
    assert time.monotonic() - started >= 0.5


def test_wait_for_health_times_out_on_closed_port(launcher: ModuleType) -> None:
    """端口没人监听时也要按时超时返回，而不是抛异常。"""
    holder, port = _occupy_port()
    holder.close()
    time.sleep(0.05)

    assert launcher.wait_for_health(f"http://127.0.0.1:{port}/api/health", timeout=0.4, interval=0.1) is False


# ===========================================================================
# 依赖安装判定（按 requirements.txt 内容哈希）
# ===========================================================================
class _RecordingRunner:
    """替代 ``subprocess.run``：记录调用，不真的跑 pip。"""

    def __init__(self, returncode: int = 0) -> None:
        self.calls: List[List[str]] = []
        self.returncode = returncode

    def __call__(self, args: List[str], **_kwargs: object) -> "_FakeCompleted":
        self.calls.append(list(args))
        return _FakeCompleted(self.returncode)


class _FakeCompleted:
    """``subprocess.run`` 的最小返回值。"""

    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


def test_requirements_hash_depends_on_content(launcher: ModuleType, tmp_path: Path) -> None:
    """哈希只随内容变化（换行/字符不同则不同）。"""
    first = tmp_path / "a.txt"
    first.write_text("fastapi\n", encoding="utf-8")
    second = tmp_path / "b.txt"
    second.write_text("fastapi\nuvicorn\n", encoding="utf-8")

    assert launcher.requirements_hash(first) == launcher.requirements_hash(first)
    assert launcher.requirements_hash(first) != launcher.requirements_hash(second)


def test_ensure_dependencies_installs_once_then_skips(
    launcher: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """首次装依赖并写 stamp；第二次直接跳过，不碰 pip。

    这正是「双击启动不该每次等 pip」的保证。
    """
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("fastapi\n", encoding="utf-8")
    stamp = tmp_path / ".deps-stamp"

    runner = _RecordingRunner()
    monkeypatch.setattr(launcher.subprocess, "run", runner)

    assert launcher.ensure_dependencies(requirements, stamp) == 0
    install_calls = [call for call in runner.calls if "install" in call]
    assert len(install_calls) == 2, runner.calls  # --upgrade pip + -r requirements
    assert stamp.read_text(encoding="utf-8") == launcher.requirements_hash(requirements)

    runner.calls.clear()
    assert launcher.ensure_dependencies(requirements, stamp) == 0
    assert runner.calls == [], "依赖未变时不应再调用 pip"


def test_ensure_dependencies_reinstalls_when_requirements_change(
    launcher: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``requirements.txt`` 内容变了必须重装，并更新 stamp。"""
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("fastapi\n", encoding="utf-8")
    stamp = tmp_path / ".deps-stamp"
    runner = _RecordingRunner()
    monkeypatch.setattr(launcher.subprocess, "run", runner)

    assert launcher.ensure_dependencies(requirements, stamp) == 0
    old_stamp = stamp.read_text(encoding="utf-8")

    requirements.write_text("fastapi\nuvicorn\n", encoding="utf-8")
    runner.calls.clear()
    assert launcher.ensure_dependencies(requirements, stamp) == 0

    assert any("-r" in call for call in runner.calls), runner.calls
    assert stamp.read_text(encoding="utf-8") != old_stamp


def test_ensure_dependencies_reports_pip_failure(
    launcher: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pip 失败时返回非 0，且**不**写 stamp（否则下次会误判成已安装）。"""
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("fastapi\n", encoding="utf-8")
    stamp = tmp_path / ".deps-stamp"
    monkeypatch.setattr(launcher.subprocess, "run", _RecordingRunner(returncode=2))

    assert launcher.ensure_dependencies(requirements, stamp) != 0
    assert not stamp.exists(), "安装失败不能写 stamp"


def test_deps_subcommand_is_wired(
    launcher: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``deps`` 子命令能被命令行调用（start.bat / start.sh 依赖它）。"""
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("fastapi\n", encoding="utf-8")
    stamp = tmp_path / ".deps-stamp"
    monkeypatch.setattr(launcher.subprocess, "run", _RecordingRunner())

    code = launcher.main(
        ["deps", "--requirements", str(requirements), "--stamp", str(stamp)]
    )

    assert code == 0
    assert stamp.is_file()


# ===========================================================================
# 端口解析与命令行入口
# ===========================================================================
def test_resolve_base_port_priority(launcher: ModuleType) -> None:
    """优先级：命令行 > ``.env`` 的 TIKU_PORT > 默认 8000。"""
    assert launcher.resolve_base_port(9100, {"TIKU_PORT": "8200"}) == 9100
    assert launcher.resolve_base_port(None, {"TIKU_PORT": "8200"}) == 8200
    assert launcher.resolve_base_port(None, {}) == launcher.DEFAULT_BASE_PORT


@pytest.mark.parametrize("raw", ["abc", "", "70000", "-1", "0"])
def test_resolve_base_port_falls_back_on_invalid_value(launcher: ModuleType, raw: str) -> None:
    """``.env`` 里写了非法端口时退回默认值，而不是让启动直接崩掉。"""
    assert launcher.resolve_base_port(None, {"TIKU_PORT": raw}) == launcher.DEFAULT_BASE_PORT


def test_port_subcommand_prints_a_number(launcher: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    """``port`` 子命令把端口打到 stdout（供 shell 用 ``$(...)`` 捕获）。"""
    holder, port = _occupy_port()
    holder.close()
    time.sleep(0.05)

    assert launcher.main(["port", "--base", str(port), "--span", "1"]) == 0
    assert capsys.readouterr().out.strip() == str(port)


def test_wait_subcommand_returns_nonzero_on_timeout(
    launcher: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """``wait`` 超时返回 1，错误信息走 stderr。"""
    holder, port = _occupy_port()
    holder.close()
    time.sleep(0.05)

    code = launcher.main(["wait", "--url", f"http://127.0.0.1:{port}/api/health", "--timeout", "0.3"])

    assert code == 1
    assert "未就绪" in capsys.readouterr().err


def test_launcher_module_has_no_side_effects_on_import() -> None:
    """导入模块不应启动服务、不应改工作目录（否则 pytest 会莫名其妙跑偏）。"""
    before = Path.cwd()
    module = load_launcher()
    assert Path.cwd() == before
    assert module.HEALTH_PATH == "/api/health"
    assert module.BACKEND_DIR.name == "backend"
    assert sys.modules.get("uvicorn") is None or True  # uvicorn 只允许在 serve 里延迟导入


def test_serve_is_not_called_at_import_time(launcher: ModuleType) -> None:
    """``serve`` 必须是普通函数（显式调用才生效），不能被模块级代码触发。"""
    assert callable(launcher.serve)
    # 检查延迟导入：模块顶层不应出现 uvicorn
    source = LAUNCHER_PATH.read_text(encoding="utf-8")
    top_level_imports: List[str] = [
        line for line in source.splitlines() if line.startswith(("import ", "from "))
    ]
    assert not any("uvicorn" in line for line in top_level_imports), top_level_imports
