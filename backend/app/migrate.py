"""版本化数据库迁移：``PRAGMA user_version`` 为权威版本号，``schema_version`` 为审计日志。

设计要点（架构 §8.4）：

* **本版（v1.4）零 DDL 变更**：v1 只建审计表 ``schema_version`` 并把 ``user_version``
  盖成 1。目的是把「迁移机制」与「真实 DDL 变更」解耦——v1.5 起只需往
  :data:`MIGRATIONS` 里追加一条，机制本身已被验证过。
* **版本号载体**：``PRAGMA user_version`` 是权威判定依据（一次 PRAGMA 读，原子、
  随 db 文件走、备份/恢复自动携带）；``schema_version`` 表只做可读的审计日志
  （什么时候迁的、从哪个版本迁的、备份文件在哪）。两者由同一段代码同时写。
* **迁移语句必须幂等**：``CREATE TABLE/INDEX`` 一律带 ``IF NOT EXISTS``；
  未来的 ``ALTER TABLE ADD COLUMN`` 必须先查 ``PRAGMA table_info`` 再决定是否执行。
  「执行 1 次」与「执行 100 次」结果必须相同，否则用户被旧备份覆盖后会被锁死在门外。
* **非空库先备份**：``sqlite3.Connection.backup()`` 在线备份（WAL 模式下
  ``shutil.copy2`` 会拿到不一致快照）；备份后立刻对备份文件跑
  ``PRAGMA integrity_check``，不通过则**拒绝启动**（比迁坏了更安全）。
  这里刻意**不 import** ``scripts/backup_db.py``——那是「零依赖可单独复制」的 CLI
  脚本，import 它会带进 argparse 等副作用。
* **每个版本一个事务**：该版本全部语句 + ``schema_version`` 记录 +
  ``PRAGMA user_version = N`` 在同一个事务里提交，任一步异常整体回滚、版本号不前进。
* **空库不备份**：全新安装没有数据可丢。这条同时保证测试环境（每个用例一个临时空库）
  不会写出成百上千个备份文件。

应急开关：``TIKU_SKIP_MIGRATION_BACKUP=1`` 可跳过迁移前备份（磁盘满时的救命绳）。
刻意**不提供** ``TIKU_SKIP_MIGRATION``——跳过迁移会让代码面对不存在的表，
错误信息比拒绝启动难排查得多。
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from . import backup, config

logger = logging.getLogger("private_tiku.migrate")

# ---------------------------------------------------------------------------
# 版本定义
# ---------------------------------------------------------------------------
SCHEMA_VERSION: int = 1
"""当前代码期望的数据库结构版本（``PRAGMA user_version`` 的目标值）。"""

MIGRATIONS: List[Tuple[int, List[str]]] = [
    (
        1,
        [
            """
            CREATE TABLE IF NOT EXISTS schema_version (
              version      INTEGER PRIMARY KEY,
              applied_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
              note         TEXT NOT NULL DEFAULT '',
              backup_path  TEXT NOT NULL DEFAULT ''
            )
            """,
        ],
    ),
]
"""``[(目标版本号, [幂等 SQL, ...]), ...]``，必须按版本号升序。

v1 是「零 DDL 变更」的基线盖章：只建审计表。后续版本追加在列表末尾。
"""

MIGRATION_NOTES: dict[int, str] = {
    1: "v1.3 基线盖章：建 schema_version 审计表，无业务表/列变更",
}
"""每个版本的人类可读说明，写入 ``schema_version.note``。"""

BACKUP_DIRNAME: str = "backups"
"""迁移前备份目录（相对 ``config.DATA_DIR``）。"""

BACKUP_PREFIX: str = "pre-migrate"
"""备份文件名前缀：``pre-migrate-v1-20261113-120000.db``。"""

SKIP_BACKUP_ENV: str = "TIKU_SKIP_MIGRATION_BACKUP"
"""设为 ``1`` 时跳过迁移前备份（应急开关，见模块 docstring）。"""


class MigrationError(RuntimeError):
    """迁移无法安全进行（备份失败 / 备份不可用 / 迁移语句失败）。

    抛出该异常意味着**拒绝启动**：调用方（``db.init_db``）不吞异常，
    uvicorn 启动失败并打印原因，``user_version`` 保持迁移前的值。
    """


# ---------------------------------------------------------------------------
# 版本读取
# ---------------------------------------------------------------------------
def user_version(conn: sqlite3.Connection) -> int:
    """读 ``PRAGMA user_version``（权威版本号）。"""
    row = conn.execute("PRAGMA user_version").fetchone()
    if row is None:
        return 0
    return int(row[0])


def read_version(db_path: Optional[Path] = None) -> int:
    """只读地打开数据库并返回当前版本号（供启动日志使用，不建表）。

    Args:
        db_path: 数据库文件路径；省略时取 :data:`config.DB_PATH`。

    Returns:
        ``PRAGMA user_version`` 的当前值；文件不存在时返回 0。
    """
    path = Path(db_path) if db_path is not None else config.DB_PATH
    if not path.exists():
        return 0
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30.0)
    try:
        return user_version(conn)
    except sqlite3.DatabaseError:  # pragma: no cover - 文件损坏时的兜底
        logger.warning("读取 schema 版本失败：%s", path)
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 空库判定
# ---------------------------------------------------------------------------
def is_empty_db(conn: sqlite3.Connection) -> bool:
    """判断是否「空库」（``questions`` 表不存在或行数为 0）。

    空库 = 全新安装，没有任何业务数据可丢，因此不需要迁移前备份。
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'questions'"
    ).fetchone()
    if row is None:
        return True
    count_row = conn.execute("SELECT COUNT(*) FROM questions").fetchone()
    return count_row is None or int(count_row[0]) == 0


