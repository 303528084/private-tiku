"""迁移机制测试（T04）：``PRAGMA user_version`` + ``schema_version`` 审计表。

覆盖架构 §8.4 / §9.1 T04 的验收项：

1. 全新空库 → ``user_version == 1``、``schema_version`` 1 条、**无备份文件**；
2. 有数据的老库（``user_version == 0``）→ 自动备份 + ``integrity_check`` 通过 + 置 1；
3. 备份失败（``backups`` 被一个同名**文件**占位，等价于目录不可写）→ 抛
   :class:`MigrationError` 拒绝启动，``user_version`` 仍为 0；
4. 幂等：连续两次 ``init_db()`` → 第二次不备份、不改任何东西；
5. ``MIGRATIONS`` 里每条语句重复执行不报错（本版只有建审计表，天然幂等）。

注意：测试**绝不**写入仓库内 ``data/``，一律使用 ``tmp_path``。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

import pytest

from app import config, db, migrate


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """把 ``config`` 的三处路径指向临时目录，并重置初始化标志。"""
    directory = tmp_path / "data"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "DATA_DIR", directory)
    monkeypatch.setattr(config, "DB_PATH", directory / "tiku.db")
    monkeypatch.setattr(config, "UPLOAD_DIR", directory / "uploads")
    monkeypatch.delenv(migrate.SKIP_BACKUP_ENV, raising=False)
    db.reset_init_flag()
    yield directory
    db.reset_init_flag()


def _connect(db_path: Path) -> sqlite3.Connection:
    """以应用相同的 pragma 打开连接（row_factory 便于按列名取值）。"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _make_legacy_db(db_path: Path, question_count: int = 2) -> None:
    """造一个「v1.3 老库」：有业务数据，但 ``user_version`` 仍是 0。"""
    conn = _connect(db_path)
    try:
        for statement in db.BASE_DDL:
            conn.execute(statement)
        conn.execute("INSERT INTO imports (status) VALUES ('committed')")
        conn.execute(
            """
            INSERT INTO import_files (import_id, filename, file_type)
            VALUES (1, '老题库.md', 'md')
            """
        )
        for index in range(question_count):
            conn.execute(
                """
                INSERT INTO questions (import_file_id, question_no, qtype, stem, answer)
                VALUES (1, ?, 'single', ?, 'A')
                """,
                (str(index + 1), f"老题干 {index + 1}"),
            )
        conn.commit()
        assert migrate.user_version(conn) == 0
    finally:
        conn.close()


def _backup_files(directory: Path) -> list[Path]:
    """列出 ``backups/`` 下的备份文件（目录不存在时返回空列表）。"""
    target = directory / migrate.BACKUP_DIRNAME
    if not target.is_dir():
        return []
    return sorted(target.glob(f"{migrate.BACKUP_PREFIX}-*.db"))


# ===========================================================================
# ① 全新空库
# ===========================================================================
def test_fresh_db_stamps_version_without_backup(data_dir: Path) -> None:
    """全新空库：置 ``user_version=1``、写 1 条审计记录、**不产生备份**。"""
    db.init_db()

    conn = _connect(data_dir / "tiku.db")
    try:
        assert migrate.user_version(conn) == migrate.SCHEMA_VERSION == 1
        rows = conn.execute("SELECT * FROM schema_version ORDER BY version").fetchall()
    finally:
        conn.close()

    assert len(rows) == 1
    assert int(rows[0]["version"]) == 1
    assert rows[0]["applied_at"]
    assert rows[0]["backup_path"] == ""
    assert rows[0]["note"]
    assert _backup_files(data_dir) == []


def test_fresh_db_second_init_is_noop(data_dir: Path) -> None:
    """④ 幂等：第二次 ``init_db()`` 不备份、审计表仍只有 1 条。"""
    db.init_db()
    first = _backup_files(data_dir)
    db.reset_init_flag()
    db.init_db()
    second = _backup_files(data_dir)

    assert first == second == []

    conn = _connect(data_dir / "tiku.db")
    try:
        assert migrate.user_version(conn) == 1
        count = conn.execute("SELECT COUNT(*) AS c FROM schema_version").fetchone()["c"]
    finally:
        conn.close()
    assert int(count) == 1


