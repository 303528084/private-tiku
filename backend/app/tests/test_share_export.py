"""分享包导出测试（T02）。

覆盖架构 §9.1 T02 验收：

1. ZIP 内确有 ``manifest.json`` / ``bank.md`` / ``media/``；
2. manifest 六个契约字段齐全，``qtype_distribution`` 与库内 ``GROUP BY qtype`` 一致；
3. ``bank.md`` 用自家 Markdown 解析器解析回来，逐字段与导出前全等（往返一致性）；
4. ``namelist()`` 无绝对路径、无 ``..``；
5. 1 万题的性能实测（真实耗时与体积，见 ``test_large_bank_performance``）；
6. ``GET /api/export`` 与 ``/api/export/download`` 行为不变。
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.main import app
from app.parser.markdown import parse_bank
from app.parser.normalize import normalize_text
from app.share.manifest import MANIFEST_SCHEMA_VERSION
from app.share.markdown_render import QTYPE_LABEL
from app.share.pack import build_share_pack

SAMPLE_MD = """# 护理学基础试题（分享包样本）


## 1. [单选题]

脉搏短绌常见于哪种心律失常?

- **A.** 房颤
- **B.** 室颤
- **C.** 房扑
- **D.** 期前收缩

**答案**：A

**解析**：脉搏短绌多见于心房颤动。

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

正常成人腋温范围为36.0~37.0度。

**答案**：对

---

## 4. [单选题]

预防压疮的护理措施不包括?

- **A.** 定时翻身
- **B.** 保持皮肤清洁干燥
- **C.** 使用减压床垫
- **D.** 局部用力按摩受压皮肤

**答案**：D

**解析**：受压皮肤按摩会加重组织损伤。
"""


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


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def seed(client: TestClient, text: str = SAMPLE_MD, name: str = "护理学基础.md") -> int:
    """上传并确认入库一份 Markdown，返回 import_id。"""
    response = client.post(
        "/api/imports",
        files=[("files[]", (name, text.encode("utf-8"), "text/markdown"))],
    )
    assert response.status_code == 200, response.text
    import_id = int(response.json()["data"]["import_id"])
    committed = client.post(f"/api/imports/{import_id}/commit")
    assert committed.status_code == 200, committed.text
    return import_id


def fetch_pack(client: TestClient, **params: Any) -> Tuple[bytes, Dict[str, Any]]:
    """取分享包字节并返回 (bytes, 响应头)。"""
    response = client.get("/api/export/share-pack", params=params)
    assert response.status_code == 200, response.text
    return response.content, dict(response.headers)


def pack_manifest(payload: bytes) -> Dict[str, Any]:
    """从 ZIP 字节里读 manifest。"""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return json.loads(archive.read(config.SHARE_MANIFEST_NAME).decode("utf-8"))


def published_questions() -> List[Dict[str, Any]]:
    """按导出顺序读库内已入库题目（与 ``_fetch_questions`` 同序）。"""
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT q.* FROM questions q
            WHERE q.status = 'published'
            ORDER BY q.import_file_id ASC, q.id ASC
            """
        ).fetchall()
    return [db.question_to_dict(row) for row in rows]


# ===========================================================================
# ① ZIP 结构
# ===========================================================================
def test_pack_contains_manifest_bank_and_media(client: TestClient) -> None:
    """ZIP 内确有 manifest.json / bank.md / media/（目录条目）。"""
    seed(client)
    payload, headers = fetch_pack(client)

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        assert config.SHARE_MANIFEST_NAME in names
        assert config.SHARE_BANK_MD_NAME in names
        assert "media/" in names, names
        # media/ 必须是**目录条目**（名字以 / 结尾），不是空文件
        assert archive.getinfo("media/").is_dir()

    assert headers["content-type"] == "application/zip"
    disposition = headers["content-disposition"]
    assert disposition.startswith('attachment; filename="tiku-share-')
    assert disposition.endswith('.zip"')
    # 文件名必须 URL/ASCII 安全：不含中文、不含空格
    filename = disposition.split('filename="')[1].rstrip('"')
    assert filename.isascii() and " " not in filename


