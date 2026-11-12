"""题目 API：手录新增 / 详情 / 编辑（重算异常）/ 删除。

v1.3 新增 ``POST /api/questions``（手动录题）：题目直接 ``published``，库载体复用
``import_files``（``file_type='manual'``），异常与编辑/导入走**同一个** ``validate()``
入口。``PUT`` 的校验口径保持不变（校对路径必须能修正坏数据）。
"""

from __future__ import annotations

import re
from typing import Any, Dict

from fastapi import APIRouter

from .. import config, db
from ..models import (
    CODE_NOT_FOUND,
    CODE_PARAM_ERROR,
    ApiError,
    QuestionCreateRequest,
    QuestionUpdateRequest,
    ok,
)
from ..parser import jsonbank
from ..parser.extractor import (
    JUDGE_FALSE_MARK,
    JUDGE_TRUE_MARK,
    Parsed,
    make_parsed,
    normalize_answer,
)
from ..parser.validator import validate

router = APIRouter(prefix="/questions", tags=["questions"])

# 手录支持的题型（``unknown`` 只能出现在历史数据里，不允许新录）
CREATE_QTYPES: frozenset[str] = frozenset({"single", "multiple", "judge"})

_NON_LETTER_RE = re.compile(r"[^A-Za-z]")


def _load_question(conn: Any, question_id: int) -> Dict[str, Any]:
    """读取题目，不存在则抛 404。"""
    question = db.fetch_question(conn, int(question_id))
    if question is None:
        raise ApiError(f"题目 {question_id} 不存在", CODE_NOT_FOUND, 404)
    return question


def _filled_options(raw: Dict[str, str] | None) -> Dict[str, str]:
    """只保留**内容非空**的选项（避免 ``{"D": ""}`` 造成字母空洞）。"""
    options: Dict[str, str] = {}
    for key, value in (raw or {}).items():
        letter = str(key).strip().upper()
        text = ("" if value is None else str(value)).strip()
        if re.fullmatch(r"[A-H]", letter) and text:
            options[letter] = text
    return dict(sorted(options.items()))


