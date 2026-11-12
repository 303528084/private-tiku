"""第 2 轮回归测试（T05-R2）：D1/D2/D4 修复的独立复核 + 边界探针。

v1.3 变更：全部样本由 ``*.txt`` 改写为等价的 ``*.md``（题量、答案、异常数不变），
纯文本切分行为（括号题号 / 第N题 / 同块多答案行）随 DOCX/TXT/PDF 管线下线一并
删除，其等价覆盖由 ``test_markdown.py`` 承担。

约定：
* 所有期望值由我自己构造的样本推导（不依赖任何外部「准确率」脚本的结论）；
* 每个用例独立 ``tmp_path`` 数据库，不污染仓库 ``data/``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterator

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.main import app

# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------
@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """独立临时库 + TestClient。"""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "DB_PATH", data_dir / "tiku.db")
    monkeypatch.setattr(config, "UPLOAD_DIR", data_dir / "uploads")
    db.reset_init_flag()
    db.init_db()
    with TestClient(app) as test_client:
        yield test_client
    db.reset_init_flag()


def _upload(client: TestClient, name: str, text: str) -> Dict[str, Any]:
    response = client.post(
        "/api/imports", files=[("files[]", (name, text.encode("utf-8"), "text/markdown"))]
    )
    body = response.json()
    assert body["code"] == 0, body
    return body["data"]


def _commit(client: TestClient, import_id: int) -> int:
    return int(client.post(f"/api/imports/{import_id}/commit").json()["data"]["committed"])


# 主样本：3 题（第 1 题缺答案 → 1 条异常；解析里埋了 % 与 _ 供通配符探针用）
MAIN_MD = """# 主回归样本

- 导出时间：2025-01-01 10:00:00
- 题目总数：3

## 1. [单选题]

脉搏短绌常见于哪种心律失常?

- **A.** 房颤
- **B.** 室颤
- **C.** 房扑
- **D.** 期前收缩

**答案**：（无答案）

**解析**：发生率约100%，标记 a_b 用于测试通配符。

---

## 2. [单选题]

无菌盘铺好后有效期为?

- **A.** 4小时
- **B.** 8小时
- **C.** 12小时
- **D.** 24小时

**答案**：A

---

## 3. [单选题]

静脉输液发生空气栓塞时应立即采取的体位是?

- **A.** 左侧卧位并头低足高
- **B.** 右侧卧位
- **C.** 半坐卧位
- **D.** 平卧位

