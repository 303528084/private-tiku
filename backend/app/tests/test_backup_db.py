"""``backend/scripts/backup_db.py`` 的 CLI 冒烟测试。

刻意用 **真实子进程**（``subprocess.run``）调用脚本，而不是 import 它：
这样才能验证「零依赖、单文件、可单独复制到 NAS 部署目录运行」这一核心约束。
所有用例都在 ``tmp_path`` 里造库与备份，不触碰仓库的 ``data/``，也不留下 ``backups/``。

动作语义：``--prune --keep N`` 是「备份成功后的附加清理」，不是独立动作；
一次调用 = 先在线备份并校验 → 再按 N 清理最旧备份。备份失败绝不清理。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

# backend/app/tests/test_backup_db.py
#   parents[0] = backend/app/tests, parents[1] = backend/app, parents[2] = backend
# 因此 scripts/ 与 app/ 同级，均在 backend/ 下。
SCRIPT: Path = Path(__file__).resolve().parents[2] / "scripts" / "backup_db.py"
"""被测脚本路径，纯相对推导，不含任何盘符硬编码。"""

BACKUP_NAME_RE = re.compile(r"^tiku-\d{8}-\d{6}(?:-\d+)?\.db$")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """用真实子进程执行备份脚本，并固定 UTF-8 输出以便断言中文文案。

    Args:
        *args: 传给脚本的命令行参数。

    Returns:
        已完成的子进程结果（stdout / stderr 均为 str）。
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    # 测试一律显式传 --data-dir / --backup-dir，清掉宿主环境变量避免干扰默认语义。
    env.pop("TIKU_DATA_DIR", None)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60,
        check=False,
    )


def _user_file_snapshot(root: Path) -> dict[str, bytes]:
    """快照目录里所有**非合规备份命名**的文件，用于逐字节零改动断言。"""
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and BACKUP_NAME_RE.match(path.name) is None
    }


