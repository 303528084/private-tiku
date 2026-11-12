"""API 集成测试（T05）：TestClient 全链路 / 安全 / 错误码。

设计约束：
* 每个用例使用独立的临时数据库（``tmp_path``），通过 monkeypatch 改写
  ``config.DATA_DIR / DB_PATH / UPLOAD_DIR`` 并重置 ``db._initialized``，
  用例之间零污染，且绝不写入仓库内 ``data/``。
* 只断言对外契约（响应外壳、字段、状态码、业务码），不依赖内部实现细节；
  唯一白盒断言是 ``search._can_use_fts``（用于证明 2 字/3 字走不同检索路径）。
* v1.3 起导入样本全部改为 Markdown（``*.md``）：题量、答案、异常数与旧 ``*.txt``
  样本一一对应，覆盖 FTS / 导出 / 异常 / 错误码的断言全部保留。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.api import search as search_module
from app.main import app

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# 允许出现在高亮片段里的 HTML 标签白名单
TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
ALLOWED_TAGS = {"<mark>", "</mark>"}


# ===========================================================================
# 夹具与工具
# ===========================================================================
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


def upload(client: TestClient, files: Sequence[Tuple[str, bytes]]) -> Any:
    """按 ``files[]`` 字段上传多个文件。"""
    payload = [
        ("files[]", (name, content, "application/octet-stream"))
        for name, content in files
    ]
    return client.post("/api/imports", files=payload)


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


# ---------------------------------------------------------------------------
# 样例 Markdown（等价于 v1.2 的 BASIC_TXT / SECOND_TXT / XSS_TXT）
# ---------------------------------------------------------------------------
BASIC_MD = """# 护理学基础试题（第一批）

- 导出时间：2025-01-01 10:00:00
- 题目总数：4

## 1. [单选题]

脉搏短绌常见于哪种心律失常?

- **A.** 房颤
- **B.** 室颤
- **C.** 房扑
- **D.** 期前收缩

**答案**：A

**解析**：脉搏短绌多见于心房颤动，应与心律失常其他类型鉴别。

---

## 2. [多选题]

心肺复苏有效指征包括?

- **A.** 可触及大动脉搏动
- **B.** 面色由紫绀转红润
- **C.** 瞳孔由大变小
- **D.** 自主呼吸恢复

**答案**：ABD

---

## 3. [判断题]

正常成人腋温范围为36.0~37.0℃。

**答案**：√

---

## 4. [未知题型]

预防压疮的护理措施不包括?

- **A.** 定时翻身
- **B.** 保持皮肤清洁干燥
- **C.** 使用减压床垫
- **D.** 局部用力按摩受压皮肤

**答案**：（无答案）
"""

SECOND_MD = """# 无菌技术试题

## 1. [多选题]

无菌技术操作原则包括?

- **A.** 清洁区与污染区分开
- **B.** 无菌物品疑有污染不可使用
- **C.** 一套无菌物品仅供一位患者使用
- **D.** 操作前洗手戴口罩

**答案**：ABCD

## 2. [多选题]

铺备用床的目的?

- **A.** 保持病室整洁
- **B.** 供新入院患者使用
- **C.** 便于观察病情
- **D.** 预防压疮

**答案**：AB
"""

XSS_MD = """# XSS 样本

## 1. [单选题]

静脉输液护理常规<script>alert(1)</script>?

- **A.** <img src=x onerror=1>
- **B.** 严格执行查对制度
- **C.** 注意药物配伍禁忌
- **D.** 观察输液反应

**答案**：A

**解析**：含 <b>加粗</b> 与 script 标记的攻击载荷。
"""

THREE_MD = """# 三题样本

## 1. [单选题]

无菌盘有效期为?

- **A.** 4小时
- **B.** 8小时
- **C.** 12小时
- **D.** 24小时

**答案**：A

## 2. [单选题]

大量不保留灌肠液面距肛门高度为?

- **A.** 20~30cm
- **B.** 40~60cm
- **C.** 70~80cm
- **D.** 90cm以上

**答案**：B

## 3. [判断题]

氧气筒内氧气可用尽后再更换。

