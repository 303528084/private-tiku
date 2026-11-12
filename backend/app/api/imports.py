"""导入相关 API：上传解析 / 列表 / 详情 / 确认入库 / 删除。

流程（架构 §4）：multipart 多文件上传 → 落盘 → 同步解析 → 暂存（status=pending）
→ 用户确认 commit 后转为 published。
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from fastapi import APIRouter, File, Query, Request, UploadFile

from .. import config, db
from ..models import (
    CODE_NOT_FOUND,
    CODE_PARAM_ERROR,
    CODE_PARSE_FAILED,
    CODE_UNSUPPORTED_TYPE,
    ApiError,
    FileStatus,
    ImportStatus,
    QuestionStatus,
    ok,
)
from ..parser import pipeline
from ..parser.reader import ReaderError

router = APIRouter(prefix="/imports", tags=["imports"])

FORM_FIELD_NAMES = ("files[]", "files")


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _is_upload(value: Any) -> bool:
    """判断 form 值是否为上传文件对象。"""
    return getattr(value, "filename", None) is not None and hasattr(value, "read")


async def _collect_uploads(
    request: Request, declared: Optional[Sequence[UploadFile]]
) -> List[Any]:
    """收集上传文件：优先用声明参数，缺省时回退解析 multipart form。"""
    uploads: List[Any] = list(declared or [])
    if uploads:
        return uploads
    try:
        form = await request.form()
    except Exception as exc:  # pragma: no cover - 非法 multipart
        raise ApiError(f"表单解析失败：{exc}", CODE_PARAM_ERROR, 400) from exc
    for key, value in form.multi_items():
        if key not in FORM_FIELD_NAMES:
            continue
        if _is_upload(value):
            uploads.append(value)
    return uploads


def _ensure_import(conn: Any, import_id: int) -> Dict[str, Any]:
    """取导入记录（含实时聚合计数），不存在则抛 404。"""
    row = conn.execute(
        f"SELECT i.*, {db.IMPORT_LIVE_COLUMNS} FROM imports i WHERE i.id = ?",
        (int(import_id),),
    ).fetchone()
    if row is None:
        raise ApiError(f"导入记录 {import_id} 不存在", CODE_NOT_FOUND, 404)
    return db.import_to_dict(row)


def _insert_parsed_items(conn: Any, file_id: int, items: Sequence[pipeline.ParsedItem]) -> int:
    """把解析结果写入 questions / anomalies（status=pending）。"""
    anomaly_total = 0
    for item in items:
        parsed = item.parsed
        cursor = conn.execute(
            """
            INSERT INTO questions (import_file_id, question_no, qtype, stem, options_json,
                                   answer, analysis, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(file_id),
                parsed.no,
                parsed.qtype,
                parsed.stem,
                db.dump_options(parsed.options),
                parsed.answer,
                parsed.analysis,
                QuestionStatus.pending.value,
            ),
        )
        question_id = int(cursor.lastrowid)
        for anomaly in item.anomalies:
            conn.execute(
                "INSERT INTO anomalies (question_id, type, detail, handled) VALUES (?, ?, ?, 0)",
                (question_id, anomaly.type, anomaly.detail),
            )
            anomaly_total += 1
    return anomaly_total