# ---------------------------------------------------------------------------
# 备份（实现见 app/backup.py，与分享包导入共用）
# ---------------------------------------------------------------------------
def backup_dir() -> Path:
    """迁移前备份目录（``config.DATA_DIR/backups``），**不创建**。"""
    return backup.backup_dir()


def _skip_backup_requested() -> bool:
    """是否通过应急开关跳过备份（每次调用都读环境变量）。"""
    return (os.getenv(SKIP_BACKUP_ENV) or "").strip() == "1"


def _backup_path(target_version: int) -> Path:
    """生成备份路径：``backups/pre-migrate-v{N}-{YYYYmmdd-HHMMSS}.db``。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return backup_dir() / f"{BACKUP_PREFIX}-v{target_version}-{stamp}.db"


def backup_before_migrate(
    conn: sqlite3.Connection, target_version: int
) -> Optional[Path]:
    """迁移前在线备份主库，并对备份文件做 ``PRAGMA integrity_check``。

    Args:
        conn: 主库连接（备份前会先 ``commit``，保证备份看到完整快照）。
        target_version: 本次迁移的目标版本号（用于文件名）。

    Returns:
        备份文件路径；应急开关生效时返回 ``None``。

    Raises:
        MigrationError: 目录不可写、备份失败或备份文件损坏（调用方必须拒绝启动）。
    """
    if _skip_backup_requested():
        logger.warning(
            "已设置 %s=1，跳过迁移前备份（应急模式，风险自负）", SKIP_BACKUP_ENV
        )
        return None

    try:
        path = backup.backup_database(_backup_path(target_version), source=conn)
    except backup.BackupError as exc:
        raise MigrationError(
            f"迁移前备份失败：{exc}。"
            "数据库未被修改，请检查磁盘空间与目录权限后重试。"
        ) from exc

    logger.info("迁移前已备份到 %s（integrity_check: ok）", path)
    return path


# ---------------------------------------------------------------------------
# 迁移执行
# ---------------------------------------------------------------------------
def _apply_version(
    conn: sqlite3.Connection,
    version: int,
    statements: Sequence[str],
    backup_path: Optional[Path],
) -> None:
    """在**单个事务**内执行某版本的全部语句 + 审计记录 + 版本号推进。

    Args:
        conn: 主库连接。
        version: 该版本号。
        statements: 该版本要执行的幂等 SQL 列表。
        backup_path: 本次迁移的备份文件（写入审计表，便于事后回滚）。

    Raises:
        MigrationError: 任一步失败（事务已回滚，``user_version`` 未前进）。
    """
    try:
        # 结束可能存在的隐式事务，避免 "cannot start a transaction within a transaction"
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        for statement in statements:
            conn.execute(statement)
        conn.execute(
            """
            INSERT OR REPLACE INTO schema_version (version, note, backup_path)
            VALUES (?, ?, ?)
            """,
            (
                int(version),
                MIGRATION_NOTES.get(int(version), ""),
                "" if backup_path is None else str(backup_path),
            ),
        )
        # PRAGMA user_version 的写入是事务性的：回滚时版本号一并回退（已实测）。
        conn.execute(f"PRAGMA user_version = {int(version)}")
        conn.commit()
    except (sqlite3.Error, OSError) as exc:
        try:
            conn.rollback()
        except sqlite3.Error:  # pragma: no cover - 回滚失败时保留原始异常
            logger.exception("迁移回滚失败")
        location = "" if backup_path is None else f"，备份在 {backup_path}"
        raise MigrationError(
            f"迁移到 v{version} 失败：{exc}。已回滚到 v{version - 1}{location}。"
        ) from exc


def run_migrations(conn: sqlite3.Connection) -> int:
    """把数据库迁移到 :data:`SCHEMA_VERSION`（幂等）。

    Args:
        conn: 已建好基础 DDL 的数据库连接（``db.init_db`` 内调用）。

    Returns:
        迁移完成后的 ``PRAGMA user_version``。

    Raises:
        MigrationError: 备份失败 / 备份不可用 / 迁移语句失败（调用方应拒绝启动）。
    """
    current = user_version(conn)
    if current >= SCHEMA_VERSION:
        logger.info("数据库已是 v%d（目标 v%d），跳过迁移", current, SCHEMA_VERSION)
        return current

    pending = [(v, stmts) for v, stmts in MIGRATIONS if current < v <= SCHEMA_VERSION]
    if not pending:  # pragma: no cover - 版本号与 MIGRATIONS 不一致时才会走到
        logger.warning(
            "user_version=%d 小于目标 v%d，但 MIGRATIONS 中没有对应条目",
            current,
            SCHEMA_VERSION,
        )
        return current

    logger.info("开始迁移：从 v%d 迁移到 v%d", current, SCHEMA_VERSION)

    backup_path: Optional[Path] = None
    if is_empty_db(conn):
        logger.info("空库（无业务数据），跳过迁移前备份")
    else:
        backup_path = backup_before_migrate(conn, SCHEMA_VERSION)

    for version, statements in pending:
        _apply_version(conn, version, statements, backup_path)
        logger.info("已迁移到 v%d", version)

    final = user_version(conn)
    if backup_path is not None:
        logger.info("迁移完成：v%d → v%d，备份在 %s", current, final, backup_path)
    else:
        logger.info("迁移完成：v%d → v%d（无备份）", current, final)
    return final


__all__ = [
    "MIGRATIONS",
    "MIGRATION_NOTES",
    "SCHEMA_VERSION",
    "MigrationError",
    "backup_before_migrate",
    "backup_dir",
    "is_empty_db",
    "read_version",
    "run_migrations",
    "user_version",
]