**答案**：×
"""

JSON_MD_BANK = json.dumps(
    {
        "title": "外科护理学试题",
        "author": "教研组",
        "questions": [
            {
                "number": 1,
                "stem": "患者发生急性肺水肿时应采取的体位是?",
                "options": {
                    "A": "端坐位双腿下垂",
                    "B": "去枕平卧位",
                    "C": "头低足高位",
                    "D": "侧卧位",
                },
                "answer": "A",
            },
            {
                "number": 2,
                "stem": "洋地黄中毒最常见的心律失常是?",
                "options": {
                    "A": "室性期前收缩",
                    "B": "房颤",
                    "C": "房扑",
                    "D": "窦性心动过速",
                },
                "answer": "A",
            },
        ],
    },
    ensure_ascii=False,
)


def _basic_import(client: TestClient, name: str = "basic.md") -> Dict[str, Any]:
    """上传 BASIC_MD 并返回上传响应 data。"""
    return data_of(upload(client, [(name, BASIC_MD.encode("utf-8"))]))


def _md_import(client: TestClient, name: str, text: str) -> Dict[str, Any]:
    """上传任意 Markdown 内容并返回上传响应 data。"""
    return data_of(upload(client, [(name, text.encode("utf-8"))]))


def _publish(client: TestClient, import_id: int) -> int:
    """commit 并把入库题数返回。"""
    return int(data_of(client.post(f"/api/imports/{import_id}/commit"))["committed"])


def _create(client: TestClient, **payload: Any) -> Any:
    """POST /api/questions 的薄封装。"""
    return client.post("/api/questions", json=payload)


def _valid_create_payload(**overrides: Any) -> Dict[str, Any]:
    """一份合法的录题请求体（可局部覆盖）。"""
    payload: Dict[str, Any] = {
        "stem": "患者男，65 岁，突发胸痛 2 小时，心电图提示 ST 段抬高。",
        "options": {"A": "急性心肌梗死", "B": "心绞痛", "C": "肺栓塞", "D": "主动脉夹层"},
        "answer": "A",
        "analysis": "ST 段抬高提示透壁性心肌缺血。",
        "qtype": "single",
        "library_name": "手动录入",
        "question_no": None,
    }
    payload.update(overrides)
    return payload


# ===========================================================================
# 1. 健康检查 / 空库统计
# ===========================================================================
def test_health_and_empty_stats(client: TestClient) -> None:
    health = data_of(client.get("/api/health"))
    assert health["status"] == "ok"
    assert {"fts_trigram"} <= set(health)

    stats = data_of(client.get("/api/stats"))
    assert stats == {
        "total_published": 0,
        "total_pending": 0,
        "anomaly_open": 0,
        "total_imports": 0,
        "total_files": 0,
        "total_questions": 0,
    }
    # 空库搜索不应报错
    result = data_of(client.get("/api/search", params={"q": ""}))
    assert result["total"] == 0 and result["items"] == []
    # 空库的题库下拉为空列表（而不是 404）
    assert data_of(client.get("/api/libraries"))["items"] == []


# ===========================================================================
# 2. 上传：多格式 / 分页 / 详情
# ===========================================================================
def test_upload_multi_format_and_import_detail(client: TestClient, tmp_path: Path) -> None:
    """v1.3：JSON + Markdown 两种格式同批上传，分片与计数互不干扰。"""
    payload = data_of(
        upload(
            client,
            [
                ("basic.md", BASIC_MD.encode("utf-8")),
                ("surgery.json", JSON_MD_BANK.encode("utf-8")),
            ],
        )
    )
    assert payload["import_id"] >= 1
    files = payload["files"]
    assert len(files) == 2
    assert [f["status"] for f in files] == ["parsed", "parsed"]
    assert [f["filename"] for f in files] == ["basic.md", "surgery.json"]
    assert [f["file_type"] for f in files] == ["md", "json"]
    # md 有 4 题、json 有 2 题
    assert [f["total_questions"] for f in files] == [4, 2]
    # md 第 4 题缺答案 → 1 条异常；json 全干净
    assert files[0]["anomaly_count"] == 1
    assert files[1]["anomaly_count"] == 0

    detail = data_of(client.get(f"/api/imports/{payload['import_id']}"))
    assert detail["import"]["status"] == "pending_confirm"
    assert detail["import"]["total_files"] == 2
    assert detail["import"]["ok_files"] == 2
    assert detail["import"]["failed_files"] == 0
    assert detail["import"]["total_questions"] == 6
    assert [f["file_id"] for f in detail["files"]] == [f["file_id"] for f in files]
    assert detail["files"][0]["anomaly_count"] == 1
    # 落盘文件按批次分目录，不污染仓库 data/
    assert (tmp_path / "data" / "uploads" / f"import_{payload['import_id']}").is_dir()

    listing = data_of(client.get("/api/imports", params={"page": 1, "size": 1}))
    assert listing["total"] == 1
    assert len(listing["items"]) == 1
    assert listing["items"][0]["id"] == payload["import_id"]
    assert listing["items"][0]["anomaly_count"] == 1


def test_upload_markdown_extension_aliases_to_md(client: TestClient) -> None:
    """``.markdown`` 与 ``.md`` 合并统计为 ``md``（file_type 直出 md）。"""
    payload = data_of(upload(client, [("notes.markdown", BASIC_MD.encode("utf-8"))]))
    assert payload["files"][0]["file_type"] == "md"
    assert payload["files"][0]["status"] == "parsed"
    assert payload["files"][0]["total_questions"] == 4


def test_import_list_pagination(client: TestClient) -> None:
    ids = [_basic_import(client, f"batch{i}.md")["import_id"] for i in range(3)]
    first = data_of(client.get("/api/imports", params={"page": 1, "size": 2}))
    assert first["total"] == 3
    assert [item["id"] for item in first["items"]] == [ids[2], ids[1]]  # id 倒序
    second = data_of(client.get("/api/imports", params={"page": 2, "size": 2}))
    assert [item["id"] for item in second["items"]] == [ids[0]]
    beyond = data_of(client.get("/api/imports", params={"page": 9, "size": 2}))
    assert beyond["items"] == [] and beyond["total"] == 3


# ===========================================================================
# 3. 上传异常路径：类型不支持 / 部分失败 / 数量与参数
# ===========================================================================
def test_upload_unsupported_type_returns_42201(client: TestClient) -> None:
    response = upload(client, [("notes.xlsx", b"PK\x03\x04fake")])
    body = error_of(response, 422, 42201)
    assert "不受支持" in body["message"]
    assert body["data"]["files"][0]["status"] == "failed"


@pytest.mark.parametrize(
    ("filename", "suffix"),
    [("old.docx", ".docx"), ("old.txt", ".txt"), ("old.pdf", ".pdf")],
)
def test_upload_legacy_formats_rejected_with_prd_wording(
    client: TestClient, filename: str, suffix: str
) -> None:
    """v1.3：docx/txt/pdf 一律 422/42201，且 ``data.files[].error`` 逐字等于 PRD 文案。"""
    response = upload(client, [(filename, "占位内容".encode("utf-8"))])
    body = error_of(response, 422, 42201)
    assert set(body["data"]) == {"import_id", "files"}
    expected = (
        f"不支持的文件类型「{suffix}」：v1.3 起仅支持 .json / .md 导入。"
        "已有的 docx/txt/pdf 题库不受影响，可直接搜索与导出。"
    )
    assert body["data"]["files"][0]["error"] == expected
    assert body["data"]["files"][0]["status"] == "failed"


def test_upload_partial_failure_lists_failed_file(client: TestClient) -> None:
    """PRD P0-1：失败文件要单独列出并说明原因。"""
    payload = data_of(
        upload(
            client,
            [
                ("good.md", BASIC_MD.encode("utf-8")),
                ("bad.xlsx", b"not-a-real-xlsx"),
            ],
        )
    )
    statuses = {f["filename"]: (f["status"], f["error"]) for f in payload["files"]}
    assert statuses["good.md"][0] == "parsed"
    assert statuses["bad.xlsx"][0] == "failed"
    assert "不支持的文件类型" in statuses["bad.xlsx"][1]

    detail = data_of(client.get(f"/api/imports/{payload['import_id']}"))
    assert detail["import"]["ok_files"] == 1
    assert detail["import"]["failed_files"] == 1
    assert detail["import"]["status"] == "pending_confirm"


def test_upload_rejects_empty_request(client: TestClient) -> None:
    body = error_of(client.post("/api/imports"), 400, 40001)
    assert "未接收到上传文件" in body["message"]


def test_upload_rejects_too_many_files(client: TestClient) -> None:
    files = [(f"f{i}.md", THREE_MD.encode("utf-8")) for i in range(21)]
    body = error_of(upload(client, files), 400, 40001)
    assert "20" in body["message"]


def test_upload_large_batch_of_20_files(client: TestClient) -> None:
    """PRD P0-1：单次 ≥20 文件不崩。"""
    files = [(f"batch{i}.md", THREE_MD.encode("utf-8")) for i in range(20)]
    payload = data_of(upload(client, files))
    assert len(payload["files"]) == 20
    assert all(f["status"] == "parsed" for f in payload["files"])
    assert sum(f["total_questions"] for f in payload["files"]) == 60

    stats = data_of(client.get("/api/stats"))
    assert stats["total_pending"] == 60
    assert stats["total_files"] == 20


def test_upload_empty_file_reports_50001(client: TestClient) -> None:
    body = error_of(upload(client, [("empty.md", b"")]), 500, 50001)
    assert body["data"]["files"][0]["status"] == "failed"


def test_upload_markdown_without_heading_reports_50001(client: TestClient) -> None:
    """坏 md（全文无 ``## `` 标题）→ 500/50001，且报错带行号与期望写法。"""
    broken = "这是一份丢掉题目标题的手写稿。\n\n无菌技术操作原则包括哪些？\n"
    body = error_of(upload(client, [("broken.md", broken.encode("utf-8"))]), 500, 50001)
    assert body["data"]["files"][0]["status"] == "failed"
    assert "第 1 行" in body["data"]["files"][0]["error"]
    assert "未找到题目标题" in body["data"]["files"][0]["error"]


