"""一键启动器的辅助模块（v1.4 T05）。

``start.bat`` / ``start.sh`` 负责「环境准备」（找 Python、建 venv、装依赖、构建前端），
把下面三件「在 shell 里写起来又长又易错」的事收敛到这里：

1. **端口探测**：默认端口被占用时向上顺延到第一个空闲端口，而不是直接失败；
2. **就绪探测**：``GET /api/health`` 返回 2xx 后再打开浏览器（**不能用 HEAD**：
   FastAPI 的 ``@app.get`` 不自动支持 HEAD，``HEAD /`` 会返回 405）；
3. **拉起 uvicorn**：以 ``backend/`` 为工作目录运行 ``app.main:app``。

顺带把 ``.env`` 真正装进进程环境。在此之前 ``.env`` 只对 ``docker compose`` 生效，
本地直接跑 uvicorn 时**完全不读**——用户在 ``.env`` 里填了 ``TIKU_ACCESS_TOKEN``
却打不开门禁，属于典型的「文档说能配、实际不生效」。

子命令::

    python scripts/launcher.py serve [--base-port N] [--host H] [--timeout S]
    python scripts/launcher.py deps  --requirements R --stamp S
    python scripts/launcher.py port  [--base N] [--span N]
    python scripts/launcher.py wait  --url URL [--timeout S]

``deps`` 也放在这里而不是留在 shell 里：判断「依赖是否已是最新」需要算哈希再比较，
在 ``cmd.exe`` 里要靠 ``for /f`` 捕获命令输出，而它对「以引号开头的命令」会剥掉首尾
引号（``"C:\\...\\python.exe" -c ...`` 直接报「不是内部或外部命令」）。把这段逻辑
交给 Python，批处理只需要看退出码。
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
"""仓库根目录（``scripts/`` 的上一级）。"""

ENV_FILE: Path = REPO_ROOT / ".env"
"""本地环境变量文件（``.gitignore`` 已忽略，由启动脚本从 ``.env.example`` 复制）。"""

BACKEND_DIR: Path = REPO_ROOT / "backend"
"""uvicorn 的工作目录，也是 ``app.main`` 的导入根。"""

HEALTH_PATH: str = "/api/health"
"""就绪探测路径。门禁开启时它仍在白名单内（``auth.PUBLIC_API_PATHS``），无需口令。"""

DEFAULT_BASE_PORT: int = 8000
"""默认监听端口（可被 ``TIKU_PORT`` 覆盖）。"""

PORT_SCAN_SPAN: int = 200
"""端口不可绑定时向上顺延的搜索范围。

取 200 而不是 10 或 50：Windows 上 Hyper-V / WSL / Docker 会**保留整段端口**
（``netsh int ipv4 show excludedportrange protocol=tcp`` 可见，本机实测
7958~8057 连续 100 个都在保留段内），默认的 8000 恰好落在里面。
范围 50 时从 8000 起永远扫不到 8058，直接「全部无法绑定」而启动失败；
200 的预算足以跨过一整段保留区间。每次 ``bind()`` 失败是毫秒级，不拖慢启动。
"""

RELATIVE_PATH_KEYS: Sequence[str] = (
    "TIKU_DATA_DIR",
    "TIKU_DB_PATH",
    "TIKU_FRONTEND_DIST",
)
"""取值是「相对路径」的键，装载 ``.env`` 时按**仓库根**解析。

