"""分享包 manifest：契约定义、构造与完整性校验。

**契约冻结（T02）**——``manifest.json`` 的字段：

============================  ==========================================
``schema_version``            manifest 契约版本（**与数据库的
                              ``migrate.SCHEMA_VERSION`` 解耦**，两者
                              演进节奏不同：DB 版本变了不代表分享包格式变）
``bank_name``                 题库名（中文原样，不放文件名）
``question_count``            题目数
``qtype_distribution``        题型分布，键为中文标签（``{"单选题": 12}``）
``generated_at``              ISO8601 带时区
``app_version``               生成该包的应用版本
``bank_sha256``               正文 ``bank.md`` 字节的 SHA-256（T03 校验内容完整）
``manifest_sha256``           本 manifest 自身的 SHA-256（除本字段外全部字段
                              的规范化 JSON），T03 用它检测 manifest 被篡改
============================  ==========================================

前六个字段是契约必需项；后两个是完整性字段，缺失或对不上时 T03 拒绝导入。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from .. import config
from .markdown_render import QTYPE_LABEL, render_markdown

MANIFEST_SCHEMA_VERSION: int = 1
"""当前代码写出的 manifest 契约版本。"""

SUPPORTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})
"""本应用能导入的 manifest 版本集合（新版本向下兼容时在此追加）。"""

HASH_FIELD: str = "manifest_sha256"
"""自校验字段名（计算自身摘要时必须排除它）。"""

BANK_HASH_FIELD: str = "bank_sha256"
"""正文字节摘要字段名。"""


class ManifestError(ValueError):
    """manifest 不合法（缺字段 / 版本不支持 / 摘要不匹配）。"""


def manifest_sha256(payload: bytes) -> str:
    """计算任意字节串的 SHA-256 十六进制摘要（供 T03 校验完整性）。

    Args:
        payload: 待摘要的字节串（``bank.md`` 内容，或规范化后的 manifest JSON）。

    Returns:
        64 位小写十六进制字符串。
    """
    return hashlib.sha256(payload).hexdigest()


def canonical_json(payload: Dict[str, Any]) -> bytes:
    """把 dict 序列化为**规范化** JSON 字节（排序键、无多余空白、保留中文）。

    规范化是自校验能成立的前提：同一份 manifest 在不同机器上必须得到同一串字节。
    """
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def qtype_distribution(questions: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    """统计题型分布，键为中文标签（与导出正文的 ``[单选题]`` 标记同一套标签）。

    Args:
        questions: 题目 dict 列表（每项至少含 ``qtype``）。

    Returns:
        形如 ``{"单选题": 12, "多选题": 3}``；按标签名排序，便于稳定比对。
    """
    counts: Dict[str, int] = {}
    for question in questions:
        qtype = str(question.get("qtype") or "unknown")
        label = QTYPE_LABEL.get(qtype, qtype)
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items()))


def build_manifest(
    questions: List[Dict[str, Any]],
    bank_name: str,
    *,
    bank_md: Optional[bytes] = None,
) -> Dict[str, Any]:
    """构造 manifest。

    Args:
        questions: 题目 dict 列表（``db.question_to_dict`` 的输出）。
        bank_name: 题库名称（中文原样，仅出现在 manifest 内）。
        bank_md: 已渲染的正文字节；省略时内部渲染（打包时传入可避免重复渲染）。

    Returns:
        含契约六字段 + 两个摘要字段的 dict。
    """
    body = bank_md if bank_md is not None else render_markdown(questions).encode("utf-8")
    payload: Dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "bank_name": str(bank_name or "").strip() or "未命名题库",
        "question_count": len(questions),
        "qtype_distribution": qtype_distribution(questions),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "app_version": config.APP_VERSION,
        BANK_HASH_FIELD: manifest_sha256(body),
    }
    payload[HASH_FIELD] = manifest_sha256(canonical_json(payload))
    return payload


def verify_manifest(manifest: Dict[str, Any]) -> None:
    """校验 manifest 的契约版本与自校验摘要。

    Args:
        manifest: ``json.loads`` 之后的 manifest。

    Raises:
        ManifestError: 缺字段、版本不支持或 ``manifest_sha256`` 不匹配。
    """
    if not isinstance(manifest, dict):
        raise ManifestError("manifest.json 结构非法（不是 JSON 对象）")

    version = manifest.get("schema_version")
    if not isinstance(version, int):
        raise ManifestError("manifest.json 缺少 schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        supported = "、".join(str(item) for item in sorted(SUPPORTED_SCHEMA_VERSIONS))
        raise ManifestError(
            f"分享包格式版本 {version} 不受支持（本应用支持：{supported}），请升级应用后重试"
        )

    for field in ("bank_name", "question_count", "qtype_distribution", "generated_at", "app_version"):
        if field not in manifest:
            raise ManifestError(f"manifest.json 缺少字段 {field}")

    expected = manifest.get(HASH_FIELD)
    if not expected:
        raise ManifestError(f"manifest.json 缺少 {HASH_FIELD}，无法校验完整性")
    body = {key: value for key, value in manifest.items() if key != HASH_FIELD}
    actual = manifest_sha256(canonical_json(body))
    if actual != expected:
        raise ManifestError(
            "manifest.json 校验失败（内容与摘要不一致，可能被篡改或损坏）"
        )


def verify_bank_bytes(manifest: Dict[str, Any], bank_bytes: bytes) -> None:
    """校验正文 ``bank.md`` 的字节摘要。

    Args:
        manifest: 已通过 :func:`verify_manifest` 的 manifest。
        bank_bytes: ``bank.md`` 的实际字节。

    Raises:
        ManifestError: 缺少 ``bank_sha256`` 或与实际内容不符。
    """
    expected = manifest.get(BANK_HASH_FIELD)
    if not expected:
        raise ManifestError(f"manifest.json 缺少 {BANK_HASH_FIELD}，无法校验题库正文")
    actual = manifest_sha256(bank_bytes)
    if actual != expected:
        raise ManifestError("bank.md 校验失败（内容与 manifest 记录不一致）")


__all__ = [
    "BANK_HASH_FIELD",
    "HASH_FIELD",
    "MANIFEST_SCHEMA_VERSION",
    "ManifestError",
    "SUPPORTED_SCHEMA_VERSIONS",
    "build_manifest",
    "canonical_json",
    "manifest_sha256",
    "qtype_distribution",
    "verify_bank_bytes",
    "verify_manifest",
]