**答案**：A
"""


# ===========================================================================
# D1：异常数 / 题数实时化（四处恒等 + 删题回退）
# ===========================================================================
def _snapshot(client: TestClient, import_id: int) -> Dict[str, Any]:
    """一次性取四个视图里的计数，便于做恒等断言。"""
    detail = client.get(f"/api/imports/{import_id}").json()["data"]
    listing = client.get("/api/imports").json()["data"]["items"][0]
    stats = client.get("/api/stats").json()["data"]
    anomalies = client.get("/api/anomalies").json()["data"]
    return {
        "file": detail["files"][0],
        "batch_detail": detail["import"],
        "batch_list": listing,
        "stats_open": stats["anomaly_open"],
        "anomalies_total": anomalies["total"],
        "anomalies_unhandled": anomalies["summary"]["unhandled"],
    }


def test_import_anomaly_counts_four_way_equality(client: TestClient) -> None:
    """D1：上传 → 修复唯一异常，四个视图的未处理异常数必须恒等。"""
    payload = _upload(client, "main.md", MAIN_MD)
    import_id = payload["import_id"]
    _commit(client, import_id)

    before = _snapshot(client, import_id)
    # 主样本里只有第 1 题缺答案
    assert before["file"]["anomaly_count"] == 1
    assert before["file"]["anomaly_total"] == 1
    for view in ("anomaly_count",):
        assert before["batch_detail"][view] == 1
        assert before["batch_list"][view] == 1
    assert before["stats_open"] == 1
    assert before["anomalies_total"] == 1 == before["anomalies_unhandled"]

    question_id = int(
        client.get("/api/anomalies").json()["data"]["items"][0]["question"]["id"]
    )
    fixed = client.put(
        f"/api/questions/{question_id}",
        json={
            "stem": "脉搏短绌常见于哪种心律失常?",
            "options": {"A": "房颤", "B": "室颤", "C": "房扑", "D": "期前收缩"},
            "answer": "A",
            "qtype": "single",
        },
    ).json()["data"]
    assert fixed["anomalies"] == []

    after = _snapshot(client, import_id)
    # 四处必须同时归零（修复前：detail/list 仍为 1）
    assert after["file"]["anomaly_count"] == 0
    assert after["batch_detail"]["anomaly_count"] == 0
    assert after["batch_list"]["anomaly_count"] == 0
    assert after["stats_open"] == 0
    assert after["anomalies_total"] == 0
    assert after["file"]["anomaly_total"] == 0
    assert after["batch_list"]["anomaly_total"] == 0


def test_handled_anomaly_counts_as_total_not_open(client: TestClient) -> None:
    """D1：标记已处理后 ``anomaly_count``（未处理）与 ``anomaly_total``（全部）语义必须分家。"""
    payload = _upload(client, "main.md", MAIN_MD)
    _commit(client, payload["import_id"])

    anomaly_id = int(
        client.get("/api/anomalies", params={"handled": 0}).json()["data"]["items"][0][
            "anomaly"
        ]["id"]
    )
    assert (
        client.patch(f"/api/anomalies/{anomaly_id}", json={"handled": True}).json()["code"] == 0
    )

    snap = _snapshot(client, payload["import_id"])
    assert snap["file"]["anomaly_count"] == 0  # 未处理
    assert snap["file"]["anomaly_total"] == 1  # 含已处理
    assert snap["batch_detail"]["anomaly_count"] == 0
    assert snap["batch_detail"]["anomaly_total"] == 1
    assert snap["batch_list"]["anomaly_count"] == 0
    assert snap["batch_list"]["anomaly_total"] == 1
    assert snap["stats_open"] == 0
    assert snap["anomalies_total"] == 1


def test_question_counts_rollback_after_deleting_question(client: TestClient) -> None:
    """D1：删题后批次与文件明细的 total_questions / committed_questions 必须回退。"""
    payload = _upload(client, "main.md", MAIN_MD)
    import_id = payload["import_id"]
    assert _commit(client, import_id) == 3

    snap = _snapshot(client, import_id)
    assert snap["batch_detail"]["total_questions"] == 3
    assert snap["batch_detail"]["committed_questions"] == 3
    assert snap["file"]["total_questions"] == 3

    question_id = int(
        client.get("/api/search", params={"q": "无菌盘"}).json()["data"]["items"][0]["id"]
    )
    assert client.delete(f"/api/questions/{question_id}").json()["data"]["deleted"] == 1

    after = _snapshot(client, import_id)
    assert after["batch_detail"]["total_questions"] == 2  # 3 → 2
    assert after["batch_detail"]["committed_questions"] == 2
    assert after["batch_list"]["total_questions"] == 2
    assert after["batch_list"]["committed_questions"] == 2
    assert after["file"]["total_questions"] == 2
    assert after["file"]["committed_questions"] == 2
    # 删掉的是第 2 题，第 1 题的 missing_answer 仍在 → 未处理异常数保持 1，四处仍一致
    assert after["stats_open"] == 1 == after["anomalies_total"]
    assert after["batch_detail"]["anomaly_count"] == 1 == after["file"]["anomaly_count"]


DIRTY_MD = """# 脏样本

## 1. [单选题]

缺答案的题?

- **A.** 甲
- **B.** 乙
- **C.** 丙
- **D.** 丁

**答案**：（无答案）
"""

CLEAN_MD = """# 干净样本

## 1. [单选题]

正常题?

- **A.** 甲
- **B.** 乙
- **C.** 丙
- **D.** 丁