# ===========================================================================
# 4. 入库前不可检索 / commit 幂等
# ===========================================================================
def test_pending_questions_are_not_searchable_until_commit(client: TestClient) -> None:
    payload = _basic_import(client)
    import_id = payload["import_id"]

    before = data_of(client.get("/api/search", params={"q": "脉搏短绌"}))
    assert before["total"] == 0
    assert data_of(client.get("/api/stats"))["total_pending"] == 4

    assert _publish(client, import_id) == 4
    stats = data_of(client.get("/api/stats"))
    assert stats["total_published"] == 4
    assert stats["total_pending"] == 0

    after = data_of(client.get("/api/search", params={"q": "脉搏短绌"}))
    assert after["total"] == 1

    # 重复 commit 幂等（不会重复计数）
    assert _publish(client, import_id) == 0
    detail = data_of(client.get(f"/api/imports/{import_id}"))
    assert detail["import"]["status"] == "committed"
    assert detail["import"]["committed_questions"] == 4


# ===========================================================================
# 5. 搜索：短词 LIKE 降级 / 长词 FTS5 MATCH / 多关键词 / 高亮 / 分页
# ===========================================================================
def test_search_routing_between_like_and_fts() -> None:
    """白盒校验：2 字词必须走 LIKE，3 字以上走 FTS5 MATCH。"""
    assert search_module._can_use_fts(["房颤"]) is False
    assert search_module._can_use_fts(["脉搏短绌"]) is True
    assert search_module._can_use_fts(["脉搏短绌", "房"]) is False  # 混合长度退化为 LIKE
    assert search_module.parse_terms("  脉搏短绌   房颤 ") == ["脉搏短绌", "房颤"]
    assert search_module.parse_terms("房颤，室颤") == ["房颤", "室颤"]


def test_search_short_term_uses_like_and_highlights(client: TestClient) -> None:
    _publish(client, _basic_import(client)["import_id"])
    result = data_of(client.get("/api/search", params={"q": "房颤"}))
    assert result["total"] == 1
    assert "took_ms" in result and isinstance(result["took_ms"], int)
    item = result["items"][0]
    # 命中出现在选项 A 与解析中，两处都要高亮
    assert "<mark>房颤</mark>" in item["highlight"]["options"]["A"]
    assert "<mark>房颤</mark>" in item["highlight"]["analysis"]
    # 原始字段保持干净（未被注入标记）
    assert "<mark>" not in item["stem"]


def test_search_long_term_uses_fts_and_multi_keyword(client: TestClient) -> None:
    _publish(client, _basic_import(client)["import_id"])

    single = data_of(client.get("/api/search", params={"q": "脉搏短绌"}))
    assert single["total"] == 1
    assert single["items"][0]["question_no"] == "1"

    # 多关键词 AND 组合（两个词都在同一题内）
    combined = data_of(client.get("/api/search", params={"q": "脉搏短绌 心律失常"}))
    assert combined["total"] == 1

    # 一个词命中、另一个词不命中 → AND 后应为 0
    mismatch = data_of(client.get("/api/search", params={"q": "脉搏短绌 压疮"}))
    assert mismatch["total"] == 0

    # 答案字段也参与检索（判断题答案 √ 除外，这里用多选答案 ABD）
    by_answer = data_of(client.get("/api/search", params={"q": "ABD"}))
    assert by_answer["total"] == 1
    assert by_answer["items"][0]["qtype"] == "multiple"


def test_search_empty_query_returns_all_published_with_pagination(client: TestClient) -> None:
    _publish(client, _basic_import(client)["import_id"])
    all_rows = data_of(client.get("/api/search", params={"q": "", "size": 100}))
    assert all_rows["total"] == 4
    assert len(all_rows["items"]) == 4

    page1 = data_of(client.get("/api/search", params={"q": "", "page": 1, "size": 2}))
    page2 = data_of(client.get("/api/search", params={"q": "", "page": 2, "size": 2}))
    assert [i["id"] for i in page1["items"]] != [i["id"] for i in page2["items"]]
    assert page1["total"] == page2["total"] == 4


