"""分享包安全解压：成员名校验、解压炸弹防护、完整性校验。

**本模块是 T03 的安全边界**，规则（顺序即执行顺序）：

1. **先校验、后落盘**：全部成员先用 ``ZipInfo`` 元数据（``filename`` /
   ``file_size`` / ``compress_size`` / ``external_attr``）校验完，全部通过才创建
   目标目录并写第一个字节。因此任何被拒绝的包**在磁盘上不留任何文件**。
2. 成员名拒绝：绝对路径（``/x``、``\\\\server\\x``）、Windows 盘符（``C:x``）、
   任何 ``..`` 路径段、空名。允许子目录（如 ``media/a.png``），但每段都必须是
   普通名字。
3. 成员类型拒绝：符号链接（``S_IFLNK``）；目录条目放行。
4. 扩展名白名单：``config.SHARE_ALLOWED_EXT``（``.json .md .markdown`` 与常见图片）。
5. 解压炸弹：单成员 > 20MB、总解压 > 200MB、单成员压缩比 > 100:1
   （仅对 ≥1MB 的成员判比例，避免误伤高重复的小文本）、成员数 > 1000。
6. 写入时**双保险**：``resolve()`` 后必须仍在目标目录内（防符号链接父目录 /
   平台差异导致的越界）。
7. 写盘后核对**实写字节数 == ``ZipInfo.file_size``**：体积限制的前提是「声明的
   大小就是实际写出的大小」，而这一条此前依赖 CPython ``ZipExtFile._left`` 的截断
   行为（未文档化的实现细节）。显式核对把它变成代码自己保证的事实（P2-2）。
8. 写盘/读流阶段的 ``OSError``、``zipfile.BadZipFile``（含 CRC 校验失败）、
   ``EOFError``（流被截断）、``zlib.error``（deflate 流被篡改）统一包成
   :class:`SharePackError`，避免冒到 API 层变成 500 + 堆栈入日志（P2-1 / P2-①）。
9. 写完后校验 ``manifest.json``：存在、``schema_version`` 受支持、自摘要匹配、
   ``bank.md`` 的 ``bank_sha256`` 匹配。

失败时抛 :class:`SharePackError`，并把已创建的暂存目录整体删除。
"""

from __future__ import annotations

import io
import json
import re
import shutil
import zipfile
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import config
from .manifest import ManifestError, verify_bank_bytes, verify_manifest

MAX_MEMBER_SIZE: int = config.SHARE_MAX_MEMBER_SIZE
MAX_TOTAL_SIZE: int = config.SHARE_MAX_TOTAL_SIZE
MAX_COMPRESSION_RATIO: int = config.SHARE_MAX_COMPRESSION_RATIO
RATIO_CHECK_MIN_SIZE: int = config.SHARE_RATIO_CHECK_MIN_SIZE
MAX_MEMBERS: int = config.SHARE_MAX_MEMBERS

_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_S_IFMT = 0o170000
_S_IFLNK = 0o120000


class SharePackError(ValueError):
    """分享包不合法（路径越界 / 类型不允许 / 炸弹 / manifest 校验失败）。"""


# ---------------------------------------------------------------------------
# 成员名校验
# ---------------------------------------------------------------------------
def validate_member_name(name: str) -> str:
    """校验并归一化 ZIP 成员名；不合法抛 :class:`SharePackError`。

    归一化只做两件事：``\\`` → ``/``（Windows 打出来的包），丢弃空段与 ``.`` 段。
    **不做**任何「修正」——``..`` 一律拒绝而不是解析掉，避免与平台解析差异博弈。

    Args:
        name: ``ZipInfo.filename``。

    Returns:
        归一化后的相对路径（如 ``media/a.png``、``media/``）。

    Raises:
        SharePackError: 空名、绝对路径、盘符、含 ``..``。
    """
    raw = (name or "").strip()
    if not raw:
        raise SharePackError("分享包含有空文件名")
    normalized = raw.replace("\\", "/")
    if normalized.startswith("/"):
        raise SharePackError(f"分享包含有绝对路径条目，已拒绝：{raw}")
    if _DRIVE_RE.match(normalized):
        raise SharePackError(f"分享包含有带盘符的条目，已拒绝：{raw}")
    segments = [segment for segment in normalized.split("/") if segment not in ("", ".")]
    if not segments:
        raise SharePackError(f"分享包含有空路径条目：{raw}")
    if any(segment == ".." for segment in segments):
        raise SharePackError(f"分享包含有路径穿越条目（..），已拒绝：{raw}")
    return normalized


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    """ZIP 条目是否为符号链接（Unix 模式位 ``S_IFLNK``）。"""
    return (info.external_attr >> 16) & _S_IFMT == _S_IFLNK


