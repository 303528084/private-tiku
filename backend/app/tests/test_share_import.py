"""分享包导入测试（T03）。

覆盖架构 §9.1 T03 验收：

1. 空库导入 T02 导出的包 → 题数 / 题型 / 答案全等，且**立即可被** ``/api/search``
   检索到（无需 commit，导入即 ``published``）；
2. 同一包重复导入 → ``added=0``、``duplicates=N``、库内题数不变、不写库不备份；
3. 恶意 ZIP（``../evil.md``）→ 拒绝，且目标目录下**无任何新文件落盘**；
4. 绝对路径（``/etc/passwd``、``C:\\evil.md``）、非法扩展名（``.sh`` / ``.exe``）、
   符号链接 → 拒绝且不落盘；
5. 解压炸弹三档（单文件 >20MB / 总量 >200MB / 压缩比 >100:1）→ 全部中止且不落盘；
6. 备份在写库**之前**产生（备份内题数 = 导入前题数）且 ``integrity_check == ok``；
   备份失败 → 中止导入且库内数据不变；
7. 同 ``question_no`` 但内容不同 → 两条都入库（``question_no`` 不参与查重）。

安全项（3/4/5）统一采用「拒绝 + 未落盘」双重证据：既断言接口返回 400 与具体
文案，又断言 ``DATA_DIR/share_staging`` 下**没有任何文件**（阶段 1 的校验全部
基于 ``ZipInfo`` 元数据，全部通过后才 ``mkdir`` 并写第一个字节）。

关于「总量 >200MB」：样本用「窗口内可压缩、窗口间互不匹配」的随机块拼出来 ——
11 个 19.9MB 成员合计解压 208.8MB，而整包 ZIP 只有 ~12MB（能通过 API 层
``config.MAX_FILE_SIZE`` 的 20MB 上限），单成员压缩比仅 ~17:1、成员数 11，
因此**唯一越界的就是总量**，端到端一次性验证到位（见
``test_bomb_total_size_end_to_end_rejected``）。
"""

from __future__ import annotations

import io
import json
import logging
import random
import sqlite3
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.main import app
from app.parser.markdown import parse_bank
from app.parser.normalize import normalize_text
from app.share import unpack
from app.share.manifest import build_manifest
from app.share.unpack import SharePackError, extract_share_pack

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

# 只有一个题的「旧库」，用于验证跨库重号：每个 bank 内部各自从 1 开始编号
# （``pipeline.py:137-139`` 的重编号是**每个 bank 内部**的，跨文件不生效），
# 所以「旧库第 1 题」与「分享包第 1 题」都会是 ``question_no='1'``。
SINGLE_Q_MD = """# 旧库（单题）


## 1. [单选题]

无菌操作原则中，无菌区与非无菌区的界定依据是什么?

- **A.** 无菌物品存放区域
- **B.** 人员流动区域
- **C.** 清洁区
- **D.** 污染区

**答案**：A
"""