def test_search_filters_type_only_anomaly_and_doc_id(client: TestClient) -> None:
    first = _basic_import(client, "basic.md")
    second = _md_import(client, "second.md", SECOND_MD)
    _publish(client, first["import_id"])
    _publish(client, second["import_id"])

    def total(**params: Any) -> int:
        return int(data_of(client.get("/api/search", params={"q": "", **params}))["total"])

    assert total() == 6
    assert total(type="single") == 1
    assert total(type="multiple") == 3
    assert total(type="judge") == 1
    assert total(type="unknown") == 1

    # 只有第 4 题（缺答案）带未处理异常
    assert total(only_anomaly="true") == 1
    anomaly_hits = data_of(client.get("/api/search", params={"q": "", "only_anomaly": "true"}))
    assert anomaly_hits["items"][0]["question_no"] == "4"
    assert [a["type"] for a in anomaly_hits["items"][0]["anomalies"]] == ["missing_answer"]

    # doc_id = import_file_id
    doc_id = first["files"][0]["file_id"]
    assert total(doc_id=doc_id) == 4
    filtered = data_of(client.get("/api/search", params={"q": "", "doc_id": doc_id}))
    assert {item["filename"] for item in filtered["items"]} == {"basic.md"}

    # 组合筛选
    assert total(type="multiple", doc_id=doc_id) == 1

    # 标记已处理后不再出现在 only_anomaly
    anomaly_id = data_of(client.get("/api/anomalies", params={"handled": 0}))["items"][0]["anomaly"]["id"]
    assert client.patch(f"/api/anomalies/{anomaly_id}", json={"handled": True}).json()["code"] == 0
    assert total(only_anomaly="true") == 0


def test_search_rejects_illegal_type(client: TestClient) -> None:
    body = error_of(client.get("/api/search", params={"q": "x", "type": "essay"}), 400, 40001)
    assert "题型" in body["message"]


# ===========================================================================
# 6. XSS：只允许 <mark>，其余全部转义
# ===========================================================================
def test_search_escapes_html_in_highlight(client: TestClient) -> None:
    payload = _md_import(client, "xss.md", XSS_MD)
    _publish(client, payload["import_id"])

    result = data_of(client.get("/api/search", params={"q": "script"}))
    assert result["total"] == 1
    item = result["items"][0]
    segments = [
        item["highlight"]["stem"],
        item["highlight"]["answer"],
        item["highlight"]["analysis"],
        *item["highlight"]["options"].values(),
    ]

    def unmark(text: str) -> str:
        """去掉 <mark> 包裹，还原被高亮切开的原文片段。"""
        return text.replace("<mark>", "").replace("</mark>", "")

    # 1) 攻击标签必须被转义（允许 <mark> 插在标签中间，因此先剥离标记再比对）
    assert "&lt;script&gt;" in unmark(item["highlight"]["stem"])
    assert "&lt;/script&gt;" in unmark(item["highlight"]["stem"])
    assert "&lt;img" in unmark(item["highlight"]["options"]["A"])
    assert "&lt;b&gt;" in unmark(item["highlight"]["analysis"])
    # 2) 高亮片段里不得出现任何真实标签
    for segment in segments:
        tags = set(TAG_RE.findall(segment))
        assert tags <= ALLOWED_TAGS, f"发现非白名单标签：{tags} in {segment!r}"
    assert "script" in item["highlight"]["stem"].lower()
    # 3) 命中词仍被正确高亮（安全与功能不互斥）
    assert "<mark>" in item["highlight"]["stem"]


def test_question_edit_escapes_on_search_too(client: TestClient) -> None:
    """编辑接口写入的攻击载荷，同样要在搜索结果里被转义。"""
    payload = _basic_import(client)
    _publish(client, payload["import_id"])
    question_id = int(
        data_of(client.get("/api/search", params={"q": "脉搏短绌"}))["items"][0]["id"]
    )
    put = data_of(
        client.put(
            f"/api/questions/{question_id}",
            json={
                "stem": "注入<img src=x onerror=alert(1)>测试",
                "options": {"A": "<svg/onload=1>", "B": "乙", "C": "丙", "D": "丁"},
                "answer": "A",
                "analysis": "解析 <iframe src=1>",
                "qtype": "single",
            },
        )
    )
    assert put["question"]["anomalies"] == []

    hit = data_of(client.get("/api/search", params={"q": "onload"}))["items"][0]
    assert "<svg" not in hit["highlight"]["options"]["A"]
    assert "&lt;svg" in hit["highlight"]["options"]["A"]
    assert set(TAG_RE.findall(hit["highlight"]["options"]["A"])) <= ALLOWED_TAGS


# ===========================================================================
# 7. 题目详情 / 编辑（异常全量重建）/ 删除（FTS 同步）
# ===========================================================================
def test_question_detail_and_put_rebuilds_anomalies(client: TestClient) -> None:
    payload = _basic_import(client)
    _publish(client, payload["import_id"])

    missing = data_of(client.get("/api/anomalies", params={"type": "missing_answer"}))
    assert missing["total"] == 1
    question_id = int(missing["items"][0]["question"]["id"])

    detail = data_of(client.get(f"/api/questions/{question_id}"))
    assert detail["question"]["id"] == question_id
    assert detail["question"]["filename"] == "basic.md"
    assert [a["type"] for a in detail["anomalies"]] == ["missing_answer"]

    # 补齐答案与四个选项 → 异常应被全量清空（qtype 缺省时按答案自动判定）
    fixed = data_of(
        client.put(
            f"/api/questions/{question_id}",
            json={
                "stem": "预防压疮的护理措施不包括?",
                "options": {
                    "A": "定时翻身",
                    "B": "保持皮肤清洁干燥",
                    "C": "使用减压床垫",
                    "D": "局部用力按摩受压皮肤",
                },
                "answer": "d",
                "analysis": "局部用力按摩会加重组织损伤。",
            },
        )
    )
    assert fixed["question"]["answer"] == "D"  # 小写答案被规范化
    assert fixed["question"]["qtype"] == "single"
    assert fixed["anomalies"] == []

    # 再改成「缺答案 + 只有 1 个选项」→ 异常重建为两条，且没有残留旧记录
    broken = data_of(
        client.put(
            f"/api/questions/{question_id}",
            json={
                "stem": "只剩一个选项?",
                "options": {"A": "只有一个"},
                "answer": "",
                "qtype": "single",
            },
        )
    )
    types = sorted(a["type"] for a in broken["anomalies"])
    assert types == ["missing_answer", "too_few_options"]
    assert broken["question"]["qtype"] == "single"

    # 数据库中该题异常条数 = 返回条数（全量重建，无脏数据）
    again = data_of(client.get(f"/api/questions/{question_id}"))
    assert len(again["anomalies"]) == len(broken["anomalies"])

    # 编辑后 FTS 同步：新关键词可检索、旧关键词不再命中
    assert data_of(client.get("/api/search", params={"q": "只剩一个选项"}))["total"] == 1
    assert data_of(client.get("/api/search", params={"q": "定时翻身"}))["total"] == 0


