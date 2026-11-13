"""导入相关 API：上传解析 / 分享包导入 / 列表 / 详情 / 确认入库 / 删除。

流程（架构 §4）：multipart 多文件上传 → 落盘 → 同步解析 → 暂存（status=pending）
→ 用户确认 commit 后转为 published。

v1.4 新增 ``POST /api/imports/share-pack``：分享包（ZIP）走「解压 → 校验 →
查重 → 备份 → 直接 published」的另一条链路，**不需要**再确认入库。
"""

from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from fastapi import APIRouter, File, Query, Request, UploadFile

from .. import backup, config, db
from ..models import (
    CODE_INTERNAL_ERROR,
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
from ..parser.extractor import Parsed
from ..parser.reader import ReaderError
from ..share import dedup
from ..share.unpack import SharePackError, cleanup, extract_share_pack

logger = logging.getLogger("private_tiku.api.imports")

router = APIRouter(prefix="/imports", tags=["imports"])

FORM_FIELD_NAMES = ("files[]", "files")
SHARE_FIELD_NAMES = ("file", "files[]", "files")
"""分享包上传接受的表单字段名（``file`` 优先，兼容 ``files[]``）。"""



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


def _insert_parsed_items(
    conn: Any,
    file_id: int,
    items: Sequence[pipeline.ParsedItem],
    status: str = QuestionStatus.pending.value,
) -> int:
    """把解析结果写入 questions / anomalies。

    Args:
        conn: 数据库连接（调用方负责事务）。
        file_id: ``import_files.id``。
        items: 解析结果。
        status: 写入的题目状态。普通导入为 ``pending``（需人工确认）；
            分享包导入为 ``published``（包内是已校对内容，见 T03）。

    Returns:
        写入的异常条数。
    """
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
                status,
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
# POST /api/imports/share-pack（v1.4 T03）
# ---------------------------------------------------------------------------
def _is_empty_question(parsed: Parsed) -> bool:
    """题干 / 选项 / 答案全空的「空题」不入库（第三方分享包可能出现空块）。"""
    return not (parsed.stem or parsed.options or parsed.answer)


async def _collect_share_upload(
    request: Request, declared: Optional[UploadFile]
) -> Any:
    """取分享包上传对象：优先声明参数，缺省时回退解析 multipart form。"""
    if declared is not None and _is_upload(declared):
        return declared
    try:
        form = await request.form()
    except Exception as exc:  # pragma: no cover - 非法 multipart
        raise ApiError(f"表单解析失败：{exc}", CODE_PARAM_ERROR, 400) from exc
    for key, value in form.multi_items():
        if key in SHARE_FIELD_NAMES and _is_upload(value):
            return value
    raise ApiError(
        "未接收到分享包文件，表单字段名应为 file（.zip）", CODE_PARAM_ERROR, 400
    )


@router.post("/share-pack")
async def import_share_pack(
    request: Request,
    file: Optional[UploadFile] = File(default=None, description="分享包 .zip"),
) -> Dict[str, Any]:
    """导入分享包：解压 → 校验 → 查重 → **备份** → 写入（published）。

    执行顺序（不可调换）：

    1. 解压到 ``DATA_DIR/share_staging/<uuid>``（安全规则见
       :mod:`app.share.unpack`；被拒绝的包不会留下任何文件）；
    2. 解析 ``bank.md`` → 计算内容指纹 → 与库内**全部**题目比对；
    3. **备份主库**（``backups/pre-import-*.db`` + ``integrity_check``）；
       备份失败直接中止，库内数据不变；
    4. 单事务写入 ``imports`` / ``import_files`` / ``questions``（``published``）。

    语义：**新增、不覆盖**。重复题（指纹相同）不写入，只计入 ``duplicates``；
    同 ``question_no`` 但内容不同 → 视为不同题，两条都保留。

    Returns:
        ``{import_id, file_id, added, duplicates, skipped, total_in_pack, backup_path}``；
        全部重复时 ``import_id`` / ``file_id`` / ``backup_path`` 为 ``None``（不写库也不备份）。

    Raises:
        ApiError: 文件缺失/非 zip/超限（40001）、包不合法（40001）、备份失败（50000）。
    """
    upload = await _collect_share_upload(request, file)
    filename = (getattr(upload, "filename", "") or "share-pack.zip").strip()
    if Path(filename).suffix.lower() != ".zip":
        raise ApiError("分享包必须是 .zip 文件", CODE_PARAM_ERROR, 400)

    try:
        data = await upload.read()
    except Exception as exc:  # pragma: no cover - 上传中断
        raise ApiError(f"读取上传内容失败：{exc}", CODE_PARAM_ERROR, 400) from exc
    if not data:
        raise ApiError("文件内容为空", CODE_PARAM_ERROR, 400)
    if len(data) > config.MAX_FILE_SIZE:
        raise ApiError(
            f"分享包超过大小上限 {config.MAX_FILE_SIZE // (1024 * 1024)}MB",
            CODE_PARAM_ERROR,
            400,
        )

    config.ensure_dirs()
    staging = config.DATA_DIR / config.SHARE_STAGING_DIRNAME / uuid.uuid4().hex
    try:
        try:
            extracted = extract_share_pack(data, staging)
        except SharePackError as exc:
            logger.warning("分享包被拒绝：%s", exc)
            raise ApiError(str(exc), CODE_PARAM_ERROR, 400) from exc

        manifest: Dict[str, Any] = extracted["manifest"]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bank_name = str(manifest.get("bank_name") or "").strip() or f"分享包-{stamp}"

        try:
            parts = pipeline.run_parts(extracted["bank_path"], display_name=bank_name)
        except ReaderError as exc:
            raise ApiError(f"分享包题库正文解析失败：{exc}", CODE_PARAM_ERROR, 400) from exc
        items = [item for part in parts for item in part.items]
        total_in_pack = len(items)
        if total_in_pack == 0:
            raise ApiError("分享包内没有可导入的题目", CODE_PARAM_ERROR, 400)

        # ---- 查重（v1.4 不加列：对存量题现场算指纹）----
        with db.get_conn() as conn:
            known = dedup.existing_hashes(conn)

        selected: List[pipeline.ParsedItem] = []
        duplicates = 0
        skipped = 0
        seen = set(known)
        for item in items:
            if _is_empty_question(item.parsed):
                skipped += 1
                continue
            digest = dedup.content_hash(item.parsed)
            if digest in seen:
                duplicates += 1
                continue
            seen.add(digest)
            selected.append(item)

        if not selected:
            logger.info(
                "分享包无新增题目：total=%d duplicates=%d skipped=%d",
                total_in_pack,
                duplicates,
                skipped,
            )
            return ok(
                {
                    "import_id": None,
                    "file_id": None,
                    "added": 0,
                    "duplicates": duplicates,
                    "skipped": skipped,
                    "total_in_pack": total_in_pack,
                    "backup_path": None,
                },
                "没有新增题目（全部为重复题）",
            )

        # ---- 备份必须在写库之前 ----
        try:
            backup_path = backup.backup_database(
                backup.backup_dir() / f"pre-import-{stamp}.db"
            )
        except backup.BackupError as exc:
            logger.error("导入前备份失败，已中止导入：%s", exc)
            raise ApiError(
                f"导入前备份失败，已中止导入（库内数据未改变）：{exc}",
                CODE_INTERNAL_ERROR,
                500,
            ) from exc

        # ---- 单事务写入 ----
        with db.get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO imports (status, total_files, ok_files, failed_files,
                                     total_questions, committed_questions)
                VALUES (?, 1, 1, 0, ?, ?)
                """,
                (ImportStatus.committed.value, len(selected), len(selected)),
            )
            import_id = int(cursor.lastrowid)
            cursor = conn.execute(
                """
                INSERT INTO import_files (import_id, filename, file_type, status, error,
                                          total_questions)
                VALUES (?, ?, ?, ?, '', ?)
                """,
                (
                    import_id,
                    bank_name,
                    config.SHARE_FILE_TYPE,
                    FileStatus.parsed.value,
                    len(selected),
                ),
            )
            file_id = int(cursor.lastrowid)
            anomaly_count = _insert_parsed_items(
                conn, file_id, selected, status=QuestionStatus.published.value
            )
            conn.execute(
                "UPDATE import_files SET anomaly_count = ? WHERE id = ?",
                (anomaly_count, file_id),
            )

        logger.info(
            "分享包导入完成：import_id=%s added=%d duplicates=%d skipped=%d 备份=%s",
            import_id,
            len(selected),
            duplicates,
            skipped,
            backup_path,
        )
        return ok(
            {
                "import_id": import_id,
                "file_id": file_id,
                "added": len(selected),
                "duplicates": duplicates,
                "skipped": skipped,
                "total_in_pack": total_in_pack,
                "backup_path": str(backup_path),
            }
        )
    finally:
        cleanup(staging)


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
