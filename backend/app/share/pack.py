"""分享包打包：题库 → ZIP 字节流。

ZIP 结构（T02 契约）::

    manifest.json     # 契约与完整性摘要（见 share/manifest.py）
    bank.md           # 题库正文（与 /api/export?format=md 同一渲染实现）
    media/            # 媒体目录（本版为空目录条目，为 v1.5 图片题预留）

所有成员名在写入前都过一遍 :func:`app.share.unpack.validate_member_name`：
不得以 ``/`` 开头、不得含盘符、不得含 ``..``。本模块**不 import** ``app.api.*``。
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any, Dict, List

from .. import config
from .manifest import build_manifest
from .markdown_render import render_markdown
from .unpack import validate_member_name

# 固定时间戳：同一份题目在任意机器上打出的 ZIP 头部一致，便于比对/复现
# （ZIP 规范要求 DOS 时间，1980-01-01 00:00:00 是最小合法值）。
_FIXED_DATE_TIME: tuple[int, int, int, int, int, int] = (1980, 1, 1, 0, 0, 0)

MEDIA_DIR_MODE: int = 0o40755
"""目录条目的 Unix 模式（``S_IFDIR | 0o755``）。"""


def _write_member(archive: zipfile.ZipFile, name: str, payload: bytes) -> None:
    """写入一个普通文件成员（名字先过安全检查）。"""
    safe_name = validate_member_name(name)
    info = zipfile.ZipInfo(filename=safe_name, date_time=_FIXED_DATE_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16  # 普通文件 rw-r--r--
    archive.writestr(info, payload)


def _write_directory(archive: zipfile.ZipFile, name: str) -> None:
    """写入一个**显式目录条目**（``media/``）。

    只靠文件路径隐式建目录时，空目录不会出现在 ZIP 里；这里用 ``ZipInfo``
    显式写一条以 ``/`` 结尾的条目，``namelist()`` 才能看到 ``media/``。
    """
    safe_name = validate_member_name(name)
    if not safe_name.endswith("/"):
        safe_name = f"{safe_name}/"
    info = zipfile.ZipInfo(filename=safe_name, date_time=_FIXED_DATE_TIME)
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = (MEDIA_DIR_MODE << 16) | 0x10  # 0x10 = MS-DOS 目录位
    archive.writestr(info, b"")


def build_share_pack(questions: List[Dict[str, Any]], bank_name: str) -> bytes:
    """把题目列表打成分享包 ZIP。

    Args:
        questions: ``db.question_to_dict`` 产出的题目列表（**非空**，由调用方校验）。
        bank_name: 题库名称（只进 manifest，不进文件名——避免中文/空格污染
            ``Content-Disposition``）。

    Returns:
        ZIP 字节流（``ZIP_DEFLATED``）。
    """
    bank_md = render_markdown(questions).encode("utf-8")
    manifest = build_manifest(questions, bank_name, bank_md=bank_md)
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        _write_member(archive, config.SHARE_MANIFEST_NAME, manifest_bytes)
        _write_member(archive, config.SHARE_BANK_MD_NAME, bank_md)
        _write_directory(archive, config.SHARE_MEDIA_DIRNAME)
    return buffer.getvalue()


__all__ = ["MEDIA_DIR_MODE", "build_share_pack"]