def test_put_rejects_empty_stem(client: TestClient) -> None:
    payload = _basic_import(client)
    _publish(client, payload["import_id"])
    question_id = int(data_of(client.get("/api/search", params={"q": "脉搏短绌"}))["items"][0]["id"])
    body = error_of(
        client.put(f"/api/questions/{question_id}", json={"stem": "", "answer": "A"}),
        400,
        40001,
    )
    assert "题干" in body["message"]


def test_delete_question_syncs_fts_and_anomalies(client: TestClient) -> None:
    payload = _basic_import(client)
    _publish(client, payload["import_id"])
    question_id = int(
        data_of(client.get("/api/anomalies", params={"type": "missing_answer"}))["items"][0]["question"]["id"]
    )
    assert data_of(client.get("/api/search", params={"q": "减压床垫"}))["total"] == 1

    assert data_of(client.delete(f"/api/questions/{question_id}"))["deleted"] == 1
    assert data_of(client.get("/api/search", params={"q": "减压床垫"}))["total"] == 0
    assert data_of(client.get("/api/anomalies"))["total"] == 0
    assert data_of(client.get("/api/stats"))["anomaly_open"] == 0
    assert data_of(client.get("/api/stats"))["total_published"] == 3

    error_of(client.get(f"/api/questions/{question_id}"), 404, 40401)
    error_of(client.delete(f"/api/questions/{question_id}"), 404, 40401)


# ===========================================================================
# 8. 异常列表 / 标记已处理
# ===========================================================================
def test_anomaly_list_filters_and_patch(client: TestClient) -> None:
    payload = _basic_import(client)
    _publish(client, payload["import_id"])

    listing = data_of(client.get("/api/anomalies"))
    assert listing["total"] == 1
    item = listing["items"][0]
    assert item["anomaly"]["type"] == "missing_answer"
    assert item["anomaly"]["handled"] == 0
    assert item["question"]["question_no"] == "4"
    assert item["question"]["filename"] == "basic.md"
    assert listing["summary"] == {"unhandled": 1, "handled": 0}

    anomaly_id = int(item["anomaly"]["id"])
    patched = data_of(client.patch(f"/api/anomalies/{anomaly_id}", json={"handled": True}))
    assert patched["anomaly"]["handled"] == 1

    handled = data_of(client.get("/api/anomalies", params={"handled": 1}))
    assert handled["total"] == 1
    assert handled["summary"] == {"unhandled": 0, "handled": 1}
    assert data_of(client.get("/api/anomalies", params={"handled": 0}))["total"] == 0
    assert data_of(client.get("/api/anomalies", params={"type": "missing_answer"}))["total"] == 1
    assert data_of(client.get("/api/anomalies", params={"type": "parse_error"}))["total"] == 0

    # 还原
    reverted = data_of(client.patch(f"/api/anomalies/{anomaly_id}", json={"handled": False}))
    assert reverted["anomaly"]["handled"] == 0

    error_of(client.get("/api/anomalies", params={"type": "nope"}), 400, 40001)
    error_of(client.patch("/api/anomalies/999999", json={"handled": True}), 404, 40401)


# ===========================================================================
# 9. 导出 json / md
# ===========================================================================
def test_export_markdown_and_json(client: TestClient) -> None:
    payload = _basic_import(client)
    _publish(client, payload["import_id"])

    md = client.get("/api/export", params={"format": "md"})
    assert md.status_code == 200
    assert md.headers["content-type"].startswith("text/markdown")
    assert "attachment" in md.headers["content-disposition"]
    assert md.headers["x-total-count"] == "4"
    body = md.text
    assert body.startswith("# 私有护理题库导出")
    assert "## 1. [单选题]" in body
    assert "**答案**：D" not in body  # 未编辑前答案是 A
    assert "**答案**：A" in body
    assert "**异常标记**：缺答案" in body  # 第 4 题异常以中文标签导出
    assert "**来源**：basic.md" in body

    js = client.get("/api/export", params={"format": "json"})
    assert js.status_code == 200
    assert "attachment" in js.headers["content-disposition"]
    parsed = json.loads(js.text)
    assert parsed["total"] == 4
    assert len(parsed["items"]) == 4
    assert {"id", "question_no", "qtype", "stem", "options", "answer", "anomalies"} <= set(
        parsed["items"][0]
    )

    # 未入库（pending）的题不导出
    payload2 = _basic_import(client, "pending_only.md")
    assert payload2["import_id"] > 0
    assert json.loads(client.get("/api/export", params={"format": "json"}).text)["total"] == 4


def test_export_rejects_illegal_format(client: TestClient) -> None:
    error_of(client.get("/api/export", params={"format": "xml"}), 400, 40001)


def _question_fingerprints(client: TestClient) -> List[Tuple[Any, ...]]:
    """取 JSON 导出里的逐题指纹（排除 id / 时间戳等易变字段）。"""
    items = json.loads(client.get("/api/export", params={"format": "json"}).text)["items"]
    return [
        (
            item["question_no"],
            item["qtype"],
            item["stem"],
            item["options"],
            item["answer"],
            item["analysis"],
            tuple(anomaly["type"] for anomaly in item["anomalies"]),
        )
        for item in items
    ]