# ---------------------------------------------------------------------------
# POST /api/imports
# ---------------------------------------------------------------------------
@router.post("")
async def create_import(
    request: Request,
    files: Optional[List[UploadFile]] = File(default=None, alias="files[]"),
) -> Dict[str, Any]:
    """上传并同步解析多个文件，结果暂存待确认。"""
    uploads = await _collect_uploads(request, files)
    if not uploads:
        raise ApiError("未接收到上传文件，表单字段名应为 files[]", CODE_PARAM_ERROR, 400)
    if len(uploads) > config.MAX_FILES_PER_REQUEST:
        raise ApiError(
            f"单次最多上传 {config.MAX_FILES_PER_REQUEST} 个文件，本次 {len(uploads)} 个",
            CODE_PARAM_ERROR,
            400,
        )

    config.ensure_dirs()
    # 1) 先建批次行，拿到 import_id 以便组织文件目录
    with db.get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO imports (status, total_files) VALUES (?, ?)",
            (ImportStatus.parsing.value, len(uploads)),
        )
        import_id = int(cursor.lastrowid)

    upload_dir = config.import_upload_dir(import_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    # 2) 逐文件落盘 + 解析（不占用写事务）
    staged: List[Dict[str, Any]] = []
    for upload in uploads:
        filename = (getattr(upload, "filename", "") or "unnamed").strip()
        suffix = Path(filename).suffix.lower()
        entry: Dict[str, Any] = {
            "filename": filename,
            "file_type": config.FILE_TYPE_BY_EXT.get(suffix, suffix.lstrip(".") or "unknown"),
            "ok": False,
            "error": "",
            # 一个文件可产出多个题库分片（JSON 多题库包）；文本类恒为 1 个
            "parts": [],
        }
        if suffix not in config.ALLOWED_EXT:
            # 文案唯一来源：config.UNSUPPORTED_EXT_TEMPLATE（前端 constants/import.ts 同字面量）
            entry["error"] = config.unsupported_ext_message(suffix)
            staged.append(entry)
            continue
        try:
            content = await upload.read()
        except Exception as exc:  # pragma: no cover - 上传中断
            entry["error"] = f"读取上传内容失败：{exc}"
            staged.append(entry)
            continue
        if not content:
            entry["error"] = "文件内容为空"
            staged.append(entry)
            continue
        if len(content) > config.MAX_FILE_SIZE:
            entry["error"] = (
                f"文件超过大小上限 {config.MAX_FILE_SIZE // (1024 * 1024)}MB"
            )
            staged.append(entry)
            continue

        target = upload_dir / f"{uuid.uuid4().hex}{suffix}"
        try:
            target.write_bytes(content)
        except OSError as exc:  # pragma: no cover - 磁盘异常
            entry["error"] = f"保存文件失败：{exc}"
            staged.append(entry)
            continue

        try:
            entry["parts"] = pipeline.run_parts(target, display_name=filename)
            entry["ok"] = True
        except ReaderError as exc:
            entry["error"] = str(exc)
        except Exception as exc:  # pragma: no cover - 兜底
            entry["error"] = f"解析失败：{exc}"
        staged.append(entry)

    # 3) 单事务写库（一个题库分片 = 一条 import_files 记录）
    ok_files = 0
    failed_files = 0
    total_questions = 0
    file_payloads: List[Dict[str, Any]] = []
    with db.get_conn() as conn:
        for entry in staged:
            entry_ok = bool(entry["ok"])
            parts: List[pipeline.PartResult] = (
                list(entry["parts"]) if entry_ok else [pipeline.PartResult(name=entry["filename"])]
            )
            for part in parts:
                if entry_ok and part.question_count > 0:
                    status = FileStatus.parsed.value
                    question_count = part.question_count
                    ok_files += 1
                else:
                    # 失败文件 / 空分片仍留一条记录便于溯源（v1.0 行为）
                    status = FileStatus.failed.value
                    question_count = 0
                    failed_files += 1
                cursor = conn.execute(
                    """
                    INSERT INTO import_files (import_id, filename, file_type, status, error, total_questions)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        import_id,
                        part.name,
                        entry["file_type"],
                        status,
                        entry["error"],
                        question_count,
                    ),
                )
                file_id = int(cursor.lastrowid)
                anomaly_count = 0
                if status == FileStatus.parsed.value:
                    anomaly_count = _insert_parsed_items(conn, file_id, part.items)
                    total_questions += question_count
                # 冗余列仅作历史快照；读取侧一律用实时聚合（v1.1 D1）
                conn.execute(
                    "UPDATE import_files SET anomaly_count = ? WHERE id = ?",
                    (anomaly_count, file_id),
                )
                file_payloads.append(
                    {
                        "file_id": file_id,
                        "id": file_id,
                        "filename": part.name,
                        "file_type": entry["file_type"],
                        "status": status,
                        "error": entry["error"],
                        "total_questions": question_count,
                        "committed_questions": 0,
                        "anomaly_count": anomaly_count,
                        "anomaly_total": anomaly_count,
                    }
                )

        final_status = (
            ImportStatus.pending_confirm.value if ok_files > 0 else ImportStatus.failed.value
        )
        conn.execute(
            """
            UPDATE imports
            SET status = ?, total_files = ?, ok_files = ?, failed_files = ?, total_questions = ?
            WHERE id = ?
            """,
            (final_status, len(file_payloads), ok_files, failed_files, total_questions, import_id),
        )

    if ok_files == 0:
        # 「全部是类型错误」判定与文案同源：只看 config.UNSUPPORTED_EXT_PREFIX 前缀，
        # 避免把解析失败（文件内容为空 / 无题目标题）误判成类型错误。
        only_type_error = all(
            item["error"].startswith(config.UNSUPPORTED_EXT_PREFIX) for item in file_payloads
        )
        if only_type_error:
            raise ApiError(
                f"全部文件类型不受支持，仅接受 {config.ALLOWED_EXT_HINT}",
                CODE_UNSUPPORTED_TYPE,
                422,
                {"import_id": import_id, "files": file_payloads},
            )
        raise ApiError(
            "全部文件解析失败",
            CODE_PARSE_FAILED,
            500,
            {"import_id": import_id, "files": file_payloads},
        )

    return ok({"import_id": import_id, "files": file_payloads})


# ---------------------------------------------------------------------------
# GET /api/imports
# ---------------------------------------------------------------------------
@router.get("")
def list_imports(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=config.DEFAULT_PAGE_SIZE, ge=1, le=config.MAX_PAGE_SIZE),
) -> Dict[str, Any]:
    """导入记录列表（按 id 倒序）。"""
    offset = (page - 1) * size
    with db.get_conn() as conn:
        total_row = conn.execute("SELECT COUNT(*) AS c FROM imports").fetchone()
        rows = conn.execute(
            f"""
            SELECT i.*, {db.IMPORT_LIVE_COLUMNS}
            FROM imports i
            ORDER BY i.id DESC
            LIMIT ? OFFSET ?
            """,
            (size, offset),
        ).fetchall()
    return ok({"items": [db.import_to_dict(row) for row in rows], "total": int(total_row["c"])})


# ---------------------------------------------------------------------------
# GET /api/imports/{import_id}
# ---------------------------------------------------------------------------
@router.get("/{import_id}")
def get_import(import_id: int) -> Dict[str, Any]:
    """导入详情（含文件明细；计数均为实时聚合值）。"""
    with db.get_conn() as conn:
        record = _ensure_import(conn, import_id)
        rows = conn.execute(
            f"""
            SELECT f.*, {db.FILE_LIVE_COLUMNS}
            FROM import_files f
            WHERE f.import_id = ?
            ORDER BY f.id ASC
            """,
            (int(import_id),),
        ).fetchall()
    return ok({"import": record, "files": [db.import_file_to_dict(row) for row in rows]})


# ---------------------------------------------------------------------------
# POST /api/imports/{import_id}/commit
# ---------------------------------------------------------------------------
@router.post("/{import_id}/commit")
def commit_import(import_id: int) -> Dict[str, Any]:
    """确认入库：把该批次下所有 pending 题目转为 published。"""
    with db.get_conn() as conn:
        _ensure_import(conn, import_id)
        cursor = conn.execute(
            """
            UPDATE questions
            SET status = ?, updated_at = datetime('now','localtime')
            WHERE status = ?
              AND import_file_id IN (SELECT id FROM import_files WHERE import_id = ?)
            """,
            (QuestionStatus.published.value, QuestionStatus.pending.value, int(import_id)),
        )
        committed = int(cursor.rowcount or 0)
        total_row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM questions
            WHERE status = ? AND import_file_id IN (
                SELECT id FROM import_files WHERE import_id = ?
            )
            """,
            (QuestionStatus.published.value, int(import_id)),
        ).fetchone()
        conn.execute(
            "UPDATE imports SET status = ?, committed_questions = ? WHERE id = ?",
            (ImportStatus.committed.value, int(total_row["c"]), int(import_id)),
        )
    return ok({"committed": committed})


# ---------------------------------------------------------------------------
# DELETE /api/imports/{import_id}
# ---------------------------------------------------------------------------
@router.delete("/{import_id}")
def delete_import(import_id: int) -> Dict[str, Any]:
    """删除导入批次及其题目/异常（DB 级联 + 清理落盘文件）。"""
    with db.get_conn() as conn:
        _ensure_import(conn, import_id)
        file_row = conn.execute(
            "SELECT COUNT(*) AS c FROM import_files WHERE import_id = ?", (int(import_id),)
        ).fetchone()
        question_row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM questions
            WHERE import_file_id IN (SELECT id FROM import_files WHERE import_id = ?)
            """,
            (int(import_id),),
        ).fetchone()
        # 显式删除题目以触发 FTS 同步触发器（FK 级联删除不会触发 AFTER DELETE 触发器）
        conn.execute(
            """
            DELETE FROM questions
            WHERE import_file_id IN (SELECT id FROM import_files WHERE import_id = ?)
            """,
            (int(import_id),),
        )
        conn.execute("DELETE FROM import_files WHERE import_id = ?", (int(import_id),))
        conn.execute("DELETE FROM imports WHERE id = ?", (int(import_id),))

    upload_dir = config.import_upload_dir(int(import_id))
    if upload_dir.is_dir():
        shutil.rmtree(upload_dir, ignore_errors=True)

    return ok(
        {
            "deleted": 1,
            "deleted_files": int(file_row["c"]),
            "deleted_questions": int(question_row["c"]),
        }
    )
