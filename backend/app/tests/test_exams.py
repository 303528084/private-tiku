"""练习 / 模拟考试 API 集成测试（v1.5）。

覆盖：开考（抽题/快照/范围筛选）→ 作答（练习即时判分 / 考试不泄题）→
交卷（统一判分、未答题按错计）→ 历史/详情/删除 → 迁移 v2 与错误码。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import pytest
from fastapi.testclient import TestClient

from app import config, db, migrate
from app.main import app

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# ---------------------------------------------------------------------------
# 夹具与工具（与 test_api.py 同一套约定）
# ---------------------------------------------------------------------------
@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterable[TestClient]:
    """每个用例一份全新的临时数据目录 + SQLite 库。"""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "DB_PATH", data_dir / "tiku.db")
    monkeypatch.setattr(config, "UPLOAD_DIR", data_dir / "uploads")
    db.reset_init_flag()
    db.init_db()
    with TestClient(app) as test_client:
        yield test_client
    db.reset_init_flag()


def data_of(response: Any) -> Dict[str, Any]:
    """断言 code=0 并返回 data。"""
    body = response.json()
    assert body["code"] == 0, body
    return body["data"]


def error_of(response: Any, http_status: int, code: int) -> Dict[str, Any]:
    """断言失败响应同时满足 HTTP 状态码与业务码。"""
    assert response.status_code == http_status, response.text
    body = response.json()
    assert body["code"] == code, body
    assert set(body) == {"code", "message", "data"}, body
    return body


SINGLE_MD = """# 单选题库

## 1. [单选题]

脉搏短绌常见于哪种心律失常?

- **A.** 房颤
- **B.** 室颤
- **C.** 房扑
- **D.** 期前收缩

**答案**：A

**解析**：脉搏短绌多见于心房颤动。

---

## 2. [单选题]

正常成人静息心率范围为?

- **A.** 40~60次/分
- **B.** 60~100次/分
- **C.** 100~120次/分
- **D.** 120~140次/分

**答案**：B

---

## 3. [单选题]

无菌持物钳使用错误的是?

- **A.** 钳端向下
- **B.** 就近取物
- **C.** 到远处取物应带容器
- **D.** 干式持物钳4小时更换

**答案**：C

**解析**：无菌持物钳不能就近夹取。

---

## 4. [单选题]

护理程序的步骤顺序是?

- **A.** 评估诊断计划实施评价
- **B.** 诊断评估计划实施评价
- **C.** 评估诊断实施计划评价
- **D.** 诊断计划评估实施评价