def test_export_import_export_via_api_is_byte_identical(client: TestClient) -> None:
    """API 级往返：导出 md → 原样重新导入 → 再导出，除时间戳外逐字节相等。

    这是「Markdown 可往返」的端到端证明（``test_markdown.py`` 覆盖解析层，
    本例覆盖「导出格式 ↔ 解析器」两侧都不漂移）。
    """
    first = _basic_import(client, "roundtrip.md")
    _publish(client, first["import_id"])
    md1 = client.get("/api/export", params={"format": "md"}).text
    assert "**来源**：roundtrip.md" in md1
    fingerprints_before = _question_fingerprints(client)

    # 清空后把导出的 md 原样作为新文件导入
    data_of(client.delete(f"/api/imports/{first['import_id']}"))
    assert data_of(client.get("/api/stats"))["total_questions"] == 0

    second = _md_import(client, "roundtrip.md", md1)
    assert second["files"][0]["status"] == "parsed"
    assert second["files"][0]["total_questions"] == 4
    _publish(client, second["import_id"])

    md2 = client.get("/api/export", params={"format": "md"}).text

    def body(text: str) -> List[str]:
        return [line for line in text.split("\n") if not line.startswith("- 导出时间：")]

    assert body(md1) == body(md2)
    assert _question_fingerprints(client) == fingerprints_before


# ===========================================================================
# 10. 删除批次级联 + 统计归零
# ===========================================================================
def test_delete_import_cascades_everything(client: TestClient) -> None:
    first = _basic_import(client, "basic.md")
    second = _md_import(client, "second.md", SECOND_MD)
    _publish(client, first["import_id"])
    _publish(client, second["import_id"])
    assert data_of(client.get("/api/stats"))["total_published"] == 6

    deleted = data_of(client.delete(f"/api/imports/{first['import_id']}"))
    assert deleted["deleted"] == 1
    assert deleted["deleted_files"] == 1
    assert deleted["deleted_questions"] == 4

    stats = data_of(client.get("/api/stats"))
    assert stats["total_published"] == 2
    assert stats["total_questions"] == 2
    assert stats["total_files"] == 1
    assert stats["total_imports"] == 1
    assert stats["anomaly_open"] == 0  # 唯一异常来自被删批次

    # FTS 已同步：被删批次的词检索不到
    assert data_of(client.get("/api/search", params={"q": "脉搏短绌"}))["total"] == 0
    assert data_of(client.get("/api/search", params={"q": "无菌技术"}))["total"] == 1
    assert data_of(client.get("/api/anomalies"))["total"] == 0

    # 再删剩余批次 → 全部归零
    data_of(client.delete(f"/api/imports/{second['import_id']}"))
    final = data_of(client.get("/api/stats"))
    assert final == {
        "total_published": 0,
        "total_pending": 0,
        "anomaly_open": 0,
        "total_imports": 0,
        "total_files": 0,
        "total_questions": 0,
    }
    assert data_of(client.get("/api/search", params={"q": ""}))["total"] == 0
    error_of(client.delete(f"/api/imports/{first['import_id']}"), 404, 40401)


# ===========================================================================
# 11. 手录单题：POST /api/questions
# ===========================================================================
def test_create_question_persists_and_returns_envelope(client: TestClient) -> None:
    """录题成功：直接 published、来源=题库名、返回 question/anomalies/library。"""
    data = data_of(_create(client, **_valid_create_payload()))

    assert set(data) == {"question", "anomalies", "library"}
    question = data["question"]
    assert question["status"] == "published"
    assert question["qtype"] == "single"
    assert question["answer"] == "A"
    assert question["question_no"] == "1"
    assert question["filename"] == "手动录入"
    assert question["options"]["A"] == "急性心肌梗死"
    assert data["anomalies"] == [] == question["anomalies"]
    assert data["library"] == {
        "id": question["import_file_id"],
        "import_id": data["library"]["import_id"],
        "name": "手动录入",
        "file_type": "manual",
        "created": True,
    }

    # 立即可检索（published 无需 commit），且出现在搜索结果里
    hits = data_of(client.get("/api/search", params={"q": "心肌梗死"}))
    assert hits["total"] == 1
    assert hits["items"][0]["id"] == question["id"]

    # 手录库以 committed 批次 + manual 文件出现在导入列表（计数走实时聚合）
    imports = data_of(client.get("/api/imports"))["items"]
    assert len(imports) == 1
    assert imports[0]["status"] == "committed"
    assert imports[0]["total_files"] == 1
    assert imports[0]["ok_files"] == 1
    assert imports[0]["total_questions"] == 1

    detail = data_of(client.get(f"/api/questions/{question['id']}"))
    assert detail["question"]["filename"] == "手动录入"
    assert detail["question"]["status"] == "published"
    assert detail["anomalies"] == []


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"stem": "   "}, "题干不能为空"),
        ({"library_name": "   "}, "题库名称不能为空"),
        ({"library_name": "库" * 121}, "题库名称最多 120 字"),
        ({"question_no": "9" * 33}, "题号最多 32 字"),
        ({"qtype": "unknown"}, "题型只能是 single / multiple / judge"),
        ({"qtype": "essay"}, "题型只能是 single / multiple / judge"),
        ({"answer": ""}, "请填写答案"),
    ],
)
def test_create_question_error_branches(
    client: TestClient, overrides: Dict[str, Any], message: str
) -> None:
    """设计 §3 的 7 条错误分支（逐字 message + HTTP 400 + code 40001）。"""
    body = error_of(_create(client, **_valid_create_payload(**overrides)), 400, 40001)
    assert body["message"] == message
    # 失败不留任何脏数据
    assert data_of(client.get("/api/stats"))["total_questions"] == 0
    assert data_of(client.get("/api/imports"))["items"] == []


def test_create_question_rejects_missing_qtype(client: TestClient) -> None:
    """题型字段缺失同样是 40001（不是 Pydantic 的通用报错文案）。"""
    payload = _valid_create_payload()
    payload.pop("qtype")
    body = error_of(_create(client, **payload), 400, 40001)
    assert body["message"] == "题型只能是 single / multiple / judge"


def test_create_question_rejects_letter_outside_options(client: TestClient) -> None:
    """答案字母越界：message 逐字给出「答案「BD」中的 D 不在已填写选项内（现有：A、B、C）」。"""
    body = error_of(
        _create(
            client,
            **_valid_create_payload(
                answer="BD", options={"A": "甲", "B": "乙", "C": "丙"}, qtype="multiple"
            ),
        ),
        400,
        40001,
    )
    assert body["message"] == "答案「BD」中的 D 不在已填写选项内（现有：A、B、C）"