@pytest.fixture()
def workspace(tmp_path: Path) -> tuple[Path, Path]:
    """造一个临时数据目录（普通表 + 若干行，尽量贴近真实库）+ 备份目录。

    WAL 模式与 FTS5 trigram 虚表都是「能用就加、不支持就跳过」，
    不让测试强依赖特定 SQLite 编译选项。
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "tiku.db"
    conn = sqlite3.connect(str(db_path))
    try:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass
        conn.execute(
            "CREATE TABLE questions (id INTEGER PRIMARY KEY, stem TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE import_files (id INTEGER PRIMARY KEY, filename TEXT NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO questions(stem) VALUES (?)",
            [(f"题干-{i}",) for i in range(1, 8)],
        )
        conn.executemany(
            "INSERT INTO import_files(filename) VALUES (?)",
            [("a.md",), ("b.json",)],
        )
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE questions_fts USING fts5(stem, tokenize='trigram')"
            )
            conn.execute("INSERT INTO questions_fts(stem) SELECT stem FROM questions")
        except sqlite3.Error:
            pass
        conn.commit()
    finally:
        conn.close()
    return data_dir, tmp_path / "backups"


# ---------------------------------------------------------------------------
# 基础能力（QA 已验证通过，保留为回归基线）
# ---------------------------------------------------------------------------
def test_default_action_creates_valid_backup(workspace: tuple[Path, Path]) -> None:
    """不带任何动作参数时默认执行备份，产物完整、行数一致且校验通过。"""
    data_dir, backup_dir = workspace

    proc = _run("--data-dir", str(data_dir), "--backup-dir", str(backup_dir))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ok" in proc.stdout
    assert "在 data/ 之外" in proc.stdout
    files = sorted(backup_dir.glob("tiku-*.db"))
    assert len(files) == 1
    assert BACKUP_NAME_RE.match(files[0].name)

    with sqlite3.connect(str(files[0])) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 7
        assert conn.execute("SELECT COUNT(*) FROM import_files").fetchone()[0] == 2


def test_missing_source_db_fails_readably(tmp_path: Path) -> None:
    """源库不存在时非 0 退出，并给出可读中文错误（不是裸 traceback）。"""
    proc = _run(
        "--data-dir", str(tmp_path / "no-such-dir"),
        "--backup-dir", str(tmp_path / "backups"),
    )

    assert proc.returncode != 0
    output = proc.stdout + proc.stderr
    assert "错误" in output
    assert "不存在" in output
    assert "Traceback" not in output


def test_verify_ok_and_rejects_garbage_file(
    workspace: tuple[Path, Path], tmp_path: Path
) -> None:
    """--verify 校验真备份通过且列出关键表；对非 SQLite 垃圾文件非 0 退出。"""
    data_dir, backup_dir = workspace
    created = _run("--data-dir", str(data_dir), "--backup-dir", str(backup_dir))
    assert created.returncode == 0, created.stdout + created.stderr
    backup = sorted(backup_dir.glob("tiku-*.db"))[0]

    ok = _run("--verify", str(backup))
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "ok" in ok.stdout
    assert "questions" in ok.stdout
    assert "import_files" in ok.stdout
    assert "7 行" in ok.stdout

    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"this is definitely not a sqlite database" * 16)
    bad = _run("--verify", str(garbage))
    assert bad.returncode != 0
    assert "错误" in (bad.stdout + bad.stderr)
    assert "Traceback" not in (bad.stdout + bad.stderr)


def test_list_shows_created_backups(workspace: tuple[Path, Path]) -> None:
    """--list 能列出刚生成的备份；同秒连续备份不覆盖，各自留一份。"""
    data_dir, backup_dir = workspace

    first = _run("--data-dir", str(data_dir), "--backup-dir", str(backup_dir))
    second = _run("--data-dir", str(data_dir), "--backup-dir", str(backup_dir))
    assert first.returncode == 0 and second.returncode == 0

    files = sorted(backup_dir.glob("tiku-*.db"))
    assert len(files) == 2
    assert all(BACKUP_NAME_RE.match(path.name) for path in files)

    listed = _run("--backup-dir", str(backup_dir), "--list")
    assert listed.returncode == 0, listed.stdout + listed.stderr
    assert "共找到 2 份备份" in listed.stdout
    for path in files:
        assert path.name in listed.stdout
    assert "字节" in listed.stdout and "修改于" in listed.stdout


def test_include_uploads_copies_and_tolerates_missing_dir(
    workspace: tuple[Path, Path], tmp_path: Path
) -> None:
    """--include-uploads 复制上传原文件；uploads 目录不存在时跳过且不失败。"""
    data_dir, backup_dir = workspace
    uploads = data_dir / "uploads" / "import_1"
    uploads.mkdir(parents=True)
    (uploads / "a.md").write_text(
        "## 1. [单选题]\n\n题干？\n\n- A. 甲\n- B. 乙\n\n**答案**：A\n",
        encoding="utf-8",
    )

    copied = _run(
        "--data-dir", str(data_dir), "--backup-dir", str(backup_dir),
        "--include-uploads",
    )
    assert copied.returncode == 0, copied.stdout + copied.stderr
    copies = list(backup_dir.glob("uploads-*"))
    assert len(copies) == 1
    assert (copies[0] / "import_1" / "a.md").read_text(encoding="utf-8").startswith("## 1.")

    bare = tmp_path / "bare_data"
    bare.mkdir()
    conn = sqlite3.connect(str(bare / "tiku.db"))
    try:
        conn.execute("CREATE TABLE questions (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO questions VALUES (1)")
        conn.commit()
    finally:
        conn.close()
    bare_backups = tmp_path / "backups_no_uploads"
    skipped = _run(
        "--data-dir", str(bare), "--backup-dir", str(bare_backups),
        "--include-uploads",
    )
    assert skipped.returncode == 0, skipped.stdout + skipped.stderr
    assert "跳过" in skipped.stdout
    assert not list(bare_backups.glob("uploads-*"))
    assert len(list(bare_backups.glob("tiku-*.db"))) == 1


def test_json_output_is_machine_readable(workspace: tuple[Path, Path]) -> None:
    """--json 的 stdout 是纯 JSON，含固定字段且 ok 为 true（不带 prune）。"""
    data_dir, backup_dir = workspace

    proc = _run("--data-dir", str(data_dir), "--backup-dir", str(backup_dir), "--json")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["action"] == "backup"
    for key in ("backup_file", "size_bytes", "integrity", "message"):
        assert key in payload
    assert payload["integrity"] == "ok"
    assert payload["size_bytes"] > 0
    assert Path(payload["backup_file"]).is_file()


def test_backup_is_self_contained_single_file(workspace: tuple[Path, Path]) -> None:
    """备份产物必须是自包含单文件（journal_mode=DELETE），无 -wal / -shm 伴生文件。"""
    data_dir, backup_dir = workspace

    proc = _run("--data-dir", str(data_dir), "--backup-dir", str(backup_dir))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "自包含单文件" in proc.stdout
    entries = sorted(path.name for path in backup_dir.iterdir())
    assert len(entries) == 1, entries
    assert not any(name.endswith(("-wal", "-shm")) for name in entries)
    backup = backup_dir / entries[0]
    with sqlite3.connect(str(backup)) as conn:
        assert str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "delete"
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 7
    verified = _run("--verify", str(backup))
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert sorted(path.name for path in backup_dir.iterdir()) == entries


# ---------------------------------------------------------------------------
# P0-1：--prune 是「备份成功后的附加清理」
# ---------------------------------------------------------------------------
def test_prune_combo_creates_new_backup_and_protects_user_files(
    workspace: tuple[Path, Path]
) -> None:
    """文档同款 --prune --keep 2：生成本次新备份 + 只删最旧合规 .db，用户文件零改动。

    B2 场景：6 份旧合规备份 + 1 份新备份 = 7 份，keep 2 →
    保留「新备份 + tiku-20260105-020000-2.db」，删 0101/0102/0103/0104/0105 共 5 份。
    """
    data_dir, backup_dir = workspace
    backup_dir.mkdir()
    old_names = [
        "tiku-20260101-020000.db",
        "tiku-20260102-020000.db",
        "tiku-20260103-020000.db",
        "tiku-20260104-020000.db",
        "tiku-20260105-020000.db",
        "tiku-20260105-020000-2.db",
    ]
    for name in old_names:
        (backup_dir / name).write_bytes(b"old-" + name.encode("ascii"))
    # 用户放进去的其它文件：不匹配命名规则的散文件、.bak/.gz、子目录，逐字节断言保留。
    (backup_dir / "keep-me.txt").write_text("keep-me-v1\n", encoding="utf-8")
    (backup_dir / "other.db").write_bytes(b"other-db-bytes")
    (backup_dir / "tiku-20260101-020000.db.bak").write_bytes(b"bak-bytes")
    (backup_dir / "tiku-20260101-020000.db.gz").write_bytes(b"gz-bytes")
    (backup_dir / "tiku-bad-name.db").write_bytes(b"bad-name-bytes")
    manual = backup_dir / "manual"
    manual.mkdir()
    (manual / "notes.txt").write_text("manual notes\n", encoding="utf-8")
    before = _user_file_snapshot(backup_dir)

    proc = _run(
        "--data-dir", str(data_dir), "--backup-dir", str(backup_dir),
        "--prune", "--keep", "2",
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "待删除清单" in proc.stdout
    # 用与脚本一致的命名正则筛选，避免把 tiku-bad-name.db / *.db.bak 算成备份。
    now = set(
        path.name for path in backup_dir.iterdir()
        if path.is_file() and BACKUP_NAME_RE.match(path.name)
    )
    created = now - set(old_names)
    assert len(created) == 1, f"本次应恰好生成 1 份新备份，实际 {created}"
    new_backup = backup_dir / next(iter(created))
    assert BACKUP_NAME_RE.match(new_backup.name)
    with sqlite3.connect(str(new_backup)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 7

    # 保留最近 2 份 = 新备份 + 0105-2；其余 5 份旧备份被删。
    assert now == {new_backup.name, "tiku-20260105-020000-2.db"}, now
    for deleted in old_names[:5]:
        assert not (backup_dir / deleted).exists(), deleted
    assert _user_file_snapshot(backup_dir) == before


@pytest.mark.parametrize(
    "failure_kind",
    ["missing_source", "empty_source", "garbage_source", "backup_dir_is_file"],
)
def test_backup_failure_never_triggers_prune(
    failure_kind: str, workspace: tuple[Path, Path], tmp_path: Path
) -> None:
    """硬约束：备份失败时绝不清理，历史备份一份不少、不产生新备份。

    覆盖源库不存在 / 空文件 / 非 SQLite 垃圾库 / 备份目录不可创建四种失败。
    """
    data_dir, backup_dir = workspace
    backup_dir.mkdir()
    old_names = [
        "tiku-20260101-020000.db",
        "tiku-20260102-020000.db",
        "tiku-20260103-020000.db",
    ]
    for name in old_names:
        (backup_dir / name).write_bytes(b"old-" + name.encode("ascii"))
    blocker: Path | None = None

    if failure_kind == "missing_source":
        effective_data = tmp_path / "no-such-data-dir"
        effective_backup = backup_dir
    elif failure_kind == "empty_source":
        effective_data = tmp_path / "empty_data"
        effective_data.mkdir()
        (effective_data / "tiku.db").write_bytes(b"")
        effective_backup = backup_dir
    elif failure_kind == "garbage_source":
        effective_data = tmp_path / "garbage_data"
        effective_data.mkdir()
        (effective_data / "tiku.db").write_bytes(b"not a sqlite database" * 32)
        effective_backup = backup_dir
    else:
        # backup_dir 指向一个已存在的文件：mkdir(parents=True, exist_ok=True) 必然失败。
        effective_data = data_dir
        blocker = tmp_path / "blocker"
        blocker.write_bytes(b"blocker-bytes")
        effective_backup = blocker

    proc = _run(
        "--data-dir", str(effective_data), "--backup-dir", str(effective_backup),
        "--prune", "--keep", "1",
    )

    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "错误" in (proc.stdout + proc.stderr)
    assert "Traceback" not in (proc.stdout + proc.stderr)
    if blocker is not None:
        assert blocker.read_bytes() == b"blocker-bytes"
    else:
        # 三份历史备份一份不少，且没有产生本次新备份。
        remaining = sorted(path.name for path in backup_dir.glob("tiku-*.db"))
        assert remaining == old_names, remaining


def test_keep_count_includes_new_backup(workspace: tuple[Path, Path]) -> None:
    """连续跑 N+2 次 --prune --keep 2，每次都恰好留 2 份（新备份计入 keep）。"""
    data_dir, backup_dir = workspace

    for round_no in range(4):
        proc = _run(
            "--data-dir", str(data_dir), "--backup-dir", str(backup_dir),
            "--prune", "--keep", "2",
        )
        assert proc.returncode == 0, f"第 {round_no + 1} 轮：{proc.stdout + proc.stderr}"
        # 前两轮目录还在攒备份，从第三轮起稳定为恰好 2 份。
        assert len(list(backup_dir.glob("tiku-*.db"))) == min(round_no + 1, 2)


def test_composite_json_shape(workspace: tuple[Path, Path]) -> None:
    """复合动作只输出**一个**纯 JSON：顶层字段 + backup / prune 子对象。"""
    data_dir, backup_dir = workspace

    proc = _run(
        "--data-dir", str(data_dir), "--backup-dir", str(backup_dir),
        "--prune", "--keep", "1", "--json",
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)  # 单个 JSON：拼接两段会在这里直接报错
    assert payload["ok"] is True
    assert payload["action"] == "backup+prune"
    assert payload["integrity"] == "ok"
    assert Path(payload["backup_file"]).is_file()
    assert payload["size_bytes"] > 0
    assert isinstance(payload["kept"], list)
    assert isinstance(payload["deleted"], list)
    assert payload["backup"]["ok"] is True
    assert payload["backup"]["backup_file"] == payload["backup_file"]
    assert payload["backup"]["integrity"] == "ok"
    assert payload["backup"]["size_bytes"] == payload["size_bytes"]
    assert payload["prune"]["ok"] is True
    assert payload["prune"]["kept"] == payload["kept"]
    assert payload["prune"]["deleted"] == payload["deleted"]


@pytest.mark.parametrize("extra", [("--verify", "any.db"), ("--list",)])
def test_verify_or_list_with_prune_is_rejected(
    extra: tuple[str, ...], workspace: tuple[Path, Path]
) -> None:
    """--prune 与 --verify / --list 同给 → 非 0、中文错误、且不产生备份。"""
    data_dir, backup_dir = workspace

    proc = _run(
        "--data-dir", str(data_dir), "--backup-dir", str(backup_dir),
        "--prune", "--keep", "1", *extra,
    )

    assert proc.returncode != 0
    assert "错误" in (proc.stdout + proc.stderr)
    assert not list(backup_dir.glob("tiku-*.db"))


@pytest.mark.parametrize("flags", [("--keep", "3"), ("--prune",)])
def test_keep_and_prune_require_each_other(
    flags: tuple[str, ...], workspace: tuple[Path, Path]
) -> None:
    """--keep 单独给 / --prune 不带 keep 都报错，且不产生备份。"""
    data_dir, backup_dir = workspace

    proc = _run(
        "--data-dir", str(data_dir), "--backup-dir", str(backup_dir), *flags
    )

    assert proc.returncode != 0
    assert "错误" in (proc.stdout + proc.stderr)
    assert not list(backup_dir.glob("tiku-*.db"))


def test_include_uploads_with_prune_uses_same_stamp(
    workspace: tuple[Path, Path]
) -> None:
    """--include-uploads 与 --prune 组合不被误拒，uploads 副本与 .db 同时间戳。"""
    data_dir, backup_dir = workspace
    uploads = data_dir / "uploads" / "import_1"
    uploads.mkdir(parents=True)
    (uploads / "a.md").write_text("## 1. [单选题]\n", encoding="utf-8")

    proc = _run(
        "--data-dir", str(data_dir), "--backup-dir", str(backup_dir),
        "--prune", "--keep", "1", "--include-uploads",
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    dbs = sorted(backup_dir.glob("tiku-*.db"))
    copies = sorted(backup_dir.glob("uploads-*"))
    assert len(dbs) == 1 and len(copies) == 1
    stamp = dbs[0].name[len("tiku-"):-len(".db")]
    assert copies[0].name == f"uploads-{stamp}"
    assert (copies[0] / "import_1" / "a.md").read_text(encoding="utf-8").startswith("## 1.")


# ---------------------------------------------------------------------------
# P1-1：--json 契约在 argparse 阶段也要成立
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_keep", ["0", "-1", "abc"])
def test_invalid_keep_with_json_still_emits_json(bad_keep: str) -> None:
    """非法 --keep + --json：stdout 可解析 JSON、ok=false、非 0 退出。"""
    proc = _run("--prune", "--keep", bad_keep, "--json")

    assert proc.returncode != 0
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert "正整数" in payload["message"]
    assert payload["action"] in ("backup+prune", "parse")


@pytest.mark.parametrize(
    "extra",
    [("--unknown-flag",), ("--verify", "a.db", "--list")],
)
def test_argparse_errors_with_json_emit_pure_json(
    extra: tuple[str, ...]
) -> None:
    """未知参数 / 互斥冲突等 argparse 阶段错误，--json 下 stdout 仍是纯 JSON。"""
    proc = _run(*extra, "--json")

    assert proc.returncode != 0
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert payload["action"] == "parse"
    assert "错误" in payload["message"]


def test_argparse_error_without_json_is_chinese(tmp_path: Path) -> None:
    """普通模式下 argparse 错误是中文可读提示，不是英文 usage + 裸 traceback。"""
    proc = _run("--unknown-flag")

    assert proc.returncode != 0
    output = proc.stdout + proc.stderr
    assert "错误：参数错误" in output
    assert "Traceback" not in output