**答案**：A
"""


def upload_and_commit(client: TestClient, files: List[Tuple[str, str]]) -> int:
    """上传一个 md 并确认入库，返回批次 id。"""
    payload = [
        ("files[]", (name, content.encode("utf-8"), "application/octet-stream"))
        for name, content in files
    ]
    resp = client.post("/api/imports", files=payload)
    batch = data_of(resp)
    import_id = int(batch["import_id"])
    commit = client.post(f"/api/imports/{import_id}/commit")
    assert commit.json()["code"] == 0, commit.text
    return import_id


def open_exam(client: TestClient, **overrides: Any) -> Dict[str, Any]:
    """开一场练习（默认 3 题、全部题库），返回 data。"""
    body: Dict[str, Any] = {"mode": "practice", "count": 3}
    body.update(overrides)
    resp = client.post("/api/exams", json=body)
    return data_of(resp)


# 抽题是随机的：测试用「题干 → 标准答案」映射定位题目，不依赖抽中顺序
SINGLE_ANSWERS: Dict[str, str] = {
    "脉搏短绌常见于哪种心律失常?": "A",
    "正常成人静息心率范围为?": "B",
    "无菌持物钳使用错误的是?": "C",
    "护理程序的步骤顺序是?": "A",
}
SINGLE_WITH_ANALYSIS: set[str] = {"脉搏短绌常见于哪种心律失常?", "无菌持物钳使用错误的是?"}


def item_by_stem(items: List[Dict[str, Any]], stem: str) -> Dict[str, Any]:
    """按题干找出试卷里的题（找不到即失败）。"""
    matched = [i for i in items if i["stem"] == stem]
    assert len(matched) == 1, f"题干未唯一匹配：{stem}"
    return matched[0]


# ===========================================================================
# 迁移 v2
# ===========================================================================
def test_migration_v2_creates_exam_tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """v2 迁移建出 exams / exam_answers，且幂等（重复执行结果不变）。"""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "DB_PATH", data_dir / "tiku.db")
    monkeypatch.setattr(config, "UPLOAD_DIR", data_dir / "uploads")
    db.reset_init_flag()
    db.init_db()

    conn = sqlite3.connect(str(config.DB_PATH))
    assert migrate.user_version(conn) == 2
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"exams", "exam_answers"} <= tables
    conn.close()

    # 幂等：再跑一遍不报错、版本号不变
    db.reset_init_flag()
    db.init_db()
    conn = sqlite3.connect(str(config.DB_PATH))
    assert migrate.user_version(conn) == 2
    conn.close()
    db.reset_init_flag()


# ===========================================================================
# 开考
# ===========================================================================
def test_create_exam_practice_snapshots(client: TestClient) -> None:
    """练习模式开考：抽题数正确、题目有快照、状态 in_progress。"""
    upload_and_commit(client, [("basic.md", SINGLE_MD)])
    exam = open_exam(client, mode="practice", count=3)
    assert exam["mode"] == "practice"
    assert exam["question_count"] == 3
    assert exam["requested_count"] == 3
    assert exam["status"] == "in_progress"
    assert exam["score"] == 0.0

    detail = data_of(client.get(f"/api/exams/{exam['id']}"))
    items = detail["items"]
    assert len(items) == 3
    assert [item["seq"] for item in items] == [1, 2, 3]
    first = items[0]
    assert first["stem"]
    assert first["options"]
    # 未作答且进行中：不泄露答案（练习模式也等提交后才放开，口径统一）
    assert "answer" not in first
    assert "is_correct" not in first
    assert first["answered"] is False


def test_create_exam_scope_and_qtype_filter(client: TestClient) -> None:
    """按题库与题型筛选抽题。"""
    lib_id = upload_and_commit(client, [("basic.md", SINGLE_MD)])
    exam = open_exam(
        client, mode="exam", count=10, library_ids=[lib_id], qtypes=["single"]
    )
    assert exam["question_count"] == 4  # 库里只有 4 道单选，不足 10 按实际数开考
    detail = data_of(client.get(f"/api/exams/{exam['id']}"))
    assert all(item["qtype"] == "single" for item in detail["items"])
    assert all("answer" not in item for item in detail["items"])  # 考试模式不泄题


def test_create_exam_no_questions_rejected(client: TestClient) -> None:
    """空库开考 → 40001 参数错误。"""
    error_of(
        client.post("/api/exams", json={"mode": "practice", "count": 5}),
        400,
        40001,
    )


def test_create_exam_invalid_params(client: TestClient) -> None:
    """mode / qtypes / count 非法 → 40001。"""
    upload_and_commit(client, [("basic.md", SINGLE_MD)])
    error_of(
        client.post("/api/exams", json={"mode": "quiz", "count": 3}),
        400,
        40001,
    )
    error_of(
        client.post("/api/exams", json={"mode": "exam", "count": 3, "qtypes": ["true"]}),
        400,
        40001,
    )
    error_of(
        client.post("/api/exams", json={"mode": "exam", "count": 0}),
        400,  # Pydantic ge=1 校验 → RequestValidationError → 40001 外壳
        40001,
    )
    error_of(
        client.post("/api/exams", json={"mode": "exam", "count": 201}),
        400,
        40001,
    )


def test_create_exam_excludes_anomalous_and_unknown(client: TestClient) -> None:
    """缺答案/unknown 题型/未入库题不进试卷。"""
    dirty_md = """# 脏题库

## 1. [单选题]

标准题?

- **A.** 甲
- **B.** 乙
- **C.** 丙
- **D.** 丁

**答案**：A

---

## 2. [未知题型]

没有答案的题?

- **A.** 甲
- **B.** 乙

---

## 3. [单选题]

缺答案的题?

- **A.** 甲
- **B.** 乙
- **C.** 丙
- **D.** 丁
"""
    upload_and_commit(client, [("dirty.md", dirty_md)])
    exam = open_exam(client, mode="practice", count=10)
    assert exam["question_count"] == 1  # 只有第 1 题合格
    detail = data_of(client.get(f"/api/exams/{exam['id']}"))
    assert detail["items"][0]["stem"] == "标准题?"


# ===========================================================================
# 练习：逐题即时判分
# ===========================================================================
def test_practice_submit_instant_judge(client: TestClient) -> None:
    """练习模式提交单题立刻返回对错与正确答案。"""
    upload_and_commit(client, [("basic.md", SINGLE_MD)])
    exam = open_exam(client, mode="practice", count=4)
    detail = data_of(client.get(f"/api/exams/{exam['id']}"))
    items = detail["items"]

    # 先答错一次再答对（同题重复提交应覆盖上一次作答）
    q2 = item_by_stem(items, "正常成人静息心率范围为?")
    wrong = data_of(
        client.post(f"/api/exams/{exam['id']}/answers", json={"seq": q2["seq"], "answer": "C"})
    )
    assert wrong["is_correct"] is False
    assert wrong["answer"] == "B"
    right = data_of(
        client.post(f"/api/exams/{exam['id']}/answers", json={"seq": q2["seq"], "answer": "b"})
    )
    assert right["is_correct"] is True  # 小写 b 归一化后判对
    assert right["answer_given"] == "B"

    # 其余全部答对；有解析的题提交后应带回解析
    for stem, answer in SINGLE_ANSWERS.items():
        if stem == "正常成人静息心率范围为?":
            continue
        item = item_by_stem(items, stem)
        result = data_of(
            client.post(f"/api/exams/{exam['id']}/answers", json={"seq": item["seq"], "answer": answer})
        )
        assert result["is_correct"] is True
        assert result["answer"] == answer
        assert (result["analysis"] != "") == (stem in SINGLE_WITH_ANALYSIS)

    exam_data = data_of(client.get(f"/api/exams/{exam['id']}"))["exam"]
    assert exam_data["answered_count"] == 4


def test_practice_answer_normalization(client: TestClient) -> None:
    """作答归一化：判断题「对/×」按标准归一。"""
    judge_md = """# 判断题库

