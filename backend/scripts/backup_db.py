"""题库数据备份工具：在线备份 SQLite 主库，并提供校验 / 列举 / 清理能力。

这是一个**零依赖、单文件**工具，只 import Python 标准库，不引用项目内任何
``app.*`` 模块，也不依赖第三方包。因此可以单独复制到 fnOS / NAS 的部署目录
（那里只有 compose 文件、没有源码）直接运行::

    python3 backup_db.py --data-dir ./data --backup-dir ../backups

设计要点：

* WAL 模式下直接 ``shutil.copy2`` 主库会拿到不一致快照，统一改用
  ``sqlite3.Connection.backup()`` 在线备份；
* 源库以只读 URI ``file:...?mode=ro`` 打开，避免对线上库产生写入副作用；
* 备份目录默认落在 ``data/`` **之外**：``data/`` 是容器挂载卷，与其同卷等于没备份；
* ``--prune --keep N`` 是**备份成功后的附加清理开关**，不是独立动作：
  先完成备份并通过 ``PRAGMA integrity_check``，才按 N 清理最旧备份；
  备份失败一律不清理，避免源库损坏/磁盘满时误删仅存的历史备份；
* 删除只删符合本工具命名规则的文件，绝不触碰备份目录里用户放的其它文件。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

# 本工具生成的备份文件命名：tiku-YYYYmmdd-HHMMSS.db，同秒重复时追加 -2 / -3 …
BACKUP_NAME_RE: re.Pattern[str] = re.compile(
    r"^tiku-(\d{8})-(\d{6})(?:-(\d+))?\.db$"
)
"""匹配本工具产出的备份文件名；``--list`` / ``--prune`` 只认这个规则。"""

KEY_TABLES: tuple[str, ...] = ("questions", "import_files")
"""``--verify`` 摘要里要报告行数的关键表；不存在时优雅跳过。"""

DB_FILENAME: str = "tiku.db"
"""主库文件名，与 ``app/config.py::DB_PATH`` 的口径一致。"""

_CURRENT_ARGV: list[str] = []
"""本次调用的参数快照，供 ``ArgumentParser.error()`` 判断是否处于 --json 模式。"""


class BackupArgumentParser(argparse.ArgumentParser):
    """自定义解析器：保证 ``--json`` 下**所有** argparse 阶段错误也输出纯 JSON。

    ``parse_args()`` 的错误（未知参数、互斥冲突、缺少值等）发生在 ``main()``
    的 try 之前；argparse 默认只往 stderr 打英文 usage、stdout 为空，会让
    「stdout 只有 JSON」的契约被破坏（监控解析空串后静默失败）。
    """

    def error(self, message: str) -> NoReturn:
        """把参数错误统一转成中文可读错误；``--json`` 时输出固定字段纯 JSON。

        Args:
            message: argparse 给出的原始错误描述。

        Returns:
            永不返回：总是以退出码 2 结束进程。
        """
        readable = f"错误：参数错误：{message}"
        if "--json" in _CURRENT_ARGV:
            result = _result("parse", False, readable)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            self.exit(2)
        else:
            self.exit(2, f"{readable}\n（运行 --help 查看用法）\n")


def parse_keep_value(raw: str) -> int:
    """把 ``--keep`` 的原始字符串解析为正整数。

    校验刻意放在 ``main()`` 的 ``_validate()`` 阶段，而不是 argparse 的 ``type``：
    这样 ``--keep 0`` / ``--keep abc`` 在 ``--json`` 模式下也走统一错误路径，
    stdout 仍是纯 JSON（定时任务 / 监控可正常解析），而不是空 stdout + 英文 usage。

    Args:
        raw: 命令行传入的原始字符串。

    Returns:
        解析出的正整数。

    Raises:
        ValueError: 不是整数或不是正数（中文消息，由 main 统一兜底）。
    """
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"--keep 必须是正整数，收到：{raw!r}") from exc
    if value <= 0:
        raise ValueError(f"--keep 必须是正整数，收到：{raw!r}")
    return value


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器（帮助信息全中文）。

    ``--verify`` / ``--list`` 互斥；都不给时执行备份。``--prune`` 在互斥组之外，
    是「备份成功后的附加清理开关」，因此一次调用即可完成「备份 + 保留最近 N 份」。

    Returns:
        配置好的 ``BackupArgumentParser``。
    """
    parser = BackupArgumentParser(
        prog="backup_db.py",
        description="私有题库数据备份工具（零依赖单文件，可单独复制到 NAS 部署目录运行）。",
        epilog=(
            "常用示例：\n"
            "  python backup_db.py                                  # 默认：在线备份 + 完整性校验\n"
            "  python backup_db.py --include-uploads                # 同时复制 data/uploads/\n"
            "  python backup_db.py --verify backups/tiku-20260101-020000.db\n"
            "  python backup_db.py --list                           # 按时间倒序列出备份\n"
            "  python backup_db.py --prune --keep 7                 # 备份后保留最近 7 份\n"
            "  python backup_db.py --json                           # 机器可读输出\n"
            "\n"
            "默认数据目录取环境变量 TIKU_DATA_DIR，否则为 <仓库根>/data；\n"
            "默认备份目录为数据目录的兄弟目录 backups/（与 data/ 不同位置）。\n"
            "单独复制到 NAS / 脱离项目目录运行时，必须显式传 --data-dir，\n"
            "否则默认数据目录会按脚本所在位置推导而指错位置。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="数据目录（内含 tiku.db 与 uploads/）；默认取 TIKU_DATA_DIR，否则 <仓库根>/data",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="备份输出目录；默认 <data-dir 的父目录>/backups，切勿放在 data/ 内部",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--verify",
        type=Path,
        default=None,
        metavar="DB_FILE",
        help="校验一个已有备份：完整性 + 关键表行数，不做备份",
    )
    action.add_argument(
        "--list",
        action="store_true",
        help="列出备份目录里匹配命名的备份（按时间倒序，显示大小与修改时间）",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="备份成功后保留最近 N 份（必须与 --keep N 同用）；只删匹配命名的文件",
    )
    parser.add_argument(
        "--keep",
        type=str,
        default=None,
        metavar="N",
        help="--prune 时保留最近 N 份，N 必须为正整数（校验在 main 里，--json 下错误也是 JSON）",
    )
    parser.add_argument(
        "--include-uploads",
        action="store_true",
        help="备份时把 data/uploads/ 复制到 backups/uploads-<同一次时间戳>/",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 输出机器可读结果（stdout 只有 JSON，供定时任务 / 监控消费）",
    )
    return parser


def default_data_dir() -> Path:
    """返回默认数据目录。

    优先取环境变量 ``TIKU_DATA_DIR``（与 ``app/config.py`` 同口径）；
    否则本文件位于 ``<仓库根>/backend/scripts/backup_db.py``，
    ``parents[2]`` 即 ``<仓库根>``，数据目录为 ``<仓库根>/data``。

    注意：脚本被单独复制到 NAS 部署目录后，这个推导不再指向正确位置，
    必须显式传 ``--data-dir``（见 ``--help`` epilog 与 README §10.4）。

    Returns:
        绝对路径形式的数据目录。
    """
    env_value = os.getenv("TIKU_DATA_DIR")
    if env_value:
        return Path(env_value).expanduser().resolve()
    return (Path(__file__).resolve().parents[2] / "data").resolve()


def default_backup_dir(data_dir: Path) -> Path:
    """返回默认备份目录：数据目录的**兄弟目录** ``backups/``。

    备份绝不能默认落在 ``data/`` 内部 —— 那是容器挂载卷，与数据同卷存放
    等于没有备份（卷被删或被重置时备份会一起没）。

    Args:
        data_dir: 已解析的数据目录。

    Returns:
        备份目录绝对路径。
    """
    return (data_dir.parent / "backups").resolve()


def _action_of(args: argparse.Namespace) -> str:
    """根据参数判断本次动作名（用于结果字典与错误归类）。"""
    if args.verify is not None:
        return "verify"
    if args.list:
        return "list"
    if args.prune:
        return "backup+prune"
    return "backup"


def _result(
    action: str,
    ok: bool,
    message: str,
    *,
    backup_file: str | None = None,
    size_bytes: int | None = None,
    integrity: str | None = None,
) -> dict[str, Any]:
    """构造统一的结果字典，保证 ``--json`` 的固定字段始终存在。

    Args:
        action: 动作名（backup / backup+prune / verify / list / parse）。
        ok: 是否成功。
        message: 可读的中文摘要。
        backup_file: 本次涉及的主备份文件路径。
        size_bytes: 该文件大小（字节）。
        integrity: ``PRAGMA integrity_check`` 的结果文本。

    Returns:
        至少含 ``ok`` / ``action`` / ``backup_file`` / ``size_bytes`` /
        ``integrity`` / ``message`` 的字典。
    """
    return {
        "ok": ok,
        "action": action,
        "backup_file": backup_file,
        "size_bytes": size_bytes,
        "integrity": integrity,
        "message": message,
    }


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    """以只读 URI 打开数据库，避免对线上库产生任何写入副作用。

    使用 ``Path.as_uri()`` 让中文 / 空格路径按 percent-encoding 传给 SQLite，
    再追加 ``?mode=ro``；非绝对路径时回退到普通 URI 写法。

    Args:
        db_path: 数据库文件路径。

    Returns:
        只读连接。调用方负责 ``close()``。
    """
    try:
        uri = db_path.resolve().as_uri() + "?mode=ro"
    except ValueError:
        uri = f"file:{db_path.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _integrity(conn: sqlite3.Connection) -> tuple[bool, str]:
    """执行 ``PRAGMA integrity_check`` 并归一成 ``(是否通过, 文本)``。"""
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    text = "; ".join(str(row[0]) for row in rows) if rows else "无结果"
    return text.strip().lower() == "ok", text


def check_integrity(db_path: Path) -> tuple[bool, str]:
    """以只读方式打开一个数据库文件并执行完整性检查。

    Args:
        db_path: 待检查的数据库文件。

    Returns:
        ``(是否通过, 检查文本)``；通过时文本为 ``"ok"``。
    """
    conn = _open_readonly(db_path)
    try:
        return _integrity(conn)
    finally:
        conn.close()


def normalize_backup_journal(target: sqlite3.Connection) -> str:
    """在备份连接 **close 之前**把产物切成 DELETE 模式，成为自包含单文件。

    ``Connection.backup()`` 会连源库的 WAL 文件头一起复制；若保持 WAL 模式，
    随后以只读方式打开校验时 SQLite 会在备份目录里生成 ``-wal`` / ``-shm`` 伴生文件。
    切成 DELETE 后 SQLite 自动 checkpoint 并移除伴生文件，产物只剩一个 ``.db``。
    本函数不删除任何文件，只改产物自身的日志模式；**切换失败只提示、不影响备份成功**。

    Args:
        target: 备份目标库的可写连接（尚未 close）。

    Returns:
        人类可读的产物形态说明；失败时返回以「提示：」开头的告警文本。
    """
    try:
        mode = str(target.execute("PRAGMA journal_mode=DELETE").fetchone()[0]).lower()
    except sqlite3.Error as exc:
        return f"提示：备份日志模式切换失败（{exc}），产物可能仍为 WAL 模式"
    if mode != "delete":
        return f"提示：备份日志模式切换后仍为 {mode!r}，请检查产物"
    return "产物形态：自包含单文件（journal_mode=DELETE，无 -wal / -shm 伴生文件）"


def _stamp_key(name: str) -> tuple[str, str, int] | None:
    """从备份文件名解析排序键；不符合命名规则返回 ``None``。"""
    matched = BACKUP_NAME_RE.match(name)
    if matched is None:
        return None
    return matched.group(1), matched.group(2), int(matched.group(3) or 0)


def list_backups(backup_dir: Path) -> list[Path]:
    """列出备份目录里匹配命名规则的备份，按时间**倒序**（最新在前）。

    只认 ``tiku-YYYYmmdd-HHMMSS.db`` 及其 ``-N`` 后缀变体；
    目录里其它任何文件都不进入结果，因此不会被 ``--prune`` 误删。

    Args:
        backup_dir: 备份目录。

    Returns:
        排序后的备份文件路径列表；目录不存在时为空列表。
    """
    if not backup_dir.is_dir():
        return []
    found: list[tuple[tuple[str, str, int], Path]] = []
    for entry in backup_dir.iterdir():
        if not entry.is_file():
            continue
        key = _stamp_key(entry.name)
        if key is None:
            continue
        found.append((key, entry))
    found.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in found]