def test_create_question_rejects_bad_judge_answer(client: TestClient) -> None:
    """判断题答案只能是 √ 或 ×（A/B 不再被接受）。"""
    body = error_of(
        _create(
            client,
            **_valid_create_payload(qtype="judge", answer="A", options={}),
        ),
        400,
        40001,
    )
    assert body["message"] == "判断题答案只能是 √ 或 ×"


def test_create_question_judge_and_auto_number(client: TestClient) -> None:
    """判断题无选项；题号留空自动取该库内数字题号 max+1。"""
    first = data_of(_create(client, **_valid_create_payload()))["question"]
    assert first["question_no"] == "1"

    judge = data_of(
        _create(
            client,
            **_valid_create_payload(
                stem="氧气筒内氧气可用尽后再更换。",
                options={},
                answer="√",
                qtype="judge",
                analysis="",
            ),
        )
    )["question"]
    assert judge["qtype"] == "judge"
    assert judge["options"] == {}
    assert judge["answer"] == "√"
    assert judge["question_no"] == "2"

    explicit = data_of(_create(client, **_valid_create_payload(question_no="12")))["question"]
    assert explicit["question_no"] == "12"
    # 取纯数字题号 max+1 → 13（不是 12+1 之外的任何值）
    after = data_of(_create(client, **_valid_create_payload()))["question"]
    assert after["question_no"] == "13"


def test_create_question_reuses_manual_library_and_isolates_libraries(
    client: TestClient,
) -> None:
    """同名题库复用同一条 import_files；不同名各自建库并各自从 1 开始编号。"""
    first = data_of(_create(client, **_valid_create_payload(library_name="手动录入")))
    second = data_of(_create(client, **_valid_create_payload(library_name="  手动录入  ")))
    assert second["library"]["created"] is False
    assert second["library"]["id"] == first["library"]["id"]
    assert second["library"]["import_id"] == first["library"]["import_id"]
    assert second["question"]["question_no"] == "2"
    # 只建了 1 条批次与 1 条文件
    assert data_of(client.get("/api/stats"))["total_imports"] == 1
    assert data_of(client.get("/api/stats"))["total_files"] == 1

    other = data_of(
        _create(client, **_valid_create_payload(library_name="科室新规范", question_no=None))
    )
    assert other["library"]["created"] is True
    assert other["library"]["id"] != first["library"]["id"]
    assert other["question"]["question_no"] == "1"
    assert data_of(client.get("/api/stats"))["total_files"] == 2

    libraries = data_of(client.get("/api/libraries"))["items"]
    assert {item["name"] for item in libraries} == {"手动录入", "科室新规范"}
    assert all(item["kind"] == "manual" for item in libraries)


def test_create_question_keeps_minor_defects_as_anomalies(client: TestClient) -> None:
    """题干 1–2 字、选项 1–3 个、显式重复题号都不拦，落成异常。"""
    short = data_of(
        _create(
            client,
            **_valid_create_payload(
                stem="查", options={"A": "甲", "B": "乙", "C": "丙"}, answer="A"
            ),
        )
    )
    types = sorted(a["type"] for a in short["anomalies"])
    assert types == ["empty_stem", "too_few_options"]

    dup_one = data_of(_create(client, **_valid_create_payload(question_no="7")))
    assert [a["type"] for a in dup_one["anomalies"]] == []
    dup_two = data_of(_create(client, **_valid_create_payload(question_no="7")))
    assert [a["type"] for a in dup_two["anomalies"]] == ["duplicate_number"]


def test_create_question_defaults_library_name(client: TestClient) -> None:
    """省略 library_name → 落缺省「手动录入」。"""
    payload = _valid_create_payload()
    payload.pop("library_name")
    data = data_of(_create(client, **payload))
    assert data["library"]["name"] == config.DEFAULT_LIBRARY_NAME
    assert data["library"]["file_type"] == "manual"
    assert data["library"]["created"] is True


# ===========================================================================
# 12. 题库聚合：GET /api/libraries
# ===========================================================================
def test_libraries_aggregates_history_and_manual(client: TestClient) -> None:
    """按 (filename, file_type) 分组聚合：历史 docx 多次导入合并为 1 条，手录库同列一条。"""
    # 历史数据：两条同名 docx 批次（模拟 v1.2 导入记录）
    with db.get_conn() as conn:
        for _ in range(2):
            cursor = conn.execute(
                "INSERT INTO imports (status, total_files, ok_files, committed_questions)"
                " VALUES ('committed', 1, 1, 0)"
            )
            import_id = int(cursor.lastrowid)
            cursor = conn.execute(
                "INSERT INTO import_files (import_id, filename, file_type, status)"
                " VALUES (?, ?, 'docx', 'parsed')",
                (import_id, "1-胡英芳--静脉治疗护理技术操作标准试题.docx"),
            )
            file_id = int(cursor.lastrowid)
            for no in ("1", "2"):
                conn.execute(
                    "INSERT INTO questions (import_file_id, question_no, qtype, stem,"
                    " options_json, answer, analysis, status)"
                    " VALUES (?, ?, 'single', '历史 docx 题目内容', '{\"A\": \"甲\"}', 'A', '',"
                    " 'published')",
                    (file_id, no),
                )
        # 脏 file_type：未知值应归 other
        cursor = conn.execute(
            "INSERT INTO imports (status, total_files, ok_files) VALUES ('committed', 1, 1)"
        )
        weird_import = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO import_files (import_id, filename, file_type, status)"
            " VALUES (?, '怪文件.rtf', 'rtf', 'parsed')",
            (weird_import,),
        )
        weird_file_id = int(cursor.execute("SELECT MAX(id) AS m FROM import_files").fetchone()["m"])
        conn.execute(
            "INSERT INTO questions (import_file_id, question_no, qtype, stem, options_json,"
            " answer, analysis, status) VALUES (?, '1', 'single', '脏类型题目', '{}', 'A', '',"
            " 'published')",
            (weird_file_id,),
        )
        # 0 题的记录不应出现
        cursor = conn.execute(
            "INSERT INTO imports (status, total_files, ok_files) VALUES ('committed', 1, 1)"
        )
        empty_import = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO import_files (import_id, filename, file_type, status)"
            " VALUES (?, '空库.md', 'md', 'parsed')",
            (empty_import,),
        )

    md_payload = _basic_import(client, "basic.md")
    _publish(client, md_payload["import_id"])
    _create(client, **_valid_create_payload())

    libraries = data_of(client.get("/api/libraries"))["items"]
    by_name = {item["name"]: item for item in libraries}

    # 手录库
    manual = by_name["手动录入"]
    assert manual["kind"] == "manual"
    assert manual["question_count"] == 1
    # 历史 docx：两条同名批次合并为 1 条，题数合计
    docx = by_name["1-胡英芳--静脉治疗护理技术操作标准试题.docx"]
    assert docx["kind"] == "docx"
    assert docx["question_count"] == 4
    # md 导入
    assert by_name["basic.md"]["kind"] == "md"
    assert by_name["basic.md"]["question_count"] == 4
    # 未知 file_type → other
    assert by_name["怪文件.rtf"]["kind"] == "other"
    # 0 题的记录不出现
    assert "空库.md" not in by_name

    # 排序契约：last_updated DESC, name ASC（同一秒内按名称升序，永不丢条目）
    for prev, curr in zip(libraries, libraries[1:]):
        assert prev["last_updated"] >= curr["last_updated"]
        if prev["last_updated"] == curr["last_updated"]:
            assert prev["name"] <= curr["name"]
    # 刚录过的手录库不早于任何历史库
    assert manual["last_updated"] >= docx["last_updated"]
    # 每条都必须带齐契约字段
    for item in libraries:
        assert set(item) == {
            "id",
            "name",
            "file_type",
            "kind",
            "question_count",
            "last_updated",
        }