def _suffix(name: str) -> str:
    """取扩展名（小写）；目录条目返回空串。"""
    if name.endswith("/"):
        return ""
    return Path(name).suffix.lower()


def _check_member(info: zipfile.ZipInfo) -> None:
    """校验单个成员的名称、类型、扩展名与体积（**不写盘**）。

    Raises:
        SharePackError: 任一规则不满足。
    """
    name = validate_member_name(info.filename)
    if info.is_dir():
        return
    if _is_symlink(info):
        raise SharePackError(f"分享包含有符号链接条目，已拒绝：{info.filename}")
    suffix = _suffix(name)
    if suffix not in config.SHARE_ALLOWED_EXT:
        allowed = "、".join(sorted(config.SHARE_ALLOWED_EXT))
        raise SharePackError(
            f"分享包含有不允许的文件类型「{suffix or '无扩展名'}」，仅接受 {allowed}：{info.filename}"
        )
    if info.file_size > MAX_MEMBER_SIZE:
        raise SharePackError(
            f"分享包内文件超过解压上限 {MAX_MEMBER_SIZE // (1024 * 1024)}MB："
            f"{info.filename}（{info.file_size} 字节）"
        )
    if info.file_size >= RATIO_CHECK_MIN_SIZE:
        ratio = info.file_size / max(info.compress_size, 1)
        if ratio > MAX_COMPRESSION_RATIO:
            raise SharePackError(
                f"分享包疑似解压炸弹（压缩比 {ratio:.0f}:1 超过 {MAX_COMPRESSION_RATIO}:1）："
                f"{info.filename}"
            )


def _check_total(infolist: List[zipfile.ZipInfo]) -> None:
    """校验成员数量与总解压体积。

    Raises:
        SharePackError: 成员过多或总解压体积超限。
    """
    if len(infolist) > MAX_MEMBERS:
        raise SharePackError(
            f"分享包成员数 {len(infolist)} 超过上限 {MAX_MEMBERS}"
        )
    total = sum(info.file_size for info in infolist if not info.is_dir())
    if total > MAX_TOTAL_SIZE:
        raise SharePackError(
            f"分享包解压后总大小 {total // (1024 * 1024)}MB 超过上限 "
            f"{MAX_TOTAL_SIZE // (1024 * 1024)}MB"
        )