def make_stamp(backup_dir: Path) -> str:
    """生成同秒内也不会覆盖已有备份的时间戳（本地时间）。

    同一秒重复执行时依次追加 ``-2`` / ``-3`` …，绝不覆盖已有备份。

    Args:
        backup_dir: 备份目录，用于探测同名文件是否已存在。

    Returns:
        形如 ``20260101-020000`` 或 ``20260101-020000-2`` 的时间戳。
    """
    base = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = base
    suffix = 2
    while (backup_dir / f"tiku-{candidate}.db").exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def do_backup(
    args: argparse.Namespace, data_dir: Path, backup_dir: Path
) -> tuple[dict[str, Any], list[str]]:
    """执行一次在线备份，并立即校验备份产物。

    Args:
        args: 已解析的命令行参数。
        data_dir: 数据目录。
        backup_dir: 备份输出目录。

    Returns:
        ``(结果字典, 人类可读输出行)``。``ok`` 为 False 时调用方**不得**清理。
    """
    source_db = data_dir / DB_FILENAME
    if not source_db.is_file():
        message = f"错误：源数据库不存在：{source_db}（可用 --data-dir 指定数据目录）"
        return _result("backup", False, message), [message]
    if source_db.stat().st_size == 0:
        message = f"错误：源数据库是空文件，无法备份：{source_db}"
        return _result("backup", False, message), [message]

    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        message = f"错误：无法创建备份目录 {backup_dir}：{exc}"
        return _result("backup", False, message), [message]

    stamp = make_stamp(backup_dir)
    target_db = backup_dir / f"tiku-{stamp}.db"
    lines: list[str] = [
        f"源数据库：{source_db}",
        f"备份目录：{backup_dir}（在 data/ 之外，未放入容器挂载卷）",
    ]

    # 目标路径已由 make_stamp 确认不存在；失败时清理它不会删到任何用户文件。
    failure: str | None = None
    journal_note = "产物形态：未整理日志模式"
    source: sqlite3.Connection | None = None
    target: sqlite3.Connection | None = None
    try:
        source = _open_readonly(source_db)
        target = sqlite3.connect(str(target_db))
        source.backup(target)
        # 必须在 target.close() 之前切模式，避免只读校验时再生成 WAL 伴生文件。
        journal_note = normalize_backup_journal(target)
    except (sqlite3.Error, OSError) as exc:
        failure = f"SQLite 在线备份失败：{exc}"
    finally:
        # sqlite3.Connection 的 with 只管事务、不关连接，必须显式 close。
        if target is not None:
            target.close()
        if source is not None:
            source.close()
    if failure is not None:
        if target_db.exists():
            try:
                target_db.unlink()
            except OSError:
                pass
        message = f"错误：{failure}"
        return _result("backup", False, message), lines + [message]

    # 备份完成后立即校验，避免「备份出来就是坏的」这种静默失败。
    try:
        integrity_ok, integrity = check_integrity(target_db)
    except (sqlite3.Error, OSError) as exc:
        message = f"错误：备份已生成但完整性校验无法执行：{exc}"
        result = _result(
            "backup", False, message,
            backup_file=str(target_db), size_bytes=target_db.stat().st_size,
        )
        return result, lines + [f"备份文件：{target_db}", message]

    size_bytes = target_db.stat().st_size
    lines.extend([
        f"备份文件：{target_db}",
        f"大小：{size_bytes} 字节",
        journal_note,
        f"完整性校验（PRAGMA integrity_check）：{integrity}",
    ])

    uploads_dir: Path | None = None
    uploads_files = 0
    uploads_note = "未启用（未指定 --include-uploads）"
    if args.include_uploads:
        uploads_source = data_dir / "uploads"
        if not uploads_source.is_dir():
            uploads_note = f"提示：未找到上传目录 {uploads_source}，已跳过（不算失败）"
            lines.append(uploads_note)
        else:
            uploads_dir = backup_dir / f"uploads-{stamp}"
            try:
                shutil.copytree(uploads_source, uploads_dir, dirs_exist_ok=True)
            except (OSError, shutil.Error) as exc:
                message = (
                    f"错误：复制上传目录失败：{exc}"
                    f"（数据库备份本身已完成：{target_db}）"
                )
                result = _result(
                    "backup", False, message,
                    backup_file=str(target_db), size_bytes=size_bytes,
                    integrity=integrity,
                )
                return result, lines + [message]
            uploads_files = sum(1 for item in uploads_dir.rglob("*") if item.is_file())
            uploads_note = f"已复制 {uploads_files} 个文件 -> {uploads_dir}"
            lines.append(
                f"上传文件：{uploads_source} -> {uploads_dir}（{uploads_files} 个文件）"
            )

    conclusion = "成功" if integrity_ok else "完成但完整性校验未通过"
    message = (
        f"备份{conclusion}：{target_db.name}，{size_bytes} 字节，"
        f"integrity_check={integrity}；{uploads_note}；{journal_note}"
    )
    lines.append(f"结论：{message}")
    result = _result(
        "backup", integrity_ok, message,
        backup_file=str(target_db), size_bytes=size_bytes, integrity=integrity,
    )
    result["source_db"] = str(source_db)
    result["uploads_dir"] = str(uploads_dir) if uploads_dir is not None else None
    result["uploads_files"] = uploads_files
    result["include_uploads"] = bool(args.include_uploads)
    return result, lines