# ===========================================================================
# ② 有数据的老库
# ===========================================================================
def test_legacy_db_with_data_is_backed_up(data_dir: Path) -> None:
    """老库（``user_version=0`` 且有题）：自动备份 + 校验通过 + 置 1。"""
    db_path = data_dir / "tiku.db"
    _make_legacy_db(db_path, question_count=3)

    db.init_db()

    backups = _backup_files(data_dir)
    assert len(backups) == 1, backups
    backup = backups[0]
    assert backup.name.startswith("pre-migrate-v1-")

    # 备份文件自身可用
    check = sqlite3.connect(str(backup))
    try:
        assert str(check.execute("PRAGMA integrity_check").fetchone()[0]).lower() == "ok"
        assert int(check.execute("SELECT COUNT(*) FROM questions").fetchone()[0]) == 3
        # 备份是「迁移前」的快照：user_version 仍是 0
        assert int(check.execute("PRAGMA user_version").fetchone()[0]) == 0
    finally:
        check.close()

    conn = _connect(db_path)
    try:
        assert migrate.user_version(conn) == 1
        row = conn.execute("SELECT * FROM schema_version WHERE version = 1").fetchone()
        assert int(conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]) == 3
    finally:
        conn.close()

    assert row is not None
    assert row["backup_path"] == str(backup)

    # 第二次启动：不再产生新备份
    db.reset_init_flag()
    db.init_db()
    assert len(_backup_files(data_dir)) == 1


# ===========================================================================
# ③ 备份失败 → 拒绝启动
# ===========================================================================
def test_backup_failure_refuses_to_start(data_dir: Path) -> None:
    """``backups`` 被同名文件占位（等价于目录不可写）→ 抛异常且版本号不动。"""
    db_path = data_dir / "tiku.db"
    _make_legacy_db(db_path, question_count=1)

    blocker = data_dir / migrate.BACKUP_DIRNAME
    blocker.write_text("我不是目录", encoding="utf-8")

    with pytest.raises(migrate.MigrationError) as excinfo:
        db.init_db()
    assert "备份失败" in str(excinfo.value)

    conn = _connect(db_path)
    try:
        assert migrate.user_version(conn) == 0
        # 审计表也没被创建（迁移整体未执行）
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
    finally:
        conn.close()
    assert exists is None


def test_skip_backup_switch_bypasses_backup(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """应急开关 ``TIKU_SKIP_MIGRATION_BACKUP=1``：跳过备份但仍完成迁移。"""
    db_path = data_dir / "tiku.db"
    _make_legacy_db(db_path, question_count=1)
    monkeypatch.setenv(migrate.SKIP_BACKUP_ENV, "1")

    db.init_db()

    assert _backup_files(data_dir) == []
    conn = _connect(db_path)
    try:
        assert migrate.user_version(conn) == 1
        row = conn.execute("SELECT backup_path FROM schema_version WHERE version = 1").fetchone()
    finally:
        conn.close()
    assert row is not None and row["backup_path"] == ""


# ===========================================================================
# ⑤ MIGRATIONS 语句幂等
# ===========================================================================
def test_migration_statements_are_idempotent(tmp_path: Path) -> None:
    """每条迁移语句执行两次结果相同、不报错（``IF NOT EXISTS`` 保障）。"""
    conn = sqlite3.connect(str(tmp_path / "scratch.db"))
    try:
        for _ in range(2):
            for _version, statements in migrate.MIGRATIONS:
                for statement in statements:
                    conn.execute(statement)
            conn.commit()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert "schema_version" in tables


def test_migration_list_is_ascending_and_complete() -> None:
    """``MIGRATIONS`` 必须从 1 连续升到 ``SCHEMA_VERSION``，否则迁移会漏版本。"""
    versions = [version for version, _statements in migrate.MIGRATIONS]
    assert versions == list(range(1, migrate.SCHEMA_VERSION + 1))
    assert set(migrate.MIGRATION_NOTES) == set(versions)