# ---------------------------------------------------------------------------
# 解压
# ---------------------------------------------------------------------------
def extract_share_pack(data: bytes, dest: Path) -> Dict[str, Any]:
    """把分享包安全解压到 ``dest``，并返回 manifest 与关键路径。

    Args:
        data: ZIP 字节流（已由调用方限制总大小）。
        dest: 目标目录（不存在会被创建；**只在全部成员校验通过后才创建**）。

    Returns:
        ``{"manifest": dict, "bank_path": Path, "members": [相对路径, ...],
        "root": Path}``。

    Raises:
        SharePackError: ZIP 损坏、成员不合法、解压炸弹或 manifest 校验失败。
            抛出时 ``dest`` 已被清理（不会留下半成品文件）。
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SharePackError(f"不是有效的 ZIP 文件：{exc}") from exc

    with archive:
        infos = archive.infolist()
        if not infos:
            raise SharePackError("分享包是空的")

        # ---- 阶段 1：全部校验（不写盘）----
        _check_total(infos)
        for info in infos:
            _check_member(info)
        names = [validate_member_name(info.filename) for info in infos]
        if config.SHARE_MANIFEST_NAME not in names:
            raise SharePackError(f"分享包缺少 {config.SHARE_MANIFEST_NAME}")
        if config.SHARE_BANK_MD_NAME not in names:
            raise SharePackError(f"分享包缺少 {config.SHARE_BANK_MD_NAME}")

        # ---- 阶段 2：写盘（逐条再次做 resolve 校验）----
        root = Path(dest)
        try:
            root.mkdir(parents=True, exist_ok=True)
            resolved_root = root.resolve()
            for info in infos:
                name = validate_member_name(info.filename)
                target = resolved_root / name
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                resolved_target = target.resolve()
                if resolved_target != resolved_root and resolved_root not in resolved_target.parents:
                    raise SharePackError(
                        f"分享包条目解析后越出目标目录，已拒绝：{info.filename}"
                    )
                with archive.open(info) as source, open(resolved_target, "wb") as sink:
                    shutil.copyfileobj(source, sink)
                    # 体积限制的前提是「写出的字节 == ZipInfo 声明的 file_size」。
                    # 这一条目前由 CPython ``ZipExtFile._left`` 的截断行为保证，但那是
                    # 未文档化的实现细节；这里显式断言，把安全性变成代码自己保证的事实
                    # （P2-2）。不匹配说明包被伪造，按非法包处理。
                    written = sink.tell()
                if written != info.file_size:
                    raise SharePackError(
                        f"分享包条目实际大小与元数据不符（声明 {info.file_size} 字节，"
                        f"写出 {written} 字节）：{info.filename}"
                    )

            # ---- 阶段 3：manifest 与正文完整性 ----
            manifest = _load_manifest(resolved_root)
            bank_path = resolved_root / config.SHARE_BANK_MD_NAME
            try:
                verify_bank_bytes(manifest, bank_path.read_bytes())
            except ManifestError as exc:
                raise SharePackError(str(exc)) from exc
        except (OSError, zipfile.BadZipFile, EOFError, zlib.error) as exc:
            # 写盘/读流阶段的「环境问题 + 数据损坏」必须统一包成 SharePackError，
            # 否则会冒到 API 层变成 500 + 完整堆栈入日志（P2-1 / P2-①）：
            # * ``OSError``：目录/文件同名冲突（PermissionError / IsADirectoryError）、
            #   磁盘满、路径过长；
            # * ``zipfile.BadZipFile``：**CRC 校验失败**（``Bad CRC-32 for file ...``）、
            #   加密条目、结构损坏 —— 它继承 ``Exception`` 而**不是** ``OSError``，
            #   这正是 P2-① 漏掉的那一类；
            # * ``EOFError``：压缩流被截断；
            # * ``zlib.error``：deflate 流被篡改（``Error -3 while decompressing``），
            #   实测同样不会变成 BadZipFile。
            # 保留原始异常链便于排查（日志里只有 warning，不带 traceback）。
            shutil.rmtree(root, ignore_errors=True)
            raise SharePackError(f"分享包解压写盘失败（数据损坏或不完整）：{exc}") from exc
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            raise

    return {
        "manifest": manifest,
        "bank_path": bank_path,
        "members": [name for name in names],
        "root": root,
    }


def _load_manifest(root: Path) -> Dict[str, Any]:
    """读取并校验 ``manifest.json``。

    Raises:
        SharePackError: 文件缺失、JSON 非法、版本不支持或摘要不匹配。
    """
    manifest_path = root / config.SHARE_MANIFEST_NAME
    try:
        raw = manifest_path.read_bytes()
    except OSError as exc:
        raise SharePackError(f"读取 {config.SHARE_MANIFEST_NAME} 失败：{exc}") from exc
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SharePackError(f"{config.SHARE_MANIFEST_NAME} 不是合法 JSON：{exc}") from exc
    try:
        verify_manifest(manifest)
    except ManifestError as exc:
        raise SharePackError(str(exc)) from exc
    return manifest


def cleanup(path: Optional[Path]) -> None:
    """删除暂存目录（尽力而为）。"""
    if path is None:
        return
    shutil.rmtree(Path(path), ignore_errors=True)


__all__ = [
    "MAX_COMPRESSION_RATIO",
    "MAX_MEMBER_SIZE",
    "MAX_MEMBERS",
    "MAX_TOTAL_SIZE",
    "SharePackError",
    "cleanup",
    "extract_share_pack",
    "validate_member_name",
]