# ---------------------------------------------------------------------------
# fixtures
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


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def make_zip(
    members: Sequence[Tuple[Any, bytes]],
    *,
    compress: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    """按给定成员（``str`` 名字或 ``ZipInfo``）打一个 ZIP 字节流。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compress) as archive:
        for name, payload in members:
            if isinstance(name, zipfile.ZipInfo):
                archive.writestr(name, payload)
            else:
                info = zipfile.ZipInfo(filename=name)
                info.compress_type = compress
                archive.writestr(info, payload)
    return buffer.getvalue()


def make_pack(
    bank_md: str = SAMPLE_MD,
    *,
    bank_name: str = "护理学基础",
    extra_members: Sequence[Tuple[Any, bytes]] = (),
    tamper_manifest: Optional[Dict[str, Any]] = None,
    tamper_bank: Optional[bytes] = None,
    drop: Sequence[str] = (),
) -> bytes:
    """用 T02 的 manifest 契约手工组一个分享包（可注入恶意成员）。

    Args:
        bank_md: 正文。
        bank_name: 题库名（只进 manifest）。
        extra_members: 追加的成员（恶意/炸弹样本）。
        tamper_manifest: 覆盖 manifest 字段（用于篡改校验）。
        tamper_bank: 替换写进包的正文字节（manifest 仍按原正文算摘要 → 触发
            ``bank.md 校验失败``）。
        drop: 需要从包里去掉的标准成员名。
    """
    body = bank_md.encode("utf-8")
    parsed = parse_bank(bank_md, name="pack.md").questions
    manifest = build_manifest(
        [{"qtype": question.qtype} for question in parsed], bank_name, bank_md=body
    )
    if tamper_manifest:
        manifest.update(tamper_manifest)
    members: List[Tuple[Any, bytes]] = [
        (config.SHARE_MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False).encode("utf-8")),
        (config.SHARE_BANK_MD_NAME, tamper_bank if tamper_bank is not None else body),
        (f"{config.SHARE_MEDIA_DIRNAME}/", b""),
    ]
    kept = [(name, payload) for name, payload in members if str(name) not in drop]
    return make_zip(kept + list(extra_members))


def export_pack(client: TestClient) -> bytes:
    """走 T02 的导出接口拿一个真实分享包。"""
    response = client.get("/api/export/share-pack")
    assert response.status_code == 200, response.text
    return response.content


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


def post_pack(
    client: TestClient, payload: bytes, name: str = "tiku-share.zip"
) -> Any:
    """把 ZIP 字节上传到分享包导入接口。"""
    return client.post(
        "/api/imports/share-pack",
        files={"file": (name, payload, "application/zip")},
    )


def all_questions() -> List[Dict[str, Any]]:
    """库内全部题目（任何状态），按 id 升序。"""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM questions ORDER BY id ASC").fetchall()
    return [db.question_to_dict(row) for row in rows]


def count_questions() -> int:
    """库内题目总数。"""
    with db.get_conn() as conn:
        return int(conn.execute("SELECT COUNT(*) AS c FROM questions").fetchone()["c"])


def staging_files() -> List[Path]:
    """暂存目录下**所有已落盘文件**（安全项要求恒为空）。"""
    root = config.DATA_DIR / config.SHARE_STAGING_DIRNAME
    if not root.exists():
        return []
    return [path for path in root.rglob("*") if path.is_file()]


def backup_files() -> List[Path]:
    """备份目录下的备份文件。"""
    root = config.DATA_DIR / config.BACKUP_DIRNAME
    if not root.is_dir():
        return []
    return sorted(path for path in root.iterdir() if path.is_file())


# ===========================================================================
# ① 空库导入 + 立即可检索
# ===========================================================================
def test_import_pack_into_empty_library(client: TestClient) -> None:
    """T02 导出的包导入空库：题目全等、状态 published、立刻可被搜索到。"""
    import_id = seed(client)
    before = all_questions()
    assert len(before) == 4
    pack = export_pack(client)

    # 清空库，制造「空库导入」
    assert client.delete(f"/api/imports/{import_id}").status_code == 200
    assert count_questions() == 0

    response = post_pack(client, pack)
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["added"] == 4
    assert data["duplicates"] == 0
    assert data["skipped"] == 0
    assert data["total_in_pack"] == 4
    assert data["import_id"] is not None
    assert data["file_id"] is not None
    assert Path(data["backup_path"]).is_file()

    after = all_questions()
    assert len(after) == 4
    for original, imported in zip(before, after):
        assert imported["stem"] == normalize_text(original["stem"])
        assert imported["options"] == original["options"]
        assert imported["answer"] == original["answer"]
        assert imported["qtype"] == original["qtype"]
        assert imported["status"] == "published", "分享包题目必须直接 published"
        assert imported["import_file_id"] == data["file_id"]

    # 导入记录：committed + file_type=share
    detail = client.get(f"/api/imports/{data['import_id']}")
    assert detail.status_code == 200
    record = detail.json()["data"]
    assert record["import"]["status"] == "committed"
    assert record["import"]["committed_questions"] == 4
    assert len(record["files"]) == 1
    assert record["files"][0]["file_type"] == config.SHARE_FILE_TYPE == "share"
    assert record["files"][0]["total_questions"] == 4

    # 立即可检索（无需 commit）
    search = client.get("/api/search", params={"q": "脉搏短绌"})
    assert search.status_code == 200, search.text
    result = search.json()["data"]
    assert result["total"] == 1, result
    assert "<mark>" in result["items"][0]["highlight"]["stem"]

    assert staging_files() == []


def test_import_keeps_qtype_distribution(client: TestClient) -> None:
    """导入后题型分布与包内 manifest 一致（单选 2 / 多选 1 / 判断 1）。"""
    seed(client)
    pack = export_pack(client)
    response = post_pack(client, pack)
    assert response.status_code == 200, response.text
    # 全部重复 → 不新增；先清库再导入才能验证题型
    assert response.json()["data"]["added"] == 0
    assert response.json()["data"]["duplicates"] == 4

    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT qtype, COUNT(*) AS c FROM questions GROUP BY qtype"
        ).fetchall()
    assert {row["qtype"]: int(row["c"]) for row in rows} == {
        "single": 2,
        "multiple": 1,
        "judge": 1,
    }


# ===========================================================================
# ② 重复导入幂等
# ===========================================================================
def test_reimport_same_pack_is_idempotent(client: TestClient) -> None:
    """同一包导入两次：第二次 added=0 / duplicates=4，库内题数与备份数都不变。"""
    seed(client)
    pack = export_pack(client)

    first = post_pack(client, pack)
    assert first.status_code == 200, first.text
    # 库里已有 4 题（seed 的），因此第一次就是全重复
    assert first.json()["data"]["added"] == 0
    assert first.json()["data"]["duplicates"] == 4
    assert first.json()["data"]["import_id"] is None
    assert first.json()["data"]["backup_path"] is None
    assert count_questions() == 4
    assert backup_files() == [], "全部重复时不应产生备份"

    second = post_pack(client, pack)
    assert second.status_code == 200, second.text
    assert second.json()["data"]["added"] == 0
    assert second.json()["data"]["duplicates"] == 4
    assert second.json()["data"]["total_in_pack"] == 4
    assert count_questions() == 4
    assert backup_files() == []


def test_in_pack_duplicate_counted_once(client: TestClient) -> None:
    """包内自重复：同一题出现两次 → added=1、duplicates=1。"""
    twice_md = SAMPLE_MD.split("---")[0]  # 只保留第 1 题
    duplicated_md = f"{twice_md}\n---\n{twice_md}"
    response = post_pack(client, make_pack(duplicated_md))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["total_in_pack"] == 2
    assert data["added"] == 1
    assert data["duplicates"] == 1
    assert count_questions() == 1


# ===========================================================================
# ③ 路径穿越
# ===========================================================================
def test_path_traversal_member_rejected_and_nothing_written(client: TestClient) -> None:
    """``../evil.md`` → 400 且暂存目录无任何文件落盘。"""
    payload = make_pack(extra_members=[("../evil.md", b"pwned")])
    response = post_pack(client, payload)

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["code"] == 40001
    assert "路径穿越" in body["message"]
    assert body["data"] is None

    assert count_questions() == 0
    assert staging_files() == [], "被拒绝的包不允许在磁盘上留下任何文件"
    # 目标目录本身都不应被创建
    assert not (config.DATA_DIR / config.SHARE_STAGING_DIRNAME).exists()


def test_path_traversal_in_subdirectory_rejected(client: TestClient) -> None:
    """``media/../../evil.md`` 同样拒绝（不做「修正」，直接判非法）。"""
    payload = make_pack(extra_members=[("media/../../evil.md", b"pwned")])
    response = post_pack(client, payload)
    assert response.status_code == 400, response.text
    assert "路径穿越" in response.json()["message"]
    assert staging_files() == []


# ===========================================================================
# ④ 绝对路径 / 非法类型
# ===========================================================================
@pytest.mark.parametrize(
    ("member", "expected"),
    [
        ("/etc/passwd", "绝对路径"),
        ("C:\\Windows\\evil.md", "盘符"),
        ("/abs/evil.md", "绝对路径"),
    ],
)
def test_absolute_path_member_rejected(
    client: TestClient, member: str, expected: str
) -> None:
    """绝对路径 / 盘符条目 → 400，且不落盘。"""
    response = post_pack(client, make_pack(extra_members=[(member, b"pwned")]))
    assert response.status_code == 400, response.text
    assert expected in response.json()["message"]
    assert staging_files() == []


@pytest.mark.parametrize("member", ["evil.sh", "media/evil.exe", "media/payload.bat"])
def test_forbidden_extension_rejected(client: TestClient, member: str) -> None:
    """非白名单扩展名 → 400（文案列出允许的扩展名），且不落盘。"""
    response = post_pack(client, make_pack(extra_members=[(member, b"pwned")]))
    assert response.status_code == 400, response.text
    message = response.json()["message"]
    assert "不允许的文件类型" in message
    assert ".md" in message  # 文案里带白名单
    assert staging_files() == []


def test_symlink_member_rejected(client: TestClient) -> None:
    """符号链接条目 → 400，且不落盘。"""
    info = zipfile.ZipInfo(filename="media/link.md")
    info.external_attr = 0o120777 << 16  # S_IFLNK | 0777
    response = post_pack(client, make_pack(extra_members=[(info, b"/etc/passwd")]))
    assert response.status_code == 400, response.text
    assert "符号链接" in response.json()["message"]
    assert staging_files() == []


# ===========================================================================
# ⑤ 解压炸弹三档
# ===========================================================================
def test_bomb_single_member_over_20mb_rejected(client: TestClient) -> None:
    """单成员 21MB（ZIP 本身只有 ~20KB，能通过 API 的 20MB 上限）→ 拒绝且不落盘。"""
    big = b"\0" * (21 * 1024 * 1024)
    payload = make_pack(extra_members=[("media/huge.png", big)])
    assert len(payload) < config.MAX_FILE_SIZE, "样本 ZIP 必须能通过外层大小限制"

    response = post_pack(client, payload)
    assert response.status_code == 400, response.text
    assert "超过解压上限 20MB" in response.json()["message"]
    assert staging_files() == []


def test_bomb_total_size_end_to_end_rejected(client: TestClient) -> None:
    """真实 209MB 解压样本 → 400「总大小 208MB 超过上限 200MB」且不落盘。

    样本构造（QA 提供的可行配方）：每个 32000 字节窗口 = 同一个 1600 字节随机块
    重复 20 次 —— **窗口内可压缩、窗口之间互不匹配**。于是单个 19.9MB 成员的压缩比
    只有 ~17:1（远低于 100:1 阈值），11 个成员合计解压 208.8MB（超过 200MB 阈值），
    而整包 ZIP 只有 ~12MB（能通过 API 层的 20MB 上限），成员数 11（远低于 1000）。
    也就是说：单成员体积 / 压缩比 / 成员数三项全部**合法**，唯一越界的就是总量，
    正好单独验证总量这一档。
    """
    rng = random.Random(20260101)
    window_size, block_size, repeat = 32000, 1600, 20
    chunks = []
    for _ in range(622):  # 622 × 32000 = 19,904,000 字节 ≈ 18.98MB
        chunks.append(rng.randbytes(block_size) * repeat)
    member = b"".join(chunks)

    payload = make_pack(
        extra_members=[(f"media/part{index}.png", member) for index in range(11)]
    )

    # 先自证样本「三档里只有总量越界」，否则这条用例就没验证到总量
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        total = sum(info.file_size for info in infos)
        assert len(payload) < config.MAX_FILE_SIZE, f"ZIP {len(payload)} 字节超过外层上限"
        assert total > unpack.MAX_TOTAL_SIZE, total
        for info in infos:
            assert info.file_size <= unpack.MAX_MEMBER_SIZE, info.filename
            assert info.file_size / max(info.compress_size, 1) <= unpack.MAX_COMPRESSION_RATIO
        assert len(infos) <= unpack.MAX_MEMBERS

    max_ratio = max(info.file_size / max(info.compress_size, 1) for info in infos)
    print(
        f"\n[炸弹实测] 文件成员 {len(infos)} 个，总解压 {total / 1024 / 1024:.1f}MB，"
        f"ZIP {len(payload) / 1024 / 1024:.2f}MB，最大单成员压缩比 {max_ratio:.1f}:1"
    )

    response = post_pack(client, payload)
    assert response.status_code == 400, response.text
    assert "总大小 208MB 超过上限 200MB" in response.json()["message"]
    assert count_questions() == 0
    assert staging_files() == []


def test_bomb_compression_ratio_rejected(client: TestClient) -> None:
    """压缩比 >100:1（2MB 全零 → 约 2KB）→ 拒绝且不落盘。"""
    payload = make_pack(extra_members=[("media/zeros.png", b"\0" * (2 * 1024 * 1024))])
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        info = archive.getinfo("media/zeros.png")
        ratio = info.file_size / max(info.compress_size, 1)
    assert ratio > unpack.MAX_COMPRESSION_RATIO, f"样本压缩比只有 {ratio:.0f}:1"

    response = post_pack(client, payload)
    assert response.status_code == 400, response.text
    assert "疑似解压炸弹" in response.json()["message"]
    assert staging_files() == []


def test_member_file_directory_collision_returns_400(client: TestClient) -> None:
    """包内同时含 ``media/x.md/``（目录条目）与 ``media/x.md``（文件）→ 400 而不是 500。

    P2-1 回归：``_check_member`` 对目录条目提前放行（不查扩展名），写盘阶段才会撞上
    「同名目录 / 文件」，抛的是 ``PermissionError`` / ``IsADirectoryError``（``OSError``
    子类）而不是 :class:`SharePackError`。若不包装，API 层会返回 500 + 完整堆栈入日志。
    """
    directory = zipfile.ZipInfo(filename="media/x.md/")
    directory.external_attr = (0o40755 << 16) | 0x10  # S_IFDIR | MS-DOS 目录位
    payload = make_pack(
        extra_members=[(directory, b""), ("media/x.md", "# 与目录同名\n".encode("utf-8"))]
    )

    response = post_pack(client, payload)
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["code"] == 40001
    assert "写盘失败" in body["message"]
    assert count_questions() == 0
    assert staging_files() == []


def test_write_size_mismatch_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-2 回归：实写字节数 ≠ ``ZipInfo.file_size`` → 拒绝并清理。

    CPython 的 ``ZipExtFile`` 会按 ``file_size`` 截断，所以真实包走不到这条分支；
    这里替换 ``shutil.copyfileobj`` 模拟「解压器多写了字节」，验证守卫确实存在
    （而不是「代码看起来会拒绝」）。

    **注意：这条用例是「同义反复」，不能当安全证据**（QA 指出）：测试自己制造了
    唯一能触发该守卫的条件，真实世界里构造不出这种包。它的价值只是防止守卫被
    误删。真正的外部可触发面见 ``test_corrupted_crc_returns_400_without_traceback``
    （CRC 损坏）与 ``test_corrupted_compressed_stream_returns_400``（deflate 流损坏）。
    """

    def lying_copyfileobj(source: Any, sink: Any, length: int = 0) -> None:
        """正常拷贝后多写 5 字节，制造「实写 > 声明」的情形。"""
        sink.write(source.read())
        sink.write(b"extra")

    monkeypatch.setattr(unpack.shutil, "copyfileobj", lying_copyfileobj)

    dest = tmp_path / "staging"
    with pytest.raises(SharePackError) as excinfo:
        extract_share_pack(make_pack(), dest)

    assert "实际大小与元数据不符" in str(excinfo.value)
    assert not dest.exists(), "校验失败后不得留下半成品"


# ---------------------------------------------------------------------------
# P2-① 损坏的分享包：必须 400，不能 500 + 堆栈
# ---------------------------------------------------------------------------
def corrupt_central_crc(payload: bytes, member: str) -> bytes:
    """把某成员在**中央目录**里的 CRC 字段改坏。

    只改中央目录（``PK\\x01\\x02`` 条目内偏移 16 的 4 字节），**不动压缩流**，
    这样解压时压缩数据本身是合法的，只会触发 ``BadZipFile("Bad CRC-32 for file ...")``，
    精确复现「传输过程中损坏」的场景。
    """
    data = bytearray(payload)
    cursor = 0
    while True:
        index = data.find(b"PK\x01\x02", cursor)
        assert index >= 0, f"中央目录里找不到成员 {member}"
        name_length = int.from_bytes(data[index + 28 : index + 30], "little")
        name = bytes(data[index + 46 : index + 46 + name_length]).decode("utf-8")
        if name == member:
            data[index + 16 : index + 20] = b"\x00\x00\x00\x00"
            return bytes(data)
        cursor = index + 4


def corrupt_compressed_stream(payload: bytes, member: str) -> bytes:
    """改坏某成员**压缩流**的前 10 个字节（deflate 流被篡改 → ``zlib.error``）。"""
    data = bytearray(payload)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        info = archive.getinfo(member)
    header = info.header_offset
    name_length = int.from_bytes(data[header + 26 : header + 28], "little")
    extra_length = int.from_bytes(data[header + 28 : header + 30], "little")
    start = header + 30 + name_length + extra_length
    for offset in range(10):
        data[start + offset] ^= 0xFF
    return bytes(data)


def assert_rejected_without_traceback(
    client: TestClient, caplog: pytest.LogCaptureFixture, payload: bytes, expected: str
) -> None:
    """断言：400 / 40001、文案命中、暂存 0 文件、**日志里没有 traceback**。"""
    caplog.clear()
    with caplog.at_level(logging.INFO):
        response = post_pack(client, payload)

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["code"] == 40001, body
    assert expected in body["message"], body

    assert count_questions() == 0
    assert staging_files() == [], "被拒绝的包不允许在磁盘上留下任何文件"

    traces = [record for record in caplog.records if record.exc_info]
    assert traces == [], [record.getMessage() for record in traces]
    # 反向确认：拒绝原因确实被记了一条 warning（不是静默吞掉）
    assert any("分享包被拒绝" in record.getMessage() for record in caplog.records)


def test_corrupted_crc_returns_400_without_traceback(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """P2-①：CRC 校验失败（``BadZipFile``）→ 400 + 40001，**不是 500 + 堆栈**。"""
    payload = corrupt_central_crc(make_pack(), config.SHARE_BANK_MD_NAME)
    assert_rejected_without_traceback(client, caplog, payload, "分享包解压写盘失败")


def test_corrupted_compressed_stream_returns_400(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """P2-① 延伸：deflate 流被篡改（``zlib.error``）同样 400，不是 500。

    实测发现这一类**不会**被包成 ``BadZipFile``：CPython 直接把 ``zlib.error``
    抛出来，所以 ``except`` 元组里必须带上它，否则依然是 500 + 堆栈。
    """
    payload = corrupt_compressed_stream(make_pack(), config.SHARE_BANK_MD_NAME)
    assert_rejected_without_traceback(client, caplog, payload, "分享包解压写盘失败")


def test_truncated_pack_returns_400(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    """尾部被截断的包（传输中断）→ 400，且不留文件。"""
    payload = make_pack()[:-100]
    assert_rejected_without_traceback(client, caplog, payload, "不是有效的 ZIP 文件")


def test_too_many_members_rejected(client: TestClient) -> None:
    """成员数 > 1000 → 拒绝且不落盘。"""
    members = [(f"media/f{index}.md", b"x") for index in range(unpack.MAX_MEMBERS + 1)]
    response = post_pack(client, make_pack(extra_members=members))
    assert response.status_code == 400, response.text
    assert "成员数" in response.json()["message"]
    assert staging_files() == []


def test_oversized_zip_rejected_by_api_before_unpack(client: TestClient) -> None:
    """ZIP 自身 > 20MB → 在外层就被拦下（更深一层的防护），且不落盘。"""
    big = b"\0" * (21 * 1024 * 1024)
    payload = make_pack(extra_members=[("media/raw.png", big)])
    # 用 STORED 让 ZIP 体积真的超过 20MB
    payload = make_zip(
        [
            (config.SHARE_MANIFEST_NAME, b"{}"),
            (config.SHARE_BANK_MD_NAME, SAMPLE_MD.encode("utf-8")),
            ("media/raw.png", big),
        ],
        compress=zipfile.ZIP_STORED,
    )
    assert len(payload) > config.MAX_FILE_SIZE

    response = post_pack(client, payload)
    assert response.status_code == 400, response.text
    assert "超过大小上限 20MB" in response.json()["message"]
    assert staging_files() == []


# ===========================================================================
# ⑥ 备份
# ===========================================================================
def test_backup_created_before_write_and_valid(client: TestClient) -> None:
    """备份在写库之前产生：备份内题数 = 导入前题数，且 ``integrity_check == ok``。"""
    response = post_pack(client, make_pack())
    assert response.status_code == 200, response.text
    backup_path = Path(response.json()["data"]["backup_path"])

    assert backup_path.is_file()
    assert backup_path.parent == config.DATA_DIR / config.BACKUP_DIRNAME
    assert backup_path.name.startswith("pre-import-")
    assert backup_path.suffix == ".db"
    assert backup_files() == [backup_path]

    backup_conn = sqlite3.connect(str(backup_path))
    try:
        assert backup_conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        rows = backup_conn.execute("SELECT COUNT(*) FROM questions").fetchone()
    finally:
        backup_conn.close()
    assert int(rows[0]) == 0, "备份必须是导入前的状态（0 题）"
    assert count_questions() == 4, "导入后主库应有 4 题"


def test_backup_failure_aborts_import(client: TestClient) -> None:
    """备份目录不可用 → 500，导入整体中止，库内数据不变。"""
    backup_root = config.DATA_DIR / config.BACKUP_DIRNAME
    backup_root.parent.mkdir(parents=True, exist_ok=True)
    backup_root.write_text("我不是目录", encoding="utf-8")  # 占位成同名文件

    response = post_pack(client, make_pack())
    assert response.status_code == 500, response.text
    body = response.json()
    assert body["code"] == 50000
    assert "备份失败" in body["message"]
    assert "库内数据未改变" in body["message"]

    assert count_questions() == 0
    with db.get_conn() as conn:
        assert int(conn.execute("SELECT COUNT(*) AS c FROM imports").fetchone()["c"]) == 0
        assert int(conn.execute("SELECT COUNT(*) AS c FROM import_files").fetchone()["c"]) == 0
    assert staging_files() == []


# ===========================================================================
# ⑦ 同 question_no 不同内容
# ===========================================================================
def test_same_question_no_different_content_both_imported(client: TestClient) -> None:
    """同 ``question_no`` 但内容不同 → 两条都入库（``question_no`` 不参与查重）。

    走真实链路：先普通导入「旧库（单题）」并 commit（题号 = 1），再导入分享包
    （包内第 1 题题号也是 1）——这正是跨库分享的真实形态（两个题库各自从 1 编号）。
    """
    seed(client, text=SINGLE_Q_MD, name="旧库.md")
    with db.get_conn() as conn:
        existing = conn.execute(
            "SELECT question_no, stem FROM questions ORDER BY id"
        ).fetchall()
    assert [(row["question_no"]) for row in existing] == ["1"]

    response = post_pack(client, make_pack())
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["added"] == 4, "编号相同但内容不同 → 不算重复"
    assert data["duplicates"] == 0

    questions = all_questions()
    same_no = [question for question in questions if question["question_no"] == "1"]
    assert len(same_no) == 2, [question["question_no"] for question in questions]
    assert len({question["stem"] for question in same_no}) == 2
    assert same_no[0]["stem"] != normalize_text(same_no[1]["stem"])


def test_analysis_change_is_not_a_duplicate(client: TestClient) -> None:
    """只改解析不算新题（analysis 不进指纹）：答案/题干相同 → 判定为重复。"""
    seed(client)
    base = SAMPLE_MD.split("---")[0]
    edited = base.replace("脉搏短绌多见于心房颤动。", "解析被改写了，但题目本身没变。")
    response = post_pack(client, make_pack(edited))
    assert response.status_code == 200, response.text
    assert response.json()["data"]["added"] == 0
    assert response.json()["data"]["duplicates"] == 1


# ===========================================================================
# 包结构 / 完整性校验
# ===========================================================================
def test_missing_members_rejected(client: TestClient) -> None:
    """缺 manifest.json / bank.md → 400，且不落盘。"""
    no_manifest = post_pack(client, make_pack(drop=[config.SHARE_MANIFEST_NAME]))
    assert no_manifest.status_code == 400, no_manifest.text
    assert f"缺少 {config.SHARE_MANIFEST_NAME}" in no_manifest.json()["message"]

    no_bank = post_pack(client, make_pack(drop=[config.SHARE_BANK_MD_NAME]))
    assert no_bank.status_code == 400, no_bank.text
    assert f"缺少 {config.SHARE_BANK_MD_NAME}" in no_bank.json()["message"]

    assert staging_files() == []


def test_tampered_bank_rejected(client: TestClient) -> None:
    """``bank.md`` 正文与 ``bank_sha256`` 不符 → 400，且已写出的暂存目录被清理。

    注意不能靠改 ``bank_sha256`` 字段来造这个场景：改任何字段都会先触发
    ``manifest_sha256`` 自校验。这里替换正文而 manifest 保持原样，才会走到
    ``verify_bank_bytes``。
    """
    payload = make_pack(tamper_bank="# 被替换的正文\n\n没有题目。\n".encode("utf-8"))
    response = post_pack(client, payload)
    assert response.status_code == 400, response.text
    assert "bank.md 校验失败" in response.json()["message"]
    assert count_questions() == 0
    assert staging_files() == []


def test_tampered_manifest_rejected(client: TestClient) -> None:
    """``manifest_sha256`` 自校验失败 → 400。"""
    payload = make_pack(tamper_manifest={"question_count": 999})
    response = post_pack(client, payload)
    assert response.status_code == 400, response.text
    assert "manifest.json 校验失败" in response.json()["message"]
    assert staging_files() == []


def test_unsupported_manifest_version_rejected(client: TestClient) -> None:
    """未来版本（schema_version=99）→ 400 且提示升级。"""
    payload = make_pack(tamper_manifest={"schema_version": 99})
    response = post_pack(client, payload)
    assert response.status_code == 400, response.text
    assert "不受支持" in response.json()["message"]
    assert staging_files() == []


@pytest.mark.parametrize(
    ("payload", "name", "expected"),
    [
        (b"not a zip at all", "fake.zip", "不是有效的 ZIP"),
        (b"", "empty.zip", "文件内容为空"),
    ],
)
def test_invalid_upload_rejected(
    client: TestClient, payload: bytes, name: str, expected: str
) -> None:
    """非 ZIP / 空文件 → 400。"""
    response = post_pack(client, payload, name=name)
    assert response.status_code == 400, response.text
    assert expected in response.json()["message"]
    assert staging_files() == []


def test_non_zip_filename_rejected(client: TestClient) -> None:
    """扩展名不是 .zip → 400。"""
    response = post_pack(client, make_pack(), name="pack.tar.gz")
    assert response.status_code == 400, response.text
    assert "必须是 .zip 文件" in response.json()["message"]


def test_missing_form_field_rejected(client: TestClient) -> None:
    """表单里没有文件字段 → 400。"""
    response = client.post("/api/imports/share-pack", data={"foo": "bar"})
    assert response.status_code == 400, response.text
    assert "未接收到分享包文件" in response.json()["message"]


# ===========================================================================
# 直接调用安全边界（不经过 HTTP，验证 dest 目录的处理）
# ===========================================================================
def test_extract_share_pack_creates_no_dir_on_rejection(tmp_path: Path) -> None:
    """``extract_share_pack`` 被拒绝时，目标目录**完全不被创建**。"""
    dest = tmp_path / "staging"
    with pytest.raises(SharePackError):
        extract_share_pack(make_pack(extra_members=[("../evil.md", b"x")]), dest)
    assert not dest.exists()

    with pytest.raises(SharePackError):
        extract_share_pack(b"broken", dest)
    assert not dest.exists()


def test_extract_share_pack_removes_partial_output_on_bad_manifest(tmp_path: Path) -> None:
    """阶段 3 校验失败 → 已写出的文件被整体清理（不留半成品）。"""
    dest = tmp_path / "staging"
    with pytest.raises(SharePackError):
        extract_share_pack(make_pack(tamper_manifest={"bank_sha256": "0" * 64}), dest)
    assert not dest.exists()


def test_extract_share_pack_happy_path(tmp_path: Path) -> None:
    """正常包解压后返回 manifest / bank_path / 成员列表。"""
    dest = tmp_path / "staging"
    result = extract_share_pack(make_pack(), dest)

    assert result["manifest"]["question_count"] == 4
    assert result["bank_path"] == dest.resolve() / config.SHARE_BANK_MD_NAME
    assert result["bank_path"].is_file()
    assert (dest.resolve() / config.SHARE_MEDIA_DIRNAME).is_dir()
    assert config.SHARE_MANIFEST_NAME in result["members"]


# ===========================================================================
# 与既有导入链路共存
# ===========================================================================
def test_share_import_does_not_break_normal_import(client: TestClient) -> None:
    """分享包导入后，普通上传 → commit 链路仍正常（pending → published）。"""
    assert post_pack(client, make_pack()).status_code == 200
    assert count_questions() == 4

    import_id = seed(client, name="追加.md")
    assert count_questions() == 8
    assert client.post(f"/api/imports/{import_id}/commit").status_code == 200

    search = client.get("/api/search", params={"q": "压疮"})
    # 分享包里的第 4 题与新上传的同名题都含「压疮」→ 2 条
    assert search.json()["data"]["total"] == 2


def test_share_import_questions_survive_pack_reimport_after_delete(
    client: TestClient,
) -> None:
    """删除分享包批次后，题目一并清理（FTS 同步），再次导入仍成功。"""
    pack = make_pack()
    first = post_pack(client, pack)
    assert first.status_code == 200
    import_id = first.json()["data"]["import_id"]

    assert client.delete(f"/api/imports/{import_id}").status_code == 200
    assert count_questions() == 0
    assert client.get("/api/search", params={"q": "脉搏短绌"}).json()["data"]["total"] == 0

    again = post_pack(client, pack)
    assert again.status_code == 200, again.text
    assert again.json()["data"]["added"] == 4


def test_export_after_import_roundtrip(client: TestClient) -> None:
    """导入后的题再导出，manifest 题目数与题型分布与导入一致（闭环）。"""
    response = post_pack(client, make_pack())
    assert response.status_code == 200

    pack = export_pack(client)
    with zipfile.ZipFile(io.BytesIO(pack)) as archive:
        manifest = json.loads(archive.read(config.SHARE_MANIFEST_NAME).decode("utf-8"))
    assert manifest["question_count"] == 4
    assert manifest["qtype_distribution"] == {"判断题": 1, "单选题": 2, "多选题": 1}