def test_libraries_reflects_question_deletion(client: TestClient) -> None:
    """删空题库后不再出现在下拉里（计数实时聚合，用快照会残留）。"""
    data = data_of(_create(client, **_valid_create_payload()))
    question_id = int(data["question"]["id"])
    assert len(data_of(client.get("/api/libraries"))["items"]) == 1

    data_of(client.delete(f"/api/questions/{question_id}"))
    assert data_of(client.get("/api/libraries"))["items"] == []


# ===========================================================================
# 13. 错误码统一性
# ===========================================================================
def test_error_envelope_and_codes(client: TestClient) -> None:
    # 40401：资源不存在
    error_of(client.get("/api/questions/999999"), 404, 40401)
    error_of(client.get("/api/imports/999999"), 404, 40401)
    error_of(client.post("/api/imports/999999/commit"), 404, 40401)

    # 40001：参数错误（Query 校验失败也走统一外壳）
    for params in ({"page": 0}, {"size": 0}, {"size": 101}):
        error_of(client.get("/api/imports", params=params), 400, 40001)
        error_of(client.get("/api/search", params=params), 400, 40001)

    # 未注册的 /api 路径 → 40401
    error_of(client.get("/api/not-exists"), 404, 40401)


def test_response_envelope_shape(client: TestClient) -> None:
    for path in (
        "/api/health",
        "/api/stats",
        "/api/imports",
        "/api/search?q=",
        "/api/anomalies",
        "/api/libraries",
    ):
        body = client.get(path).json()
        assert set(body) == {"code", "message", "data"}, path
        assert body["code"] == 0
        assert body["message"] == "ok"


# ---------------------------------------------------------------------------
# v1.3 QA 回归：手录库与同名导入库必须隔离（阻塞-1）
# ---------------------------------------------------------------------------
def test_create_question_isolates_from_imported_library(client: TestClient) -> None:
    """手录题库名与已导入文件同名时，必须新建 manual 库，绝不写进导入库。

    回归 v1.3 QA 阻塞-1：``find_manual_library`` 曾只按 ``filename`` 查找，
    用户在下拉里点选一个导入题库名（正常操作路径）就会把题写进那条 md 记录，
    既不产生 manual 库，也会让导入题库的题数凭空 +1。
    """
    uploaded = _md_import(client, "同名题库.md", BASIC_MD)
    import_id = int(uploaded["import_id"])
    _publish(client, import_id)
    md_file_id = int(uploaded["files"][0]["file_id"])
    md_total = int(
        data_of(client.get(f"/api/imports/{import_id}"))["files"][0]["total_questions"]
    )
    assert md_total > 0

    created = data_of(_create(client, **_valid_create_payload(library_name="同名题库.md")))
    assert created["library"]["created"] is True
    assert created["library"]["file_type"] == "manual"
    assert created["library"]["id"] != md_file_id
    assert created["question"]["import_file_id"] == created["library"]["id"]
    # 新库题号从 1 开始，不续接导入库
    assert created["question"]["question_no"] == "1"

    # 导入库题数不变（未被污染）
    after = int(
        data_of(client.get(f"/api/imports/{import_id}"))["files"][0]["total_questions"]
    )
    assert after == md_total

    # 再录一题：仍落在 manual 库，题号 2
    second = data_of(_create(client, **_valid_create_payload(library_name="同名题库.md")))
    assert second["library"]["created"] is False
    assert second["library"]["id"] == created["library"]["id"]
    assert second["question"]["question_no"] == "2"


def test_libraries_do_not_merge_manual_and_imported_same_name(client: TestClient) -> None:
    """``/api/libraries`` 按 (filename, file_type) 分组：同名手录库与导入库是两条。"""
    uploaded = _md_import(client, "合并检查.md", BASIC_MD)
    _publish(client, int(uploaded["import_id"]))
    md_total = int(
        data_of(client.get(f"/api/imports/{uploaded['import_id']}"))["files"][0]["total_questions"]
    )
    data_of(_create(client, **_valid_create_payload(library_name="合并检查.md")))

    items = data_of(client.get("/api/libraries"))["items"]
    matched = [item for item in items if item["name"] == "合并检查.md"]
    assert len(matched) == 2, matched
    by_kind = {item["kind"]: item for item in matched}
    assert set(by_kind) == {"md", "manual"}
    assert by_kind["md"]["question_count"] == md_total
    assert by_kind["manual"]["question_count"] == 1
    # id 不相同，避免前端下拉把两条指向同一条记录
    assert by_kind["md"]["id"] != by_kind["manual"]["id"]