def do_verify(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    """校验一个已有备份：能打开、完整性通过、列出关键表及行数。

    Args:
        args: 已解析的命令行参数，``args.verify`` 为待校验文件路径。

    Returns:
        ``(结果字典, 人类可读输出行)``。
    """
    target: Path = args.verify
    if not target.is_file():
        message = f"错误：待校验文件不存在：{target}"
        return _result("verify", False, message), [message]
    size_bytes = target.stat().st_size
    if size_bytes == 0:
        message = f"错误：待校验文件是空文件，不是有效 SQLite 数据库：{target}"
        return _result("verify", False, message), [message]

    lines = [f"校验文件：{target}", f"大小：{size_bytes} 字节"]
    conn: sqlite3.Connection | None = None
    try:
        conn = _open_readonly(target)
        integrity_ok, integrity = _integrity(conn)
        existing = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        tables: list[dict[str, Any]] = []
        for name in KEY_TABLES:
            if name in existing:
                count = int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
                tables.append({"table": name, "rows": count})
            else:
                # 表不存在（例如只备份了空库）时优雅跳过，不算失败。
                tables.append({"table": name, "rows": None})
    except sqlite3.Error as exc:
        message = f"错误：无法作为 SQLite 数据库打开或校验失败：{target}（{exc}）"
        result = _result(
            "verify", False, message,
            backup_file=str(target), size_bytes=size_bytes,
        )
        return result, lines + [message]
    finally:
        if conn is not None:
            conn.close()

    lines.append(f"完整性校验（PRAGMA integrity_check）：{integrity}")
    lines.append("关键表行数：")
    for item in tables:
        if item["rows"] is None:
            lines.append(f"  {item['table']}：不存在（已跳过）")
        else:
            lines.append(f"  {item['table']}：{item['rows']} 行")
    conclusion = "校验通过" if integrity_ok else "校验未通过"
    lines.append(f"结论：{conclusion}")
    message = (
        f"{conclusion}：{target.name}，integrity_check={integrity}，"
        f"大小 {size_bytes} 字节"
    )
    result = _result(
        "verify", integrity_ok, message,
        backup_file=str(target), size_bytes=size_bytes, integrity=integrity,
    )
    result["tables"] = tables
    return result, lines


def do_list(backup_dir: Path) -> tuple[dict[str, Any], list[str]]:
    """列出备份目录里的备份（按时间倒序，含文件名 / 大小 / mtime）。"""
    items = list_backups(backup_dir)
    details: list[dict[str, Any]] = []
    for path in items:
        stat = path.stat()
        details.append({
            "file": path.name,
            "size_bytes": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    lines = [f"备份目录：{backup_dir}", f"共找到 {len(items)} 份备份（按时间倒序）："]
    if not items:
        lines.append("  （空：目录下没有匹配 tiku-YYYYmmdd-HHMMSS.db 命名的备份）")
    for item in details:
        lines.append(
            f"  {item['file']}  {item['size_bytes']} 字节  修改于 {item['mtime']}"
        )
    note = "" if backup_dir.is_dir() else "（备份目录尚不存在）"
    message = f"共找到 {len(items)} 份备份{note}"
    result = _result("list", True, message)
    result["backup_dir"] = str(backup_dir)
    result["backups"] = details
    return result, lines


def do_prune(
    args: argparse.Namespace,
    backup_dir: Path,
    base: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """备份**成功后**的附加清理：把结果合并进 ``base``，输出单个复合结果。

    调用前必须已完成 ``do_backup()`` 且 ``base["ok"]`` 为 True。
    ``list_backups()`` 在这里才执行，因此当次新备份已计入「最近 N 份」；
    若先列举再备份会多留一份、甚至误删次新备份。
    只删匹配命名规则的文件，目录里其它文件一律不触碰。

    Args:
        args: 已解析参数（``args.keep`` 已由 ``_validate`` 转成 int）。
        backup_dir: 备份目录。
        base: ``do_backup()`` 的成功结果，作为复合结果的顶层基础。

    Returns:
        ``(复合结果字典, 清理部分的人类可读输出行)``。
    """
    keep_count: int = args.keep
    items = list_backups(backup_dir)
    kept = items[:keep_count]
    removed = items[keep_count:]
    lines = [
        f"清理阶段（备份已成功）：匹配命名的备份共 {len(items)} 份；"
        f"保留最近 {keep_count} 份（含本次新备份），待删除 {len(removed)} 份。",
        "保留清单：",
    ]
    if kept:
        for path in kept:
            lines.append(f"  保留  {path.name}")
    else:
        lines.append("  （无）")
    lines.append("待删除清单（仅删除匹配 tiku-YYYYmmdd-HHMMSS.db 命名的文件）：")
    if removed:
        for path in removed:
            lines.append(f"  待删  {path.name}")
    else:
        lines.append("  （无，无需清理）")

    deleted: list[str] = []
    prune_ok = True
    for path in removed:
        try:
            path.unlink()
        except OSError as exc:
            prune_ok = False
            lines.append(f"  错误：删除备份失败 {path.name}：{exc}")
            break
        deleted.append(path.name)
        lines.append(f"  已删除：{path.name}")
    lines.append("说明：不匹配命名的文件（如 keep-me.txt / other.db）一律不触碰。")

    prune_message = (
        f"清理{'完成' if prune_ok else '未全部完成'}：保留 {len(kept)} 份，"
        f"删除 {len(deleted)} 份，其它文件未触碰"
    )
    lines.append(f"结论：{prune_message}")

    result = dict(base)
    result["action"] = "backup+prune"
    result["ok"] = bool(base.get("ok")) and prune_ok
    result["message"] = f"{base['message']}；{prune_message}"
    result["backup"] = {
        "ok": bool(base.get("ok")),
        "backup_file": base.get("backup_file"),
        "size_bytes": base.get("size_bytes"),
        "integrity": base.get("integrity"),
    }
    result["prune"] = {
        "ok": prune_ok,
        "kept": [path.name for path in kept],
        "deleted": deleted,
        "removed_count": len(deleted),
    }
    # 顶层也直接给出常用字段，方便定时任务 / 监控一行读取。
    result["kept"] = [path.name for path in kept]
    result["deleted"] = deleted
    result["backup_dir"] = str(backup_dir)
    return result, lines


def _validate(args: argparse.Namespace) -> None:
    """校验并规范化参数组合，错误时抛出带中文说明的 ``ValueError``。

    会把 ``args.keep`` 从字符串转成 int 供 ``do_prune()`` 直接使用；
    校验全部在 ``main()`` 的 try 内执行，因此 ``--json`` 模式下错误输出也是纯 JSON。

    Raises:
        ValueError: ``--keep`` 非法，或动作 / 参数组合非法。
    """
    if args.keep is not None:
        args.keep = parse_keep_value(str(args.keep))
    # --prune 只是备份成功后的附加开关，不能与独立动作 --verify / --list 同用。
    if args.prune and (args.verify is not None or args.list):
        raise ValueError(
            "--prune 是备份成功后的附加清理开关，不能与 --verify / --list 同用"
        )
    if args.keep is not None and not args.prune:
        raise ValueError(
            "--keep 只能与 --prune 一起使用：python backup_db.py --prune --keep 7"
        )
    if args.prune and args.keep is None:
        raise ValueError("--prune 必须同时指定 --keep N（N 为正整数）")
    # 「是否处于备份动作」用「非 verify / list」判断，避免把 --prune 组合误拒。
    if args.include_uploads and (args.verify is not None or args.list):
        raise ValueError("--include-uploads 仅在备份动作下有效")


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：解析参数 → 执行动作 → 输出结果 → 返回退出码。

    Args:
        argv: 参数列表；``None`` 时读取 ``sys.argv[1:]``。

    Returns:
        成功返回 0；运行期失败返回 1；argparse 阶段参数错误返回 2。
        异常统一转成可读中文错误，不抛裸 traceback。
    """
    global _CURRENT_ARGV
    _CURRENT_ARGV = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    action = _action_of(args)
    data_dir = (args.data_dir or default_data_dir()).resolve()
    backup_dir = (args.backup_dir or default_backup_dir(data_dir)).resolve()

    try:
        _validate(args)
        if args.verify is not None:
            result, lines = do_verify(args)
        elif args.list:
            result, lines = do_list(backup_dir)
        else:
            result, lines = do_backup(args, data_dir, backup_dir)
            # 硬约束：只有备份成功（含 integrity_check 通过）才允许清理。
            # 备份失败时旧备份一份不动，避免误删仅存的历史备份。
            if args.prune:
                if result["ok"]:
                    result, prune_lines = do_prune(args, backup_dir, result)
                    lines = lines + prune_lines
                else:
                    lines.append("提示：备份未成功，已跳过清理（历史备份未被改动）")
    except (ValueError, sqlite3.Error, OSError, shutil.Error) as exc:
        message = f"错误：{exc}"
        result = _result(action, False, message)
        lines = [message]
    except Exception as exc:  # noqa: BLE001 - 兜底：绝不向用户抛裸 traceback
        message = f"错误：发生未预期异常（{type(exc).__name__}）：{exc}"
        result = _result(action, False, message)
        lines = [message]

    if args.json:
        # JSON 模式下 stdout 只输出纯 JSON，便于定时任务 / 监控直接解析。
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for line in lines:
            print(line)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
