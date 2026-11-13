"""分享包（share pack）：导出与导入的共用库。

* :mod:`app.share.manifest` —— manifest 结构与完整性校验（T02 冻结契约）
* :mod:`app.share.pack` —— 打包成 ZIP（T02）
* :mod:`app.share.unpack` —— 安全解压 + 完整性校验（T03）
* :mod:`app.share.dedup` —— 题目内容指纹与查重（T03）
* :mod:`app.share.markdown_render` —— 题库正文渲染（与 ``api/export.py`` 共用）

分层约定：本包**不 import** ``app.api.*``（避免循环依赖），只依赖
``app.config`` / ``app.db`` / ``app.parser``；API 层负责把
:class:`app.share.unpack.SharePackError` 翻译成统一响应外壳。
"""

from __future__ import annotations

from .manifest import MANIFEST_SCHEMA_VERSION, build_manifest, manifest_sha256
from .pack import build_share_pack
from .unpack import SharePackError, extract_share_pack

__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "SharePackError",
    "build_manifest",
    "build_share_pack",
    "extract_share_pack",
    "manifest_sha256",
]