为什么需要：``.env.example`` 里写的是 ``TIKU_DATA_DIR=./data``（为容器设计）。
如果按进程当前目录解析，从 ``backend/`` 启动就会得到 ``backend/data``，
悄悄建出第二份数据库——数据「丢失」但没人报错，是最难查的一类问题。
"""


def parse_env_file(path: Path) -> Dict[str, str]:
    """解析 ``KEY=VALUE`` 形式的 ``.env`` 文件。

    支持：``#`` 注释、空行、值两侧成对的单/双引号。不支持变量插值
    （本项目用不到，加了反而容易误解）。

    Args:
        path: ``.env`` 文件路径。

    Returns:
        键值对（保持文件中的出现顺序）。
    """
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def apply_env(values: Dict[str, str], root: Path = REPO_ROOT) -> List[str]:
    """把 ``.env`` 的值写进 ``os.environ``（**已存在的环境变量优先**）。

    真实环境变量优先级更高是刻意设计：运维临时 ``set TIKU_ACCESS_TOKEN=...``
    应该能覆盖 ``.env``，否则排查问题时改不动。

    Args:
        values: :func:`parse_env_file` 的结果。
        root: 相对路径键的解析基准。

    Returns:
        实际写入的键名列表（便于测试断言与日志）。
    """
    applied: List[str] = []
    for key, value in values.items():
        if key in os.environ:
            continue
        if key in RELATIVE_PATH_KEYS and value and not Path(value).is_absolute():
            value = str((root / value).resolve())
        os.environ[key] = value
        applied.append(key)
    return applied


def requirements_hash(path: Path) -> str:
    """计算 ``requirements.txt`` 的内容哈希。

    Args:
        path: 依赖清单路径。

    Returns:
        十六进制的 SHA-256。

    Raises:
        OSError: 文件不存在或不可读。
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_dependencies(
    requirements: Path, stamp: Path, python: Optional[str] = None
) -> int:
    """按内容哈希判断是否需要装依赖；需要时装一次并把哈希写回标记文件。

    判据是「``requirements.txt`` 的内容变了」而不是「venv 不存在」——否则每次启动
    都要等一遍 pip，双击启动会慢到没法用。

    Args:
        requirements: ``requirements.txt`` 路径。
        stamp: 记录上次安装所用哈希的标记文件（放在 ``.venv`` 内）。
        python: 执行 pip 的解释器路径；None 表示当前解释器。

    Returns:
        0 表示依赖已就绪，非 0 表示安装失败。
    """
    executable = python or sys.executable
    current = requirements_hash(requirements)

    if stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == current:
        print("[deps] 后端依赖已是最新（requirements.txt 未变）")
        return 0

    print("[deps] 安装后端依赖（首次运行或 requirements.txt 有变）...")
    # 升级 pip 失败不致命（离线/受限网络下很常见），继续尝试装依赖
    subprocess.run(
        [executable, "-m", "pip", "install", "--upgrade", "pip"],
        check=False,
        stdout=subprocess.DEVNULL,
    )
    completed = subprocess.run(
        [executable, "-m", "pip", "install", "-r", str(requirements)], check=False
    )
    if completed.returncode != 0:
        print(f"[deps] pip install 失败（退出码 {completed.returncode}）", file=sys.stderr)
        return completed.returncode or 1

    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(current, encoding="utf-8")
    return 0


def _configure_probe_socket(probe: socket.socket) -> None:
    """让探测套接字的绑定语义与「uvicorn 真正 bind」一致。

    **Windows 上绝不能开 ``SO_REUSEADDR``**：它的语义与 POSIX 相反，允许抢占
    已被监听的端口。实测（占用方都是真实进程，探测 socket 开 REUSEADDR）：

    ==========================  ==========================  ==========================
    占用方                       探测结果                    后果
    ==========================  ==========================  ==========================
    普通 socket（未开 REUSEADDR） ``WinError 10013``           误报「被系统保留」
    开了 ``SO_REUSEADDR``         ``bind() 成功``             误判空闲 → uvicorn 10048 崩
    ==========================  ==========================  ==========================

    也就是说：开着 REUSEADDR 时 ``10048`` 永远不可能返回，「被占用」这个分支
    在生产路径上是死代码。改用 ``SO_EXCLUSIVEADDRUSE`` 后两种情况都稳定返回
    ``WinError 10048``，与 asyncio 在 Windows 上的行为一致
    （``reuse_address`` 默认仅在 POSIX 为真，所以 uvicorn 的 bind 也是这个语义）。

    POSIX 保留 ``SO_REUSEADDR``：它只允许绑定 ``TIME_WAIT`` 状态的端口，
    不会抢占正在 LISTEN 的端口，与 uvicorn 的 ``reuse_address=True`` 一致。

    Args:
        probe: 待配置的探测套接字。
    """
    if sys.platform.startswith("win"):
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            probe.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        return
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)


