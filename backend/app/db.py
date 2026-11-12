"""SQLite 连接工厂、建表 DDL、FTS5 降级检测与行序列化。

设计要点（架构 §3）：
* 使用标准库 sqlite3，不引入 ORM；全部 SQL 显式可控。
* FTS5 使用 ``tokenize='trigram'``（中文子串匹配无需分词库）；若当前 SQLite
  不支持 trigram（< 3.34），降级为 ``unicode61`` 并把全局标志 ``FTS_TRIGRAM``
  置为 False，检索层据此改用 LIKE。
* 三触发器保证 questions 与 questions_fts 自动同步。
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from typing import Any, Dict, Iterator, List, Optional, Sequence

from . import config

# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------
FTS_TRIGRAM: bool = True
"""True 表示当前 FTS 表使用 trigram 分词器（可 MATCH 中文子串）。"""

_initialized: bool = False
_init_lock = threading.Lock()

FTS_COLUMNS: tuple[str, ...] = ("stem", "options_json", "answer", "analysis")

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------
BASE_DDL: List[str] = [
    """
    CREATE TABLE IF NOT EXISTS imports (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      status TEXT NOT NULL DEFAULT 'parsing',
      total_files INTEGER NOT NULL DEFAULT 0,
      ok_files INTEGER NOT NULL DEFAULT 0,
      failed_files INTEGER NOT NULL DEFAULT 0,
      total_questions INTEGER NOT NULL DEFAULT 0,
      committed_questions INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS import_files (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      import_id INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
      filename TEXT NOT NULL,
      file_type TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'parsed',
      error TEXT NOT NULL DEFAULT '',
      total_questions INTEGER NOT NULL DEFAULT 0,
      anomaly_count INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS questions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      import_file_id INTEGER NOT NULL REFERENCES import_files(id) ON DELETE CASCADE,
      question_no TEXT NOT NULL,
      qtype TEXT NOT NULL,
      stem TEXT NOT NULL,
      options_json TEXT NOT NULL DEFAULT '{}',
      answer TEXT NOT NULL DEFAULT '',
      analysis TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL DEFAULT 'pending',
      created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
      updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_q_file   ON questions(import_file_id)",
    "CREATE INDEX IF NOT EXISTS idx_q_status ON questions(status)",
    "CREATE INDEX IF NOT EXISTS idx_q_type   ON questions(qtype)",
    """
    CREATE TABLE IF NOT EXISTS anomalies (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
      type TEXT NOT NULL,
      detail TEXT NOT NULL DEFAULT '',
      handled INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_a_q     ON anomalies(question_id)",
    "CREATE INDEX IF NOT EXISTS idx_a_state ON anomalies(type, handled)",
    "CREATE INDEX IF NOT EXISTS idx_if_import ON import_files(import_id)",
]

TRIGGER_DDL: List[str] = [
    """
    CREATE TRIGGER IF NOT EXISTS q_ai AFTER INSERT ON questions BEGIN
      INSERT INTO questions_fts(rowid, stem, options_json, answer, analysis)
      VALUES (new.id, new.stem, new.options_json, new.answer, new.analysis);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS q_ad AFTER DELETE ON questions BEGIN
      INSERT INTO questions_fts(questions_fts, rowid, stem, options_json, answer, analysis)
      VALUES ('delete', old.id, old.stem, old.options_json, old.answer, old.analysis);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS q_au AFTER UPDATE ON questions BEGIN
      INSERT INTO questions_fts(questions_fts, rowid, stem, options_json, answer, analysis)
      VALUES ('delete', old.id, old.stem, old.options_json, old.answer, old.analysis);
      INSERT INTO questions_fts(rowid, stem, options_json, answer, analysis)
      VALUES (new.id, new.stem, new.options_json, new.answer, new.analysis);
    END
    """,
]

# ---------------------------------------------------------------------------
# 实时聚合列（v1.1 修复 D1）
# ---------------------------------------------------------------------------
# import_files.anomaly_count / imports.committed_questions 是解析期快照，
# 编辑或删除题目后必然过期。以下子查询在读取侧实时聚合，作为**权威数据源**；
# 表里的冗余列只作为历史快照保留，不再被序列化函数优先采信。
IMPORT_LIVE_COLUMNS: str = """
  (SELECT COUNT(*) FROM questions q
     JOIN import_files f ON f.id = q.import_file_id
     WHERE f.import_id = i.id) AS total_questions_live,
  (SELECT COUNT(*) FROM questions q
     JOIN import_files f ON f.id = q.import_file_id
     WHERE f.import_id = i.id AND q.status = 'published') AS committed_questions_live,
  (SELECT COUNT(*) FROM anomalies a JOIN questions q ON q.id = a.question_id
     JOIN import_files f ON f.id = q.import_file_id
     WHERE f.import_id = i.id AND a.handled = 0) AS anomaly_count_live,
  (SELECT COUNT(*) FROM anomalies a JOIN questions q ON q.id = a.question_id
     JOIN import_files f ON f.id = q.import_file_id
     WHERE f.import_id = i.id) AS anomaly_total_live
"""

FILE_LIVE_COLUMNS: str = """
  (SELECT COUNT(*) FROM questions q WHERE q.import_file_id = f.id) AS total_questions_live,
  (SELECT COUNT(*) FROM questions q
     WHERE q.import_file_id = f.id AND q.status = 'published') AS committed_questions_live,
  (SELECT COUNT(*) FROM anomalies a JOIN questions q ON q.id = a.question_id
     WHERE q.import_file_id = f.id AND a.handled = 0) AS anomaly_count_live,
  (SELECT COUNT(*) FROM anomalies a JOIN questions q ON q.id = a.question_id
     WHERE q.import_file_id = f.id) AS anomaly_total_live
"""


def _fts_ddl(tokenize: str) -> str:
    """生成 FTS5 外部内容表 DDL。"""
    return (
        "CREATE VIRTUAL TABLE IF NOT EXISTS questions_fts USING fts5("
        "  stem, options_json, answer, analysis,"
        "  content='questions', content_rowid='id',"
        f"  tokenize='{tokenize}'"
        ")"
    )


# ---------------------------------------------------------------------------
# 连接管理
# ---------------------------------------------------------------------------
def connect() -> sqlite3.Connection:
    """新建一个连接（开启 WAL 与外键级联）。调用方负责关闭。"""
    config.ensure_dirs()
    conn = sqlite3.connect(str(config.DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextlib.contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """事务化连接上下文：正常结束提交，异常回滚。"""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _create_fts(conn: sqlite3.Connection) -> bool:
    """创建 FTS 表与触发器；返回是否成功使用 trigram 分词器。"""
    trigram_ok = True
    try:
        conn.execute(_fts_ddl("trigram"))
    except sqlite3.OperationalError:
        # 老版本 SQLite 不支持 trigram：删除半成品后降级 unicode61
        conn.execute("DROP TABLE IF EXISTS questions_fts")
        conn.execute(_fts_ddl("unicode61"))
        trigram_ok = False
    for stmt in TRIGGER_DDL:
        conn.execute(stmt)
    return trigram_ok


def init_db() -> None:
    """建库建表（幂等），并重建 FTS 索引以确保一致性。"""
    global FTS_TRIGRAM, _initialized
    with _init_lock:
        if _initialized:
            return
        config.ensure_dirs()
        with get_conn() as conn:
            for stmt in BASE_DDL:
                conn.execute(stmt)
            FTS_TRIGRAM = _create_fts(conn)
            try:
                conn.execute("INSERT INTO questions_fts(questions_fts) VALUES('rebuild')")
            except sqlite3.OperationalError:
                # 空库或 FTS 不可用时忽略
                pass
        _initialized = True


def reset_init_flag() -> None:
    """测试辅助：允许在切换数据库文件后重新初始化。"""
    global _initialized
    with _init_lock:
        _initialized = False


# ---------------------------------------------------------------------------
# 序列化辅助
# ---------------------------------------------------------------------------
def parse_options(raw: Optional[str]) -> Dict[str, str]:
    """把 options_json 列安全解析为 dict；非法内容返回空 dict。"""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    result: Dict[str, str] = {}
    for key, val in value.items():
        result[str(key).upper()] = "" if val is None else str(val)
    return dict(sorted(result.items()))


def dump_options(options: Optional[Dict[str, str]]) -> str:
    """把选项 dict 序列化为 JSON 字符串（按键排序，便于稳定 diff）。"""
    if not options:
        return "{}"
    normalized: Dict[str, str] = {}
    for key, val in options.items():
        letter = str(key).strip().upper()
        if not letter:
            continue
        normalized[letter] = "" if val is None else str(val).strip()
    return json.dumps(dict(sorted(normalized.items())), ensure_ascii=False)


def anomaly_to_dict(row: sqlite3.Row | Dict[str, Any]) -> Dict[str, Any]:
    """anomalies 行 → dict。"""
    data = dict(row)
    return {
        "id": int(data["id"]),
        "question_id": int(data["question_id"]),
        "type": data["type"],
        "detail": data["detail"] or "",
        "handled": int(data["handled"] or 0),
        "created_at": data.get("created_at"),
    }


def question_to_dict(
    row: sqlite3.Row | Dict[str, Any],
    anomalies: Optional[Sequence[Dict[str, Any]]] = None,
    filename: Optional[str] = None,
) -> Dict[str, Any]:
    """questions 行 → 架构 §8 约定的题目 JSON。"""
    data = dict(row)
    result: Dict[str, Any] = {
        "id": int(data["id"]),
        "import_file_id": int(data["import_file_id"]),
        "question_no": data["question_no"],
        "qtype": data["qtype"],
        "stem": data["stem"],
        "options": parse_options(data.get("options_json")),
        "answer": data["answer"] or "",
        "analysis": data["analysis"] or "",
        "status": data["status"],
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "anomalies": list(anomalies or []),
    }
    if filename is not None:
        result["filename"] = filename
    return result


def _pick_int(data: Dict[str, Any], *keys: str, default: int = 0) -> int:
    """按优先级取第一个存在且非 None 的整数字段（用于「实时值优先」）。"""
    for key in keys:
        if key in data and data[key] is not None:
            return int(data[key])
    return default


def import_to_dict(row: sqlite3.Row | Dict[str, Any]) -> Dict[str, Any]:
    """imports 行 → dict。

    计数类字段优先采用实时聚合列（``*_live``），表内冗余列仅作兜底（v1.1 D1）。
    """
    data = dict(row)
    return {
        "id": int(data["id"]),
        "status": data["status"],
        "total_files": int(data["total_files"] or 0),
        "ok_files": int(data["ok_files"] or 0),
        "failed_files": int(data["failed_files"] or 0),
        "total_questions": _pick_int(data, "total_questions_live", "total_questions"),
        "committed_questions": _pick_int(
            data, "committed_questions_live", "committed_questions"
        ),
        "anomaly_count": _pick_int(data, "anomaly_count_live", "anomaly_count"),
        "anomaly_total": _pick_int(
            data, "anomaly_total_live", "anomaly_count_live", "anomaly_count"
        ),
        "created_at": data.get("created_at"),
    }


def import_file_to_dict(row: sqlite3.Row | Dict[str, Any]) -> Dict[str, Any]:
    """import_files 行 → dict（同时给出 id 与 file_id 两个别名）。

    ``anomaly_count`` 为**未处理**异常数实时值；``anomaly_total`` 为该文件全部异常数实时值。
    """
    data = dict(row)
    file_id = int(data["id"])
    return {
        "id": file_id,
        "file_id": file_id,
        "import_id": int(data["import_id"]),
        "filename": data["filename"],
        "file_type": data["file_type"],
        "status": data["status"],
        "error": data["error"] or "",
        "total_questions": _pick_int(data, "total_questions_live", "total_questions"),
        "committed_questions": _pick_int(data, "committed_questions_live"),
        "anomaly_count": _pick_int(data, "anomaly_count_live", "anomaly_count"),
        "anomaly_total": _pick_int(
            data, "anomaly_total_live", "anomaly_count_live", "anomaly_count"
        ),
        "created_at": data.get("created_at"),
    }


def fetch_anomalies(
    conn: sqlite3.Connection, question_ids: Sequence[int]
) -> Dict[int, List[Dict[str, Any]]]:
    """批量取多道题的异常，返回 {question_id: [anomaly, ...]}。"""
    if not question_ids:
        return {}
    placeholders = ",".join("?" for _ in question_ids)
    rows = conn.execute(
        f"SELECT * FROM anomalies WHERE question_id IN ({placeholders}) ORDER BY id ASC",
        tuple(int(qid) for qid in question_ids),
    ).fetchall()
    grouped: Dict[int, List[Dict[str, Any]]] = {int(qid): [] for qid in question_ids}
    for row in rows:
        item = anomaly_to_dict(row)
        grouped.setdefault(item["question_id"], []).append(item)
    return grouped


def fetch_question(
    conn: sqlite3.Connection, question_id: int, with_filename: bool = True
) -> Optional[Dict[str, Any]]:
    """按 id 取单题（含异常与来源文件名）；不存在返回 None。"""
    if with_filename:
        row = conn.execute(
            """
            SELECT q.*, f.filename AS filename
            FROM questions q LEFT JOIN import_files f ON f.id = q.import_file_id
            WHERE q.id = ?
            """,
            (int(question_id),),
        ).fetchone()
    else:
        row = conn.execute("SELECT * FROM questions WHERE id = ?", (int(question_id),)).fetchone()
    if row is None:
        return None
    anomalies = fetch_anomalies(conn, [question_id]).get(int(question_id), [])
    filename = dict(row).get("filename") if with_filename else None
    return question_to_dict(row, anomalies, filename=filename)


def count_same_number(conn: sqlite3.Connection, import_file_id: int, question_no: str) -> int:
    """统计同一文件内相同题号的数量（用于 duplicate_number 复算）。"""
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM questions WHERE import_file_id = ? AND question_no = ?",
        (int(import_file_id), str(question_no)),
    ).fetchone()
    return int(row["c"]) if row else 0
