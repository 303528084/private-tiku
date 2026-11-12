"""字节解码层：把上传文件的原始字节解码为文本。

v1.3 起文本类读取（DOCX/TXT/PDF）已下线，本模块只保留两件事：

* :class:`ReaderError`：读取/解码失败的统一异常类型（API 层映射为 50001）；
* :func:`decode_bytes`：编码探测 + 多编码兜底，供 JSON 与 Markdown 解析器共用。
"""

from __future__ import annotations

from typing import List

import chardet

# 优先尝试的编码顺序：探测结果 → utf-8-sig → utf-8 → gb18030（覆盖 GBK/GB2312）
_FALLBACK_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1")


class ReaderError(Exception):
    """文件读取/解码失败（对应 API 错误码 50001）。"""


def decode_bytes(raw: bytes) -> tuple[str, str]:
    """探测并解码字节流，返回 ``(文本, 使用的编码)``。

    先按 chardet 的置信度（≥0.6）采用其探测结果，再依次尝试
    utf-8-sig（顺带剥掉 BOM）/ utf-8 / gb18030 / big5 / latin-1；
    全部失败时以 ``utf-8(replace)`` 兜底，保证调用方永远拿到字符串。

    Args:
        raw: 文件原始字节。

    Returns:
        二元组：解码后的文本与真正生效的编码名。
    """
    if not raw:
        return "", "utf-8"

    candidates: List[str] = []
    detected = chardet.detect(raw[:65536]) or {}
    encoding = detected.get("encoding")
    confidence = float(detected.get("confidence") or 0.0)
    if encoding and confidence >= 0.6:
        candidates.append(encoding)
    candidates.extend(_FALLBACK_ENCODINGS)

    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            return raw.decode(candidate), candidate
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"