@router.post("")
def create_question(payload: QuestionCreateRequest) -> Dict[str, Any]:
    """手动新增单题（直接 published，来源=所选题库名称）。

    校验口径（设计 §3，共 7 条错误分支，message 逐字固定）：

    1. 题干为空 → ``题干不能为空``
    2. 题库名为空白 → ``题库名称不能为空``
    3. 题库名 >120 字 / 题号 >32 字 → ``题库名称最多 120 字`` / ``题号最多 32 字``
    4. 题型缺失或非法 → ``题型只能是 single / multiple / judge``
    5. 选择题答案含未填写选项的字母 → ``答案「BD」中的 D 不在已填写选项内（现有：A、B、C）``
    6. 判断题答案不是 √ / × → ``判断题答案只能是 √ 或 ×``
    7. 选择题答案为空 → ``请填写答案``

    题干 1–2 字、选项 1–3 个、显式重复题号**均不拦**，交 ``validate()`` 落
    ``empty_stem`` / ``too_few_options`` / ``duplicate_number`` 异常（与导入一致）。

    Args:
        payload: 请求体（``QuestionUpdateRequest`` 字段 + ``library_name`` + ``question_no``）。

    Returns:
        ``{"question": {...}, "anomalies": [...], "library": {id,name,file_type,created}}``。
    """
    # 1) 题干
    stem = (payload.stem or "").strip()
    if not stem:
        raise ApiError("题干不能为空", CODE_PARAM_ERROR, 400)

    # 2) 题库名称（省略 → 缺省值；显式空白 → 报错）
    if payload.library_name is None:
        library_name = config.DEFAULT_LIBRARY_NAME
    else:
        library_name = payload.library_name.strip()
        if not library_name:
            raise ApiError("题库名称不能为空", CODE_PARAM_ERROR, 400)

    # 3) 长度上限
    if len(library_name) > config.MAX_LIBRARY_NAME_LEN:
        raise ApiError(
            f"题库名称最多 {config.MAX_LIBRARY_NAME_LEN} 字", CODE_PARAM_ERROR, 400
        )
    question_no = (payload.question_no or "").strip()
    if len(question_no) > config.MAX_QUESTION_NO_LEN:
        raise ApiError(f"题号最多 {config.MAX_QUESTION_NO_LEN} 字", CODE_PARAM_ERROR, 400)

    # 4) 题型
    qtype = (payload.qtype or "").strip().lower()
    if qtype not in CREATE_QTYPES:
        raise ApiError("题型只能是 single / multiple / judge", CODE_PARAM_ERROR, 400)

    options = _filled_options(payload.options)
    answer_raw = (payload.answer or "").strip()

    # 5)/6)/7) 答案校验
    if qtype == "judge":
        judge_answer = normalize_answer(answer_raw, "judge")
        if judge_answer not in (JUDGE_TRUE_MARK, JUDGE_FALSE_MARK):
            raise ApiError("判断题答案只能是 √ 或 ×", CODE_PARAM_ERROR, 400)
        answer_value = judge_answer
    else:
        letters = _NON_LETTER_RE.sub("", answer_raw).upper()
        if not letters:
            raise ApiError("请填写答案", CODE_PARAM_ERROR, 400)
        missing = [letter for letter in letters if letter not in options]
        if missing:
            existing = "、".join(sorted(options)) if options else "无"
            raise ApiError(
                f"答案「{letters}」中的 {missing[0]} 不在已填写选项内（现有：{existing}）",
                CODE_PARAM_ERROR,
                400,
            )
        answer_value = letters

    # 题型与答案字母个数冲突时以答案为准（与 JSON / Markdown 导入同一口径）
    resolved_qtype = jsonbank.resolve_qtype(qtype, options, answer_value)

    with db.get_conn() as conn:
        library_row = db.find_manual_library(conn, library_name)
        if library_row is None:
            library_info = db.create_manual_library(conn, library_name)
        else:
            library_info = {
                "id": int(library_row["id"]),
                "import_id": int(library_row["import_id"]),
                "name": library_row["filename"],
                "file_type": library_row["file_type"],
                "created": False,
            }

        file_id = int(library_info["id"])
        no = question_no or db.next_question_no(conn, file_id)
        parsed: Parsed = make_parsed(
            no=no,
            stem=stem,
            options=options,
            answer=answer_value,
            analysis=payload.analysis or "",
            qtype=resolved_qtype,
        )

        cursor = conn.execute(
            """
            INSERT INTO questions (import_file_id, question_no, qtype, stem, options_json,
                                   answer, analysis, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                parsed.no,
                parsed.qtype,
                parsed.stem,
                db.dump_options(parsed.options),
                parsed.answer,
                parsed.analysis,
                "published",
            ),
        )
        question_id = int(cursor.lastrowid)

        # 与 PUT 同口径：插入后按「同题号出现次数」全量计算异常
        same_number_count = db.count_same_number(conn, file_id, parsed.no)
        counter = {parsed.no: max(same_number_count, 1)}
        anomalies = validate(parsed, counter)
        for anomaly in anomalies:
            conn.execute(
                "INSERT INTO anomalies (question_id, type, detail, handled) VALUES (?, ?, ?, 0)",
                (question_id, anomaly.type, anomaly.detail),
            )

        question = await_fresh(conn, question_id)

    return ok(
        {
            "question": question,
            "anomalies": question["anomalies"],
            "library": library_info,
        }
    )


@router.get("/{question_id}")
def get_question(question_id: int) -> Dict[str, Any]:
    """题目详情（含异常列表）。"""
    with db.get_conn() as conn:
        question = _load_question(conn, question_id)
    anomalies = question.pop("anomalies", [])
    question["anomalies"] = anomalies
    return ok({"question": question, "anomalies": anomalies})


@router.put("/{question_id}")
def update_question(question_id: int, payload: QuestionUpdateRequest) -> Dict[str, Any]:
    """编辑题目并按最新内容全量重建异常（架构 §8 异常翻新）。

    注意：**不**加手录接口的那 7 条校验 —— 校对路径必须能修正坏数据
    （例如先把答案清空再重填），且避免破坏既有用例。
    """
    with db.get_conn() as conn:
        existing = _load_question(conn, question_id)
        qtype = payload.qtype.value if payload.qtype is not None else None
        parsed = make_parsed(
            no=existing["question_no"],
            stem=payload.stem,
            options=payload.options,
            answer=payload.answer,
            analysis=payload.analysis,
            qtype=qtype,
        )
        if not parsed.stem:
            raise ApiError("题干不能为空", 40001, 400)

        conn.execute(
            """
            UPDATE questions
            SET stem = ?, options_json = ?, answer = ?, analysis = ?, qtype = ?,
                updated_at = datetime('now','localtime')
            WHERE id = ?
            """,
            (
                parsed.stem,
                db.dump_options(parsed.options),
                parsed.answer,
                parsed.analysis,
                parsed.qtype,
                int(question_id),
            ),
        )

        # 全量重建异常，避免脏数据残留
        conn.execute("DELETE FROM anomalies WHERE question_id = ?", (int(question_id),))
        same_number_count = db.count_same_number(
            conn, int(existing["import_file_id"]), parsed.no
        )
        counter = {parsed.no: max(same_number_count, 1)}
        anomalies = validate(parsed, counter)
        for anomaly in anomalies:
            conn.execute(
                "INSERT INTO anomalies (question_id, type, detail, handled) VALUES (?, ?, ?, 0)",
                (int(question_id), anomaly.type, anomaly.detail),
            )

        question = await_fresh(conn, int(question_id))
    return ok({"question": question, "anomalies": question["anomalies"]})


def await_fresh(conn: Any, question_id: int) -> Dict[str, Any]:
    """重新读取题目（同步函数，命名沿用「取最新」语义）。"""
    question = db.fetch_question(conn, question_id)
    if question is None:  # pragma: no cover - 理论上不会发生
        raise ApiError(f"题目 {question_id} 不存在", CODE_NOT_FOUND, 404)
    return question


@router.delete("/{question_id}")
def delete_question(question_id: int) -> Dict[str, Any]:
    """删除题目（AFTER DELETE 触发器同步清理 FTS 索引）。"""
    with db.get_conn() as conn:
        _load_question(conn, question_id)
        conn.execute("DELETE FROM questions WHERE id = ?", (int(question_id),))
    return ok({"deleted": 1})
