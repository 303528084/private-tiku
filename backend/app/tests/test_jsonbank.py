"""JSON 题库导入（结构化映射）测试。

覆盖三类输入形态（单题库包 / 扁平数组 / 多题库合并包）与常见字段变体：
中英文题型、dict / 分隔文本 / 列表形式的选项、字符串与列表答案、
判断题 A/B「正确/错误」折叠、分段编号重排、编码（BOM / GB18030）、
非法 JSON 的错误文案，以及 6 类异常规则在结构化输入下的表现。

一条端到端用例走完 HTTP：上传 .json → 校验分片与题数 → 入库 → 检索 → 删除。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.main import app
from app.parser import pipeline
from app.parser.reader import ReaderError


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def write_json(
    tmp_path: Path,
    payload: Any,
    *,
    name: str = "bank.json",
    encoding: str = "utf-8",
) -> Path:
    """把 payload 写成 JSON 文件（可按指定编码，验证解码回退）。"""
    path = tmp_path / name
    path.write_bytes(json.dumps(payload, ensure_ascii=False, indent=2).encode(encoding))
    return path


def single(path: Path) -> pipeline.PartResult:
    """断言只有一个分片并返回它。"""
    parts = pipeline.run_parts(path)
    assert len(parts) == 1
    return parts[0]


def parsed_list(part: pipeline.PartResult) -> List[Any]:
    return [item.parsed for item in part.items]


def anomaly_types(part: pipeline.PartResult, index: int = 0) -> List[str]:
    return [anomaly.type for anomaly in part.items[index].anomalies]


# ---------------------------------------------------------------------------
# 形态一：单题库包（本批 29 个文件的格式）
# ---------------------------------------------------------------------------
BANK = {
    "title": "静脉治疗护理技术操作标准试题",
    "author": "血液一科 胡英芳",
    "source_file": "1-胡英芳--静脉治疗护理技术操作标准试题.docx",
    "questions": [
        {
            "number": 1,
            "type": "判断题",
            "content": "外周静脉穿刺应选择透明或纱布类无菌敷料固定穿刺针。(  )",
            "options": {"A": "正确", "B": "错误"},
            "answer": "B",
            "explanation": "静脉治疗护理技术操作标准-外周静脉穿刺",
        },
        {
            "number": 2,
            "type": "单选题",
            "content": "抗肿瘤药物外溢时以下处理不正确的是：(  )",
            "options": {"A": "穿戴防护用品", "B": "标明污染范围", "C": "用清水反复擦洗", "D": "记录外溢信息"},
            "answer": "C",
            "explanation": "职业防护",
        },
        {
            "number": 3,
            "type": "多选题",
            "content": "以下关于抗肿瘤药物防护正确的是：(  )",
            "options": {"A": "独立空间配制", "B": "配备溢出包", "C": "戴双层手套", "D": "全密闭式输注", "E": "污染物入毒性标识容器"},
            "answer": ["A", "C", "E"],
            "explanation": "职业防护",
        },
    ],
}


def test_single_bank_maps_fields(tmp_path: Path) -> None:
    part = single(write_json(tmp_path, BANK))

    assert part.name == "bank.json"
    assert part.question_count == 3
    questions = parsed_list(part)
    assert [question.no for question in questions] == ["1", "2", "3"]
    assert [question.qtype for question in questions] == ["judge", "single", "multiple"]
    # 判断题：A/B「正确/错误」被折叠为无选项 + √/×
    assert questions[0].options == {}
    assert questions[0].answer == "×"
    assert questions[0].stem.startswith("外周静脉穿刺")
    assert "( )" in questions[0].stem
    assert questions[0].analysis == "静脉治疗护理技术操作标准-外周静脉穿刺"
    # 单选：选项原样保留
    assert sorted(questions[1].options) == ["A", "B", "C", "D"]
    assert questions[1].answer == "C"
    # 多选：列表答案拼成字母串并按字母升序
    assert questions[2].answer == "ACE"
    assert len(questions[2].options) == 5
    assert all(not item.anomalies for item in part.items)


# ---------------------------------------------------------------------------
# 形态二：扁平数组 + 英文题型 + 分号文本选项
# ---------------------------------------------------------------------------
FLAT = [
    {
        "title": "中心静脉导管是指经锁骨下静脉置管，尖端位于上腔静脉的导管。(  )",
        "type": "judgement",
        "options": "A: 正确\rB: 错误",
        "answer": "B",
        "source": "1-夏蕾-静脉治疗护理技术操作标准",
        "explanation": "术语和定义",
    },
    {
        "title": "抗肿瘤药物外溢时以下处理不正确的是：(  )",
        "type": "single_choice",
        "options": "A: 穿戴个人防护用品\rB: 标明污染范围\rC: 用清水反复擦洗\rD: 记录外溢信息",
        "answer": "C",
        "source": "1-夏蕾",
        "explanation": "职业防护",
    },
    {
        "title": "以下关于抗肿瘤药物防护正确的是：(  )",
        "type": "multiple_choice",
        "options": "A: 独立空间配制\rB: 配备溢出包\rC: 戴双层手套\rD: 全密闭式输注\rE: 毒性标识容器",
        "answer": ["A", "C", "E"],
        "source": "1-夏蕾",
        "explanation": "职业防护",
    },
]


def test_flat_array_and_text_options(tmp_path: Path) -> None:
    part = single(write_json(tmp_path, FLAT, name="flat.json"))

    questions = parsed_list(part)
    assert part.question_count == 3
    # 无 number 字段时按出现顺序补 1..n
    assert [question.no for question in questions] == ["1", "2", "3"]
    assert [question.qtype for question in questions] == ["judge", "single", "multiple"]
    assert questions[0].answer == "×" and questions[0].options == {}
    assert questions[1].options == {
        "A": "穿戴个人防护用品",
        "B": "标明污染范围",
        "C": "用清水反复擦洗",
        "D": "记录外溢信息",
    }
    assert questions[2].answer == "ACE"
    assert questions[2].analysis == "职业防护"


# ---------------------------------------------------------------------------
# 形态三：多题库合并包
# ---------------------------------------------------------------------------
def test_multi_bank_parts_and_names(tmp_path: Path) -> None:
    payload = {
        "banks": [
            {"title": "静脉治疗护理技术操作标准", "author": "妇科 夏蕾", "questions": BANK["questions"]},
            {"title": "护理分级标准", "author": "张重阳", "questions": [BANK["questions"][1]]},
        ]
    }
    parts = pipeline.run_parts(write_json(tmp_path, payload, name="merged.json"))

    assert [part.name for part in parts] == [
        "静脉治疗护理技术操作标准（妇科 夏蕾）.json",
        "护理分级标准（张重阳）.json",
    ]
    assert [part.question_count for part in parts] == [3, 1]
    # 每个题库独立编号，互不影响；无重号时保留原始题号
    assert [question.no for question in parsed_list(parts[0])] == ["1", "2", "3"]
    assert [question.no for question in parsed_list(parts[1])] == ["2"]


def test_merged_pack_without_title_falls_back_to_filename(tmp_path: Path) -> None:
    payload = {"banks": [{"questions": [BANK["questions"][1]]}, {"questions": [BANK["questions"][1]]}]}
    parts = pipeline.run_parts(write_json(tmp_path, payload, name="anon.json"))
    assert [part.name for part in parts] == ["anon-1.json", "anon-2.json"]


# ---------------------------------------------------------------------------
# 判断题与答案归一
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("answer", "expected"),
    [("A", "√"), ("B", "×"), ("正确", "√"), ("错误", "×"), ("对", "√"), ("错", "×"), ("√", "√"), ("×", "×")],
)
def test_judge_answer_normalized(tmp_path: Path, answer: str, expected: str) -> None:
    payload = {
        "questions": [
            {
                "number": 1,
                "type": "判断题",
                "content": "铺好的无菌盘有效期为 4 小时。(  )",
                "options": {"A": "正确", "B": "错误"},
                "answer": answer,
            }
        ]
    }
    part = single(write_json(tmp_path, payload))
    question = part.items[0].parsed
    assert question.qtype == "judge"
    assert question.options == {}
    assert question.answer == expected


def test_judge_with_reversed_option_order(tmp_path: Path) -> None:
    payload = {
        "questions": [
            {
                "type": "判断题",
                "content": "氧气筒内氧气可用尽后再更换。(  )",
                "options": {"A": "错误", "B": "正确"},
                "answer": "A",
            }
        ]
    }
    question = single(write_json(tmp_path, payload)).items[0].parsed
    assert question.answer == "×"


def test_judge_without_options_keeps_answer(tmp_path: Path) -> None:
    payload = {
        "questions": [
            {"type": "判断题", "content": "氧气筒内氧气可用尽后再更换。", "answer": "错"}
        ]
    }
    question = single(write_json(tmp_path, payload)).items[0].parsed
    assert question.qtype == "judge"
    assert question.options == {}
    assert question.answer == "×"


# ---------------------------------------------------------------------------
# 题型冲突与未知题型
# ---------------------------------------------------------------------------
def test_answer_wins_over_declared_single_type(tmp_path: Path) -> None:
    payload = {
        "questions": [
            {
                "type": "单选题",
                "content": "以下正确的是：(  )",
                "options": {"A": "甲", "B": "乙", "C": "丙", "D": "丁"},
                "answer": "ABD",
            }
        ]
    }
    question = single(write_json(tmp_path, payload)).items[0].parsed
    assert question.qtype == "multiple"
    assert question.answer == "ABD"


def test_unknown_type_falls_back_to_answer(tmp_path: Path) -> None:
    payload = {
        "questions": [
            {"type": "填空题", "content": "无菌盘有效期____小时。", "answer": "4"}
        ]
    }
    part = single(write_json(tmp_path, payload))
    question = part.items[0].parsed
    assert question.qtype == "unknown"
    assert question.answer == "4"
    # 非选择题不做选项数量校验
    assert anomaly_types(part) == []


# ---------------------------------------------------------------------------
# 编号：分段编号重排
# ---------------------------------------------------------------------------
def test_section_numbering_renumbered_without_duplicate_anomaly(tmp_path: Path) -> None:
    payload = {
        "questions": [
            {"number": 1, "type": "判断题", "content": "题干一(  )", "answer": "√"},
            {"number": 2, "type": "判断题", "content": "题干二(  )", "answer": "×"},
            {"number": 1, "type": "单选题", "content": "题干三(  )", "options": {"A": "甲", "B": "乙", "C": "丙", "D": "丁"}, "answer": "A"},
            {"number": 2, "type": "单选题", "content": "题干四(  )", "options": {"A": "甲", "B": "乙", "C": "丙", "D": "丁"}, "answer": "B"},
            {"number": 1, "type": "多选题", "content": "题干五(  )", "options": {"A": "甲", "B": "乙", "C": "丙", "D": "丁", "E": "戊"}, "answer": "AB"},
        ]
    }
    part = single(write_json(tmp_path, payload))
    assert [question.no for question in parsed_list(part)] == ["1", "2", "3", "4", "5"]
    assert "duplicate_number" not in {anomaly.type for item in part.items for anomaly in item.anomalies}


def test_unique_numbers_are_preserved(tmp_path: Path) -> None:
    payload = {
        "questions": [
            {"number": 7, "type": "判断题", "content": "题干甲(  )", "answer": "√"},
            {"number": 9, "type": "判断题", "content": "题干乙(  )", "answer": "×"},
        ]
    }
    part = single(write_json(tmp_path, payload))
    assert [question.no for question in parsed_list(part)] == ["7", "9"]


# ---------------------------------------------------------------------------
# 异常规则（沿用 validator 的 6 类）
# ---------------------------------------------------------------------------
def test_anomalies_detected_for_structured_input(tmp_path: Path) -> None:
    payload = {
        "questions": [
            {"number": 1, "type": "单选题", "content": "", "options": {}, "answer": ""},
            {"number": 2, "type": "单选题", "content": "只有三个选项(  )", "options": {"A": "甲", "B": "乙", "C": "丙"}, "answer": "A"},
            {"number": 3, "type": "判断题", "content": "缺答案(  )"},
        ]
    }
    part = single(write_json(tmp_path, payload))
    assert "empty_stem" in anomaly_types(part, 0)
    assert "missing_answer" in anomaly_types(part, 0)
    assert "missing_options" in anomaly_types(part, 0)
    assert "too_few_options" in anomaly_types(part, 1)
    assert "missing_answer" in anomaly_types(part, 2)


def test_non_dict_question_becomes_parse_error(tmp_path: Path) -> None:
    payload = {"questions": ["这不是一个题目对象"]}
    part = single(write_json(tmp_path, payload))
    assert part.question_count == 1
    assert "parse_error" in anomaly_types(part)
    assert part.items[0].parsed.parse_error is True


# ---------------------------------------------------------------------------
# 编码与错误处理
# ---------------------------------------------------------------------------
def test_bom_and_gb18030_encoded_json(tmp_path: Path) -> None:
    for encoding in ("utf-8-sig", "gb18030"):
        path = write_json(tmp_path, BANK, name=f"bank-{encoding}.json", encoding=encoding)
        part = single(path)
        assert part.question_count == 3
        assert part.items[0].parsed.qtype == "judge"


def test_invalid_json_reports_position(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text('{"questions": [ , ]}', encoding="utf-8")
    with pytest.raises(ReaderError) as excinfo:
        pipeline.run_parts(path)
    assert "JSON 格式错误" in str(excinfo.value)


def test_missing_questions_raises(tmp_path: Path) -> None:
    with pytest.raises(ReaderError) as excinfo:
        pipeline.run_parts(write_json(tmp_path, {"title": "空包"}))
    assert "未找到" in str(excinfo.value)


def test_empty_questions_list_raises(tmp_path: Path) -> None:
    with pytest.raises(ReaderError):
        pipeline.run_parts(write_json(tmp_path, {"questions": []}))


def test_empty_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_bytes(b"")
    with pytest.raises(ReaderError):
        pipeline.run_parts(path)


def test_question_order_preserved(tmp_path: Path) -> None:
    payload = {
        "questions": [
            {"number": 3, "type": "判断题", "content": "第三题(  )", "answer": "√"},
            {"number": 1, "type": "判断题", "content": "第一题(  )", "answer": "×"},
        ]
    }
    assert [q.stem[:3] for q in parsed_list(single(write_json(tmp_path, payload)))] == ["第三题", "第一题"]


# ---------------------------------------------------------------------------
# HTTP 端到端
# ---------------------------------------------------------------------------
@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterable[TestClient]:
    """独立临时库（不污染仓库内 data/）。"""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "DB_PATH", data_dir / "tiku.db")
    monkeypatch.setattr(config, "UPLOAD_DIR", data_dir / "uploads")
    db.reset_init_flag()
    db.init_db()
    with TestClient(app) as test_client:
        yield test_client
    db.reset_init_flag()


def test_upload_json_end_to_end(client: TestClient, tmp_path: Path) -> None:
    """上传 .json → 分片成文件记录 → 入库 → 检索 → 删除批次。"""
    content = json.dumps(BANK, ensure_ascii=False).encode("utf-8")
    response = client.post(
        "/api/imports",
        files=[("files[]", ("题库.json", content, "application/json"))],
    )
    body: Dict[str, Any] = response.json()
    assert body["code"] == 0, body
    data = body["data"]
    assert len(data["files"]) == 1
    file_info = data["files"][0]
    assert file_info["filename"] == "题库.json"
    assert file_info["file_type"] == "json"
    assert file_info["status"] == "parsed"
    assert file_info["total_questions"] == 3

    detail = client.get(f"/api/imports/{data['import_id']}").json()["data"]
    assert detail["import"]["total_files"] == 1
    assert detail["import"]["ok_files"] == 1
    assert detail["import"]["total_questions"] == 3

    committed = client.post(f"/api/imports/{data['import_id']}/commit").json()["data"]
    assert committed["committed"] == 3

    search = client.get("/api/search", params={"q": "抗肿瘤"}).json()["data"]
    assert search["total"] == 2
    assert "<mark>" in json.dumps(search["items"], ensure_ascii=False)
    assert {item["qtype"] for item in search["items"]} == {"single", "multiple"}

    judge = client.get("/api/search", params={"q": "无菌敷料"}).json()["data"]
    assert judge["total"] == 1
    assert judge["items"][0]["answer"] == "×"

    deleted = client.delete(f"/api/imports/{data['import_id']}").json()["data"]
    assert deleted["deleted_questions"] == 3
    assert client.get("/api/search", params={"q": "抗肿瘤"}).json()["data"]["total"] == 0


def test_upload_merged_json_creates_one_record_per_bank(client: TestClient, tmp_path: Path) -> None:
    """多题库包 → 一个分片一条文件记录，题数合计正确。"""
    payload = {
        "banks": [
            {"title": "标准甲", "author": "作者甲", "questions": BANK["questions"][:2]},
            {"title": "标准乙", "author": "作者乙", "questions": BANK["questions"][2:]},
        ]
    }
    content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    body = client.post(
        "/api/imports", files=[("files[]", ("合并.json", content, "application/json"))]
    ).json()
    assert body["code"] == 0, body
    files = body["data"]["files"]
    assert [item["filename"] for item in files] == ["标准甲（作者甲）.json", "标准乙（作者乙）.json"]
    assert [item["total_questions"] for item in files] == [2, 1]

    detail = client.get(f"/api/imports/{body['data']['import_id']}").json()["data"]
    assert detail["import"]["total_files"] == 2
    assert detail["import"]["ok_files"] == 2
    assert detail["import"]["total_questions"] == 3


def test_upload_invalid_json_marks_file_failed(client: TestClient) -> None:
    response = client.post(
        "/api/imports",
        files=[("files[]", ("坏.json", b"{not json", "application/json"))],
    )
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == 50001
    assert body["data"]["files"][0]["status"] == "failed"
    assert "JSON" in body["data"]["files"][0]["error"]


def test_upload_unsupported_type_message_mentions_json(client: TestClient) -> None:
    response = client.post("/api/imports", files=[("files[]", ("a.csv", b"a,b", "text/csv"))])
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 42201
    assert ".json" in body["message"]