def probe_port(port: int, host: str = "127.0.0.1") -> Optional[OSError]:
    """尝试绑定端口，返回失败原因（成功时返回 None）。

    保留 ``OSError`` 而不是只返回布尔值，是为了让调用方能区分「被别的程序占用」
    与「被系统保留（``WinError 10013``）」——两者的处置建议完全不同。

    为什么不用 ``connect_ex()`` 二次确认：实测在 Windows 上，对一个**刚被自己
    bind 过又关闭**的端口做 ``connect`` 会返回 0（幽灵连接），于是每个空闲端口
    都被判成「有服务在听」。``SO_EXCLUSIVEADDRUSE`` 已经足够区分三种情况
    （见 :func:`_configure_probe_socket` 的矩阵），不要再叠加连接探测。

    Args:
        port: 待检测端口。
        host: 监听地址。

    Returns:
        绑定失败的异常；端口可用时返回 None。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        _configure_probe_socket(probe)
        try:
            probe.bind((host, port))
        except OSError as exc:
            return exc
    return None


def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    """判断端口能否被绑定。

    不能只用 ``connect_ex()``：端口上没有服务在听，也可能因为被其他进程以
    通配地址占用、或落在系统保留段而无法绑定，``connect`` 会误判为「空闲」，
    启动时才炸。反过来也不能只用 ``bind()``，理由见 :func:`probe_port`。

    Args:
        port: 待检测端口。
        host: 监听地址。

    Returns:
        True 表示可以绑定。
    """
    return probe_port(port, host) is None


def is_reserved_port_error(error: Optional[OSError]) -> bool:
    """判断绑定失败是否属于「端口被系统保留 / 权限不允许」。

    这类失败与「端口已被占用」不同：换一个端口就能解决，重试同一个端口永远不行。
    Windows 上是 ``WinError 10013``（WSAEACCES），POSIX 上是 ``EACCES``/``EPERM``。

    Args:
        error: :func:`probe_port` 返回的异常。

    Returns:
        True 表示是权限/保留类错误。
    """
    if error is None:
        return False
    if getattr(error, "winerror", None) == 10013:
        return True
    return error.errno in (1, 13)


def describe_port_failure(error: Optional[OSError]) -> str:
    """把端口绑定失败翻译成一句「用户能据此行动」的中文说明。

    「被别的程序占用」与「被系统保留」在 ``OSError`` 上是两个不同的错误码，
    处置方式也完全不同（前者关掉那个程序，后者只能换端口），所以文案必须分开。

    Args:
        error: :func:`probe_port` 返回的异常；None 表示端口可用。

    Returns:
        中文描述，如 ``被系统保留或无权限（Windows WinError 10013）``。
    """
    if error is None:
        return "可用"
    if is_reserved_port_error(error):
        winerror = getattr(error, "winerror", None)
        if winerror is not None:
            return f"被系统保留或无权限（Windows WinError {winerror}）"
        return f"无权限绑定（errno {error.errno}）"
    if getattr(error, "winerror", None) == errno.EADDRINUSE or error.errno in (
        errno.EADDRINUSE,
        48,
        98,
        10048,
    ):
        return "已被其他程序占用"
    return f"绑定失败（{error}）"


def scan_ports(
    base: int, span: int = PORT_SCAN_SPAN, host: str = "127.0.0.1"
) -> Tuple[Optional[int], List[Tuple[int, OSError]]]:
    """从 ``base`` 起向上找第一个可绑定的端口，同时带回失败记录。

    与 :func:`find_free_port` 的区别是**保留失败原因**：全段扫不到时，调用方
    需要知道是「被系统保留」还是「被别的程序占用」才能给出正确提示。

    Args:
        base: 起始端口。
        span: 最多尝试多少个连续端口。
        host: 监听地址。

    Returns:
        ``(可用端口 or None, [(端口, 失败异常), ...])``。找到可用端口时，
        失败列表只包含它之前的那些端口。
    """
    failures: List[Tuple[int, OSError]] = []
    for offset in range(span):
        candidate = base + offset
        if candidate > 65535:
            break
        error = probe_port(candidate, host)
        if error is None:
            return candidate, failures
        failures.append((candidate, error))
    return None, failures


def find_free_port(
    base: int, span: int = PORT_SCAN_SPAN, host: str = "127.0.0.1"
) -> Optional[int]:
    """从 ``base`` 起向上找第一个可绑定的端口。

    Args:
        base: 起始端口。
        span: 最多尝试多少个连续端口。
        host: 监听地址。

    Returns:
        可用端口；``base..base+span-1`` 全不可绑定时返回 None。
    """
    free_port, _failures = scan_ports(base, span, host)
    return free_port


def wait_for_health(url: str, timeout: float = 60.0, interval: float = 0.5) -> bool:
    """轮询健康检查直到成功或超时。

    Args:
        url: 健康检查完整地址（如 ``http://127.0.0.1:8000/api/health``）。
        timeout: 最长等待秒数。
        interval: 两次探测的间隔秒数。

    Returns:
        True 表示拿到了 2xx 响应。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 300:
                    return True
        except (urllib.error.URLError, OSError):
            # 服务还没起来：ConnectionRefused 是最常见的一种，继续等
            pass
        time.sleep(interval)
    return False


