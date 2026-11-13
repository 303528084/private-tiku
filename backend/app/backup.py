"""SQLite 在线备份（应用内共用实现）。

为什么不用 ``shutil.copy2``：主库跑在 WAL 模式下，直接复制主库文件会拿到
**不一致快照**（``-wal`` 里已提交但未 checkpoint 的事务会丢）。统一改用
``sqlite3.Connection.backup()``，它内部走 SQLite 的在线备份 API。

为什么不在应用里 import ``scripts/backup_db.py``：那是一个「零依赖、可单独复制到
NAS 上运行」的 CLI 脚本，import 它会带进 argparse 等副作用，且它的目录约定
（备份落在 ``data/`` 之外）与容器内的应用不同。

调用方：
* :mod:`app.migrate` —— 迁移前备份（``pre-migrate-v{N}-*``）
* :mod:`app.api.imports` —— 分享包导入前备份（``pre-import-*``）

两者共用本模块，避免「备份 + integrity_check + 失败清理」被实现两遍。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

from . import config

logger = logging.getLogger("private_tiku.backup")


class BackupError(RuntimeError):
    """备份无法安全完成（目录不可写 / 磁盘满 / 备份文件损坏）。"""


def _discard(path: Path) -> None:
    """删除一个不可用的备份文件（尽力而为，失败只记日志）。"""
    try:
        path.unlink()
    except OSError:  # pragma: no cover - 清理失败不影响主流程
        logger.warning("清理无效备份文件失败：%s", path)


def unique_path(destination: Path) -> Path:
    """给备份路径去重：同秒重复时追加 ``-2`` / ``-3`` …（不改扩展名）。"""
    path = Path(destination)
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def backup_database(
    destination: Path, *, source: Optional[sqlite3.Connection] = None
) -> Path:
    """把主库在线备份到 ``destination``，并对备份文件做 ``PRAGMA integrity_check``。

    Args:
        destination: 目标文件路径；父目录不存在会自动创建，重名自动加序号。
        source: 复用已有连接（迁移流程里连接已开）；省略时按
            :data:`config.DB_PATH` 新开一条并负责关闭。

    Returns:
        实际写入的备份文件路径。

    Raises:
        BackupError: 目录不可写、备份失败或备份文件 ``integrity_check`` 不为 ``ok``。
            失败时会删除半成品文件，**主库不受任何影响**。
    """
    path = Path(destination)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupError(f"无法创建备份目录 {path.parent}（{exc}）") from exc

    path = unique_path(path)
    owns_connection = source is None
    conn = source if source is not None else sqlite3.connect(str(config.DB_PATH), timeout=30.0)
    verdict = ""
    try:
        # WAL 模式下必须先结束写事务，backup() 才能拿到一致快照。
        try:
            conn.commit()
        except sqlite3.Error:  # pragma: no cover - 连接已损坏时的兜底
            logger.warning("备份前提交事务失败，继续尝试备份")
        dst = sqlite3.connect(str(path))
        try:
            conn.backup(dst)
            row = dst.execute("PRAGMA integrity_check").fetchone()
            verdict = "" if row is None else str(row[0])
        finally:
            dst.close()
    except (OSError, sqlite3.Error) as exc:
        _discard(path)
        raise BackupError(f"备份失败：{path}（{exc}）") from exc
    finally:
        if owns_connection:
            conn.close()

    if verdict.lower() != "ok":
        _discard(path)
        raise BackupError(f"备份不可用：{path} 的 integrity_check 结果为「{verdict}」")

    logger.info("已备份到 %s（integrity_check: ok）", path)
    return path


def backup_dir() -> Path:
    """备份目录（``config.DATA_DIR/backups``），**不创建**。"""
    return config.DATA_DIR / config.BACKUP_DIRNAME


__all__ = ["BackupError", "backup_database", "backup_dir", "unique_path"]