# ===========================================================================
# ② manifest 字段
# ===========================================================================
def test_manifest_fields_and_qtype_distribution(client: TestClient) -> None:
    """六个契约字段齐全，题型分布与库内 GROUP BY 一致。"""
    seed(client)
    manifest = pack_manifest(fetch_pack(client)[0])

    for field in (
        "schema_version",
        "bank_name",
        "question_count",
        "qtype_distribution",
        "generated_at",
        "app_version",
    ):
        assert field in manifest, field

    assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION == 1
    assert manifest["question_count"] == 4
    assert manifest["app_version"] == config.APP_VERSION
    assert "T" in manifest["generated_at"]  # ISO8601 带时区

    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT qtype, COUNT(*) AS c FROM questions
            WHERE status = 'published' GROUP BY qtype
            """
        ).fetchall()
    expected = {
        QTYPE_LABEL.get(row["qtype"], row["qtype"]): int(row["c"]) for row in rows
    }
    assert manifest["qtype_distribution"] == expected
    assert manifest["qtype_distribution"] == {"判断题": 1, "单选题": 2, "多选题": 1}

    # 中文题库名只出现在 manifest 里（文件名不含中文）
    assert manifest["bank_name"] == "护理学基础"


def test_manifest_has_integrity_fields(client: TestClient) -> None:
    """完整性字段存在且是 64 位十六进制摘要（供 T03 校验）。"""
    seed(client)
    manifest = pack_manifest(fetch_pack(client)[0])
    for field in ("bank_sha256", "manifest_sha256"):
        value = manifest[field]
        assert isinstance(value, str) and len(value) == 64
        int(value, 16)


# ===========================================================================
# ③ 往返一致性
# ===========================================================================
def test_bank_md_roundtrip_matches_db(client: TestClient) -> None:
    """``bank.md`` 解析回来后，逐题逐字段与导出前全等。"""
    seed(client)
    before = published_questions()
    assert len(before) == 4

    payload, _headers = fetch_pack(client)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        md_text = archive.read(config.SHARE_BANK_MD_NAME).decode("utf-8")

    bank = parse_bank(md_text, name="roundtrip.md")
    after = bank.questions
    assert len(after) == len(before)

    for original, parsed in zip(before, after):
        assert parsed.stem == normalize_text(original["stem"])
        assert dict(parsed.options) == dict(original["options"])
        assert parsed.answer == original["answer"]
        assert parsed.qtype == original["qtype"]
        # 解析（analysis）为可选字段：有则必须一致
        assert (parsed.analysis or "") == normalize_text(original["analysis"] or "")


# ===========================================================================
# ④ 成员名安全
# ===========================================================================
def test_member_names_are_relative_and_safe(client: TestClient) -> None:
    """``namelist()`` 无绝对路径、无 ``..``、无盘符。"""
    seed(client)
    with zipfile.ZipFile(io.BytesIO(fetch_pack(client)[0])) as archive:
        names = archive.namelist()
    assert names, "ZIP 不能为空"
    for name in names:
        assert not name.startswith("/"), name
        assert not name.startswith("\\"), name
        assert ".." not in name.split("/"), name
        assert not (len(name) > 1 and name[1] == ":"), name


# ===========================================================================
# ⑥ 既有导出路由不变
# ===========================================================================
def test_existing_export_routes_unchanged(client: TestClient) -> None:
    """``/api/export``（json/md）与 ``/api/export/download`` 行为不变。"""
    seed(client)

    json_response = client.get("/api/export", params={"format": "json"})
    assert json_response.status_code == 200
    assert json_response.headers["content-type"].startswith("application/json")
    assert json_response.headers["x-total-count"] == "4"
    body = json.loads(json_response.content.decode("utf-8"))
    assert body["total"] == 4
    assert len(body["items"]) == 4
    assert set(body["items"][0]) >= {"id", "question_no", "qtype", "stem", "options", "answer"}

    md_response = client.get("/api/export", params={"format": "md"})
    assert md_response.status_code == 200
    assert md_response.headers["content-type"].startswith("text/markdown")
    assert md_response.text.startswith("# 私有护理题库导出")

    download = client.get("/api/export/download", params={"format": "md"})
    assert download.status_code == 200
    assert download.json() == {
        "code": 0,
        "message": "ok",
        "data": {"url": "/api/export?format=md"},
    }


# ===========================================================================
# 边界：空结果集 / 筛选
# ===========================================================================
def test_empty_result_returns_400(client: TestClient) -> None:
    """没有已入库题目 → 400 / 40001。"""
    response = client.get("/api/export/share-pack")
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["code"] == 40001
    assert body["message"] == "没有可导出的题目"
    assert body["data"] is None


def test_filters_are_supported(client: TestClient) -> None:
    """题型筛选与非法值校验（与 ``/api/search`` 同口径）。"""
    seed(client)

    only_judge = pack_manifest(fetch_pack(client, type="judge")[0])
    assert only_judge["question_count"] == 1
    assert only_judge["qtype_distribution"] == {"判断题": 1}

    invalid = client.get("/api/export/share-pack", params={"type": "essay"})
    assert invalid.status_code == 400
    assert invalid.json()["code"] == 40001
    assert "非法的题型筛选值" in invalid.json()["message"]

    # 筛选后为空 → 400
    empty = client.get("/api/export/share-pack", params={"doc_id": 999999})
    assert empty.status_code == 400
    assert empty.json()["message"] == "没有可导出的题目"


# ===========================================================================
# ⑤ 性能实测（1 万题）
# ===========================================================================
def test_large_bank_performance(client: TestClient, capsys: pytest.CaptureFixture[str]) -> None:
    """1 万题：导出耗时与 ZIP 体积实测（阈值按架构 §9.1 T02 ⑤）。

    题干/选项刻意**造得各不相同**：全同内容的样本压缩比虚高（实测只有 0.06MB），
    不能代表真实题库。这里用确定性的伪随机词表拼句子，体积接近真实分布。
    """
    total = 10_000
    words = [
        "患者", "护理", "观察", "评估", "记录", "输液", "血压", "心率",
        "呼吸", "体温", "用药", "宣教", "翻身", "消毒", "隔离", "吸氧",
    ]
    rows = []
    for index in range(total):
        base = index * 7
        stem = (
            f"第{index + 1}题："
            + "".join(words[(base + step * 3) % len(words)] for step in range(14))
            + "，应采取的首要护理措施是？"
        )
        options = {
            "A": f"{words[(base + 1) % len(words)]}{words[(base + 5) % len(words)]}",
            "B": f"{words[(base + 2) % len(words)]}{words[(base + 9) % len(words)]}",
            "C": f"{words[(base + 3) % len(words)]}{words[(base + 13) % len(words)]}",
            "D": f"{words[(base + 4) % len(words)]}{words[(base + 15) % len(words)]}",
        }
        rows.append(
            (
                str(index + 1),
                "single",
                stem,
                json.dumps(options, ensure_ascii=False),
                "A",
                f"解析：{words[base % len(words)]}相关处理，需结合{words[(base + 6) % len(words)]}判断。",
            )
        )

    with db.get_conn() as conn:
        conn.execute("INSERT INTO imports (status) VALUES ('committed')")
        conn.execute(
            "INSERT INTO import_files (import_id, filename, file_type) VALUES (1, '压测库.md', 'md')"
        )
        conn.executemany(
            """
            INSERT INTO questions (import_file_id, question_no, qtype, stem, options_json,
                                   answer, analysis, status)
            VALUES (1, ?, ?, ?, ?, ?, ?, 'published')
            """,
            rows,
        )

    questions = published_questions()
    assert len(questions) == total

    started = time.perf_counter()
    payload = build_share_pack(questions, "压测库")
    build_seconds = time.perf_counter() - started

    started = time.perf_counter()
    response = client.get("/api/export/share-pack")
    http_seconds = time.perf_counter() - started
    assert response.status_code == 200

    size_mb = len(payload) / (1024 * 1024)
    manifest = pack_manifest(payload)
    print(
        f"\n[性能实测] {total} 题：build_share_pack={build_seconds:.2f}s，"
        f"HTTP 全链路={http_seconds:.2f}s，ZIP={size_mb:.2f}MB，"
        f"manifest.question_count={manifest['question_count']}"
    )

    assert manifest["question_count"] == total
    assert build_seconds < 5.0, build_seconds
    assert size_mb < 20.0, size_mb