## 1. [判断题]

正常成人腋温范围为36.0~37.0℃。

**答案**：√

---

## 2. [判断题]

输血前需两人核对。

**答案**：×
"""
    upload_and_commit(client, [("judge.md", judge_md)])
    exam = open_exam(client, mode="practice", count=2)
    detail = data_of(client.get(f"/api/exams/{exam['id']}"))

    true_item = item_by_stem(detail["items"], "正常成人腋温范围为36.0~37.0℃。")
    false_item = item_by_stem(detail["items"], "输血前需两人核对。")

    resp = client.post(
        f"/api/exams/{exam['id']}/answers",
        json={"seq": true_item["seq"], "answer": "对"},
    )
    result = data_of(resp)
    assert result["is_correct"] is True
    assert result["answer"] == "√"

    resp = client.post(
        f"/api/exams/{exam['id']}/answers",
        json={"seq": false_item["seq"], "answer": "×"},
    )
    result = data_of(resp)
    assert result["is_correct"] is True
    assert result["answer"] == "×"

    # 答反的情况：「错」答到 √ 题上应判错
    resp = client.post(
        f"/api/exams/{exam['id']}/answers",
        json={"seq": true_item["seq"], "answer": "错"},
    )
    assert data_of(resp)["is_correct"] is False


def test_multiple_answer_normalization(client: TestClient) -> None:
    """多选乱序小写作答归一后判分。"""
    multiple_md = """# 多选题库

## 1. [多选题]

心肺复苏有效指征包括?

- **A.** 可触及大动脉搏动
- **B.** 面色由紫绀转红润
- **C.** 瞳孔由大变小
- **D.** 自主呼吸恢复