def open_browser(url: str) -> bool:
    """用系统默认浏览器打开地址；失败静默返回 False。

    Args:
        url: 要打开的地址。

    Returns:
        True 表示已交给系统处理。
    """
    try:
        if sys.platform.startswith("win"):
            os.startfile(url)  # type: ignore[attr-defined]  # 仅 Windows 存在
            return True
        if sys.platform == "darwin":
            subprocess.run(
                ["open", url], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return True
        subprocess.run(
            ["xdg-open", url],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except OSError:
        # 无图形环境 / 没有默认浏览器：不算错误，提示用户手动访问即可
        return False


def resolve_base_port(explicit: Optional[int], values: Dict[str, str]) -> int:
    """确定起始端口：命令行 > ``.env`` 的 ``TIKU_PORT`` > 默认 8000。

    Args:
        explicit: ``--base-port`` 的取值。
        values: ``.env`` 解析结果。

    Returns:
        起始端口号。
    """
    return resolve_requested_port(explicit, values)[0]


def resolve_requested_port(
    explicit: Optional[int], values: Dict[str, str]
) -> Tuple[int, Optional[str]]:
    """确定起始端口，并说明这个端口是谁指定的。

    知道「端口是用户指定的还是默认的」才能给出对的提示：用户明确写了
    ``TIKU_PORT=8000`` 而实际跑在 8058 上，必须告诉他换了；默认值顺延则
    一句话带过即可。

    Args:
        explicit: ``--base-port`` 的取值。
        values: ``.env`` 解析结果。

    Returns:
        ``(端口, 来源)``；来源为 ``None`` 表示用的是默认值。
    """
    if explicit is not None:
        return explicit, "命令行 --base-port"
    raw = values.get("TIKU_PORT") or os.environ.get("TIKU_PORT") or ""
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_BASE_PORT, None
    if not 0 < port <= 65535:
        return DEFAULT_BASE_PORT, None
    if port == DEFAULT_BASE_PORT:
        # ``.env.example`` 出厂就是 ``TIKU_PORT=8000``：新用户从没「指定」过
        # 端口，对他说「你指定的端口 8000 不可用」只会让人困惑。值等于默认
        # 端口时按默认处理，走轻量提示。
        return port, None
    return port, "TIKU_PORT"


def summarize_port_failures(failures: List[Tuple[int, OSError]]) -> str:
    """把一批端口失败记录汇总成一句话，例如「58 个被系统保留，1 个已被其他程序占用」。

    只报「起始端口为什么不可用」是不够的：起始端口落在 Windows 保留段时，
    用户看到的是「被系统保留」，而真正让服务换到 8059 的原因可能是 8058
    被别的程序占了。两类原因必须同时说清楚，否则用户排查方向还是错的。

    Args:
        failures: :func:`scan_ports` 返回的失败列表。

    Returns:
        汇总描述。
    """
    if not failures:
        return "无"
    reserved = sum(1 for _port, error in failures if is_reserved_port_error(error))
    busy = len(failures) - reserved
    parts: List[str] = []
    if reserved:
        parts.append(f"{reserved} 个被系统保留")
    if busy:
        parts.append(f"{busy} 个已被其他程序占用")
    return "，".join(parts) or "原因未知"


def _report_no_free_port(base: int, failures: List[Tuple[int, OSError]]) -> None:
    """整段端口都不可绑定时，打印**可操作**的排查提示。

    旧版只丢一句「全部被占用」，把「系统保留」误报成「被占用」，用户照着
    关程序永远解决不了。这里按错误码分类统计，并给出三条具体出路。

    Args:
        base: 起始端口。
        failures: :func:`scan_ports` 返回的失败列表。
    """
    last = min(base + PORT_SCAN_SPAN - 1, 65535)
    reserved = [port for port, error in failures if is_reserved_port_error(error)]
    busy = [port for port, error in failures if not is_reserved_port_error(error)]

    print(
        f"[错误] {base}~{last} 共 {len(failures)} 个端口都无法绑定，找不到可用端口",
        file=sys.stderr,
    )
    if reserved:
        print(
            f"       其中 {len(reserved)} 个被系统保留或无权限（例如 {reserved[0]}，"
            f"Windows 上为 WinError 10013），常见于 Hyper-V / WSL / Docker 整段占位",
            file=sys.stderr,
        )
    if busy:
        print(
            f"       其中 {len(busy)} 个已被其他程序占用（例如 {busy[0]}）",
            file=sys.stderr,
        )
    print("       可以这样解决：", file=sys.stderr)
    print(
        "       ① 指定端口：编辑 .env 的 TIKU_PORT=<端口>，"
        "或 python scripts/launcher.py serve --base-port 9000",
        file=sys.stderr,
    )
    print(
        "       ② 查看保留段：Windows 上执行 "
        "netsh int ipv4 show excludedportrange protocol=tcp",
        file=sys.stderr,
    )
    print(f"       ③ 换一个起始端口重试（已尝试 {base}~{last}）", file=sys.stderr)


def serve(base_port: Optional[int] = None, host: str = "127.0.0.1", timeout: float = 60.0) -> int:
    """装载 ``.env``、找端口、拉起 uvicorn，并在就绪后打开浏览器。

    Args:
        base_port: 起始端口；None 表示按 :func:`resolve_base_port` 推断。
        host: 监听地址。
        timeout: 就绪探测的最长等待秒数。

    Returns:
        进程退出码（0 表示正常退出）。
    """
    values = parse_env_file(ENV_FILE)
    applied = apply_env(values)
    if applied:
        print(f"[env] 已从 .env 装载 {len(applied)} 个变量：{', '.join(sorted(applied))}")

    requested_port, source = resolve_requested_port(base_port, values)
    free_port, failures = scan_ports(requested_port, host=host)
    if free_port is None:
        _report_no_free_port(requested_port, failures)
        return 1
    if free_port != requested_port:
        prefix = (
            f"你指定的端口 {requested_port}（来自 {source}）"
            if source
            else f"端口 {requested_port}"
        )
        print(f"[提示] {prefix} 不可用：{describe_port_failure(probe_port(requested_port, host))}")
        if failures:
            first, last = failures[0][0], failures[-1][0]
            print(
                f"       {first}~{last} 共 {len(failures)} 个端口不可用"
                f"（{summarize_port_failures(failures)}），已改用 {free_port}"
            )
        else:
            print(f"       已改用 {free_port}")
    port = free_port

    url = f"http://{host}:{port}"
    print(f"[启动] {url}（Ctrl+C 停止）")

    def _watch() -> None:
        """后台线程：等服务就绪后打开浏览器。"""
        if wait_for_health(f"{url}{HEALTH_PATH}", timeout=timeout):
            print(f"[就绪] {url}")
            if not open_browser(url):
                print("[提示] 未能自动打开浏览器，请手动访问上面的地址")
        else:
            print(f"[警告] {timeout:.0f} 秒内健康检查未通过，请检查上方日志", file=sys.stderr)

    threading.Thread(target=_watch, name="readiness-watch", daemon=True).start()

    # 切到 backend/ 让 uvicorn 能按字符串导入 "app.main:app"
    os.chdir(BACKEND_DIR)
    # chdir **不会**改变 sys.path：以 ``python scripts/launcher.py serve`` 启动时
    # sys.path[0] 是 scripts/，uvicorn 解析 "app.main:app" 会抛
    # ModuleNotFoundError: No module named 'app'。必须显式把 backend/ 放进去。
    backend_path = str(BACKEND_DIR)
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)
    import uvicorn  # 延迟导入：只有 serve 子命令才需要

    uvicorn.run("app.main:app", host=host, port=port, log_level="info")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""
    parser = argparse.ArgumentParser(
        prog="launcher.py",
        description="私有护理题库系统一键启动器辅助工具",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="装载 .env 并启动 uvicorn")
    serve_parser.add_argument("--base-port", type=int, default=None, help="起始端口")
    serve_parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    serve_parser.add_argument("--timeout", type=float, default=60.0, help="就绪等待秒数")

    deps_parser = subparsers.add_parser("deps", help="按哈希判断并安装后端依赖")
    deps_parser.add_argument("--requirements", required=True, help="requirements.txt 路径")
    deps_parser.add_argument("--stamp", required=True, help="哈希标记文件路径")

    port_parser = subparsers.add_parser("port", help="打印第一个空闲端口")
    port_parser.add_argument("--base", type=int, default=DEFAULT_BASE_PORT, help="起始端口")
    port_parser.add_argument("--span", type=int, default=PORT_SCAN_SPAN, help="搜索范围")
    port_parser.add_argument("--host", default="127.0.0.1", help="监听地址")

    wait_parser = subparsers.add_parser("wait", help="等待健康检查通过")
    wait_parser.add_argument("--url", required=True, help="健康检查地址")
    wait_parser.add_argument("--timeout", type=float, default=60.0, help="最长等待秒数")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """命令行入口。

    Args:
        argv: 参数列表；None 表示取 ``sys.argv[1:]``。

    Returns:
        进程退出码。
    """
    args = build_parser().parse_args(argv)

    if args.command == "serve":
        return serve(base_port=args.base_port, host=args.host, timeout=args.timeout)

    if args.command == "deps":
        return ensure_dependencies(Path(args.requirements), Path(args.stamp))

    if args.command == "port":
        port = find_free_port(args.base, span=args.span, host=args.host)
        if port is None:
            print(
                f"[错误] {args.base}~{args.base + args.span - 1} 全部无法绑定",
                file=sys.stderr,
            )
            return 1
        print(port)
        return 0

    if wait_for_health(args.url, timeout=args.timeout):
        print("[就绪]")
        return 0
    print(f"[错误] {args.url} 在 {args.timeout:.0f} 秒内未就绪", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