**答案**：A
"""


def test_multi_file_batch_counts_are_summed_live(client: TestClient) -> None:
    """D1 补充：多文件批次的量必须等于各文件实时值之和（不是解析期快照）。"""
    response = client.post(
        "/api/imports",
        files=[
            ("files[]", ("a.md", DIRTY_MD.encode("utf-8"), "text/markdown")),
            ("files[]", ("b.md", CLEAN_MD.encode("utf-8"), "text/markdown")),
        ],
    )
    payload = response.json()["data"]
    import_id = payload["import_id"]
    _commit(client, import_id)

    detail = client.get(f"/api/imports/{import_id}").json()["data"]
    batch = detail["import"]
    files = {item["filename"]: item for item in detail["files"]}
    assert batch["anomaly_count"] == files["a.md"]["anomaly_count"] + files["b.md"]["anomaly_count"]
    assert batch["anomaly_count"] == 1
    assert files["a.md"]["anomaly_count"] == 1
    assert files["b.md"]["anomaly_count"] == 0
    assert batch["total_questions"] == sum(item["total_questions"] for item in detail["files"]) == 2
    assert batch["committed_questions"] == 2
    assert client.get("/api/stats").json()["data"]["anomaly_open"] == 1
    assert client.get("/api/anomalies").json()["data"]["total"] == 1


# ===========================================================================
# D2：结构字符不命中 / 分隔符查询返回空集 / 正常词无回归
# ===========================================================================
@pytest.fixture()
def searchable(client: TestClient) -> TestClient:
    """上传并入库主样本（3 题，含 % 与 _ 文本）。"""
    _commit(client, _upload(client, "main.md", MAIN_MD)["import_id"])
    return client


@pytest.mark.parametrize("query", ['"', "{", "}", ":", "[", "]", "\\", ","])
def test_structure_characters_do_not_match_everything(searchable: TestClient, query: str) -> None:
    """D2：JSON 结构字符不得命中全库（``\\`` 是普通字符，只应命中含反斜杠的题=0）。"""
    body = searchable.get("/api/search", params={"q": query}).json()
    assert body["code"] == 0
    assert body["data"]["total"] == 0, f"q={query!r} 命中 {body['data']['total']} 条"


@pytest.mark.parametrize("query", [",", "，", ";", "；", ", ,"])
def test_separator_only_query_returns_empty(searchable: TestClient, query: str) -> None:
    """D2 语义收敛：只有分隔符的查询返回空集，不再退化成「列出全库」。

    注意：纯空白（``q='  '``）语义上等同空查询，仍应浏览全部，故不在此参数集中。
    """
    body = searchable.get("/api/search", params={"q": query}).json()
    assert body["code"] == 0
    assert body["data"]["total"] == 0
    assert body["data"]["items"] == []


def test_empty_query_still_browses_all(searchable: TestClient) -> None:
    """空查询仍应「浏览全部已入库题目」（与分隔符查询区分开）。"""
    body = searchable.get("/api/search", params={"q": ""}).json()
    assert body["data"]["total"] == 3


def test_normal_queries_no_regression(searchable: TestClient) -> None:
    """D2 回归：正常词、多词组合、选项值命中都不能退化。"""
    cases = {
        "房颤": 1,           # 选项 A 的值（走 json_each）
        "心律失常": 1,       # 题干
        "脉搏短绌": 1,       # 题干
        "左侧卧位": 1,       # 选项 A 的值
        "脉搏短绌 心律失常": 1,  # 多词 AND
        "房颤 室颤": 1,
        "房颤 无菌盘": 0,     # 跨题 AND → 0
        "不存在的词": 0,
        "100%": 1,          # 解析里的百分号，作为普通字符匹配
        "a_b": 1,           # 解析里的下划线，作为普通字符匹配
    }
    for query, expected in cases.items():
        total = searchable.get("/api/search", params={"q": query}).json()["data"]["total"]
        assert total == expected, f"q={query!r} 期望 {expected} 实得 {total}"

    # 高亮仍然工作
    hit = searchable.get("/api/search", params={"q": "房颤"}).json()["data"]["items"][0]
    assert "<mark>房颤</mark>" in hit["highlight"]["options"]["A"]


def test_like_wildcards_are_escaped(searchable: TestClient) -> None:
    """边界：``%`` 与 ``_`` 必须作为普通字符（ESCAPE），不得变成通配符返回全库。"""
    percent = searchable.get("/api/search", params={"q": "%"}).json()["data"]
    underscore = searchable.get("/api/search", params={"q": "_"}).json()["data"]
    assert percent["total"] == 1 and percent["items"][0]["question_no"] == "1"
    assert underscore["total"] == 1 and underscore["items"][0]["question_no"] == "1"
    # 组合通配符也不能命中全库
    assert searchable.get("/api/search", params={"q": "%%%"}).json()["data"]["total"] == 0
    assert searchable.get("/api/search", params={"q": "___"}).json()["data"]["total"] == 0


# ===========================================================================
# 边界探针：脏 options_json / 超长查询
# ===========================================================================
def test_dirty_options_json_never_crashes(client: TestClient) -> None:
    """边界：``options_json`` 为坏 JSON / ``{}`` / 数组 / 空串时，检索与详情都不能 500。"""
    payload = _upload(client, "main.md", MAIN_MD)
    file_id = payload["files"][0]["file_id"]
    _commit(client, payload["import_id"])

    dirty = [
        ("91", "坏 JSON 的题目", "{not-json"),
        ("92", "空对象的题目", "{}"),
        ("93", "数组型选项的题目", '["甲", "乙"]'),
        ("94", "空串选项的题目", ""),
    ]
    with db.get_conn() as conn:
        for no, stem, options_json in dirty:
            conn.execute(
                "INSERT INTO questions (import_file_id, question_no, qtype, stem,"
                " options_json, answer, analysis, status)"
                " VALUES (?, ?, 'single', ?, ?, 'A', '', 'published')",
                (file_id, no, stem, options_json),
            )

    for query in ["坏 JSON", "空对象", "数组型", "空串", "房颤", ""]:
        response = client.get("/api/search", params={"q": query})
        assert response.status_code == 200
        assert response.json()["code"] == 0

    # 详情/导出/异常列表/批次详情同样不能崩
    for question in client.get("/api/search", params={"q": "的题目"}).json()["data"]["items"]:
        detail = client.get(f"/api/questions/{question['id']}").json()
        assert detail["code"] == 0
        assert detail["data"]["question"]["options"] == {}  # 脏 JSON 安全降级为空
    assert client.get("/api/export", params={"format": "json"}).status_code == 200
    assert client.get("/api/export", params={"format": "md"}).status_code == 200
    assert client.get("/api/anomalies").json()["code"] == 0
    assert client.get(f"/api/imports/{payload['import_id']}").json()["code"] == 0


def test_very_long_query_does_not_crash(searchable: TestClient) -> None:
    """边界：500 字与超长重复词查询不崩、不超时，且语义自洽。"""
    cases = {
        "x" * 500: 0,          # 无意义长词
        "心律失常" * 80: 0,     # 单一超长词（无分隔），不等长匹配 → 0
        "房颤 " * 100: 1,       # 空格分隔且去重后等价于单个「房颤」→ 1
    }
    for query, expected in cases.items():
        response = searchable.get("/api/search", params={"q": query})
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 0
        assert body["data"]["total"] == expected, f"len={len(query)}"


# ===========================================================================
# FTS / LIKE 双路径一致性
# ===========================================================================
def _ids_under(client: TestClient, query: str, *, fts: bool, monkeypatch) -> set:
    """在指定路径下取命中 id 集合（通过切换 db.FTS_TRIGRAM 强制 LIKE）。"""
    monkeypatch.setattr(db, "FTS_TRIGRAM", fts)
    try:
        body = client.get("/api/search", params={"q": query, "size": 100}).json()
        assert body["code"] == 0, body
        return {item["id"] for item in body["data"]["items"]}
    finally:
        monkeypatch.setattr(db, "FTS_TRIGRAM", True)


def test_fts_and_like_paths_agree_on_text_terms(
    searchable: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """纯文本检索词：FTS5 MATCH 与 LIKE 降级的命中集合必须完全相同。"""
    for query in ["心律失常", "脉搏短绌", "静脉输液", "左侧卧位", "无菌盘", "4小时",
                  "脉搏短绌 心律失常", "房颤 室颤", "不存在的词"]:
        via_fts = _ids_under(searchable, query, fts=True, monkeypatch=monkeypatch)
        via_like = _ids_under(searchable, query, fts=False, monkeypatch=monkeypatch)
        assert via_fts == via_like, f"q={query!r} FTS={via_fts} LIKE={via_like}"


def test_fts_and_like_agree_on_quote_terms(
    searchable: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D4（已闭环）：含引号检索词，两条路径的命中集合必须**相等且非空**。

    v1.1.1 的修法是 ``parse_terms`` 剥掉词首尾引号，因此 ``"房颤"`` 应等价于
    ``房颤``——两条路径都要命中含「房颤」的那 1 题（而不是两边都为 0 的「假一致」）。
    """
    cases = {'"房颤"': "1", '房颤"': "1", '"房颤" 心律失常': "1"}
    for query, expected_no in cases.items():
        via_fts = _ids_under(searchable, query, fts=True, monkeypatch=monkeypatch)
        via_like = _ids_under(searchable, query, fts=False, monkeypatch=monkeypatch)
        assert via_fts == via_like, f"q={query!r} FTS={via_fts} LIKE={via_like}"
        assert via_fts, f"q={query!r} 两条路径都为空，说明引号未被剥离"
        hits = searchable.get("/api/search", params={"q": query}).json()["data"]["items"]
        assert {item["question_no"] for item in hits} == {expected_no}

    # 引号本身不应再作为「JSON 结构字符」命中其它题：``"`` 单独查仍为 0
    assert searchable.get("/api/search", params={"q": '"'}).json()["data"]["total"] == 0