**答案**：ABD
"""
    upload_and_commit(client, [("multi.md", multiple_md)])
    exam = open_exam(client, mode="practice", count=1)
    detail = data_of(client.get(f"/api/exams/{exam['id']}"))
    seq = detail["items"][0]["seq"]
    resp = client.post(
        f"/api/exams/{exam['id']}/answers",
        json={"seq": seq, "answer": "d,b,a"},
    )
    result = data_of(resp)
    assert result["is_correct"] is True
    assert result["answer_given"] == "ABD"


# ===========================================================================
# 模拟考试：作答不泄题，交卷统一判分
# ===========================================================================
def test_exam_answer_hidden_until_finish(client: TestClient) -> None:
    """考试模式：作答响应与详情都不含答案；交卷后全部放开。"""
    upload_and_commit(client, [("basic.md", SINGLE_MD)])
    exam = open_exam(client, mode="exam", count=4)
    detail = data_of(client.get(f"/api/exams/{exam['id']}"))
    target = item_by_stem(detail["items"], "脉搏短绌常见于哪种心律失常?")
    target_seq = target["seq"]

    resp = client.post(
        f"/api/exams/{exam['id']}/answers",
        json={"seq": target_seq, "answer": "A"},
    )
    result = data_of(resp)
    assert "is_correct" not in result
    assert "answer" not in result

    # 作答后详情：已作答的题也只放开已作答标记，不给答案（考试语义）
    detail = data_of(client.get(f"/api/exams/{exam['id']}"))
    answered_item = next(i for i in detail["items"] if i["seq"] == target_seq)
    assert answered_item["answered"] is True
    assert "answer" not in answered_item

    finished = data_of(client.post(f"/api/exams/{exam['id']}/finish"))
    assert finished["status"] == "finished"
    assert finished["answered_count"] == 1
    # 只答对 1 题，其余 3 题未答按错计 → 1/4
    assert finished["correct_count"] == 1
    assert finished["score"] == 25.0

    # 交卷后详情：全部放开答案与对错
    detail = data_of(client.get(f"/api/exams/{exam['id']}"))
    for item in detail["items"]:
        assert "answer" in item
        assert "is_correct" in item
    correct_items = [i for i in detail["items"] if i["is_correct"] is True]
    assert len(correct_items) == 1
    assert correct_items[0]["seq"] == target_seq
    unanswered = [i for i in detail["items"] if i["seq"] != target_seq]
    assert all(i["answered"] is False for i in unanswered)
    assert all(i["is_correct"] is False for i in unanswered)


def test_exam_judged_on_finish(client: TestClient) -> None:
    """考试模式交卷时判分（作答期间 is_correct 保持 NULL）。"""
    upload_and_commit(client, [("basic.md", SINGLE_MD)])
    exam = open_exam(client, mode="exam", count=4)
    detail = data_of(client.get(f"/api/exams/{exam['id']}"))
    for item in detail["items"]:
        answer = SINGLE_ANSWERS[item["stem"]]
        client.post(
            f"/api/exams/{exam['id']}/answers", json={"seq": item["seq"], "answer": answer}
        )
    finished = data_of(client.post(f"/api/exams/{exam['id']}/finish"))
    assert finished["score"] == 100.0
    assert finished["correct_count"] == 4


# ===========================================================================
# 交卷后状态与边界
# ===========================================================================
def test_finish_twice_and_answer_after_finish_rejected(client: TestClient) -> None:
    """重复交卷 / 交卷后再作答 → 40001。"""
    upload_and_commit(client, [("basic.md", SINGLE_MD)])
    exam = open_exam(client, mode="exam", count=1)
    assert data_of(client.post(f"/api/exams/{exam['id']}/finish"))["status"] == "finished"
    error_of(client.post(f"/api/exams/{exam['id']}/finish"), 400, 40001)
    error_of(
        client.post(f"/api/exams/{exam['id']}/answers", json={"seq": 1, "answer": "A"}),
        400,
        40001,
    )


def test_invalid_exam_access(client: TestClient) -> None:
    """不存在的试卷 / 不存在的 seq → 40401。"""
    upload_and_commit(client, [("basic.md", SINGLE_MD)])
    error_of(client.get("/api/exams/9999"), 404, 40401)
    error_of(client.post("/api/exams/9999/finish"), 404, 40401)
    error_of(
        client.post("/api/exams/9999/answers", json={"seq": 1, "answer": "A"}),
        404,
        40401,
    )
    exam = open_exam(client, mode="practice", count=1)
    error_of(
        client.post(f"/api/exams/{exam['id']}/answers", json={"seq": 99, "answer": "A"}),
        404,
        40401,
    )


def test_list_and_delete_exams(client: TestClient) -> None:
    """历史列表（mode 筛选 / 分页）与删除（明细级联）。"""
    upload_and_commit(client, [("basic.md", SINGLE_MD)])
    p = open_exam(client, mode="practice", count=1)
    e = open_exam(client, mode="exam", count=1)

    all_list = data_of(client.get("/api/exams"))
    assert all_list["total"] == 2
    only_practice = data_of(client.get("/api/exams?mode=practice"))
    assert only_practice["total"] == 1
    assert only_practice["items"][0]["id"] == p["id"]

    assert data_of(client.delete(f"/api/exams/{e['id']}"))["deleted"] is True
    error_of(client.get(f"/api/exams/{e['id']}"), 404, 40401)
    assert data_of(client.get("/api/exams"))["total"] == 1


def test_delete_question_keeps_exam_snapshot(client: TestClient) -> None:
    """删原题不影响已开试卷：快照可继续作答、可判分。"""
    upload_and_commit(client, [("basic.md", SINGLE_MD)])
    exam = open_exam(client, mode="practice", count=4)
    detail = data_of(client.get(f"/api/exams/{exam['id']}"))
    target = item_by_stem(detail["items"], "脉搏短绌常见于哪种心律失常?")
    original_id = target["question_id"]
    assert original_id is not None

    # 删除原题
    resp = client.delete(f"/api/questions/{original_id}")
    assert resp.json()["code"] == 0, resp.text

    # 试卷照常作答与交卷
    result = data_of(
        client.post(
            f"/api/exams/{exam['id']}/answers", json={"seq": target["seq"], "answer": "A"}
        )
    )
    assert result["is_correct"] is True
    finished = data_of(client.post(f"/api/exams/{exam['id']}/finish"))
    assert finished["correct_count"] == 1
    detail = data_of(client.get(f"/api/exams/{exam['id']}"))
    kept = item_by_stem(detail["items"], "脉搏短绌常见于哪种心律失常?")
    assert kept["stem"] == target["stem"]
