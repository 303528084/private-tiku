"""文本规范化：全角→半角、去首尾空白、压缩连续空白、统一标点。

说明：U+FF01–U+FF5E 区间（全角 ASCII）统一偏移 -0xFEE0 转为半角，
因此 ``Ａ`` → ``A``、``．`` → ``.``、``：`` → ``:``、``（`` → ``(`` 都会被一次处理。
中文句号 ``。`` 保留原样（不转成 ``.``，避免题干被误认为题号行），
选项/答案/解析的正则字符类中同时容纳了全角与半角标点。

v1.3 起只保留单行规范化 ``normalize_text()``：整文件行序列的 ``normalize(lines)``
随文本类读取层一并下线（Markdown 解析器逐行调用本函数）。
"""

from __future__ import annotations

import re

_FULLWIDTH_START = 0xFF01
_FULLWIDTH_END = 0xFF5E
_FULLWIDTH_OFFSET = 0xFEE0

_EXTRA_MAP: dict[str, str] = {
    "\u3000": " ",  # 全角空格
    "\u00a0": " ",  # 不换行空格
    "\u2002": " ",
    "\u2003": " ",
    "\u2007": " ",
    "\u202f": " ",
    "\u200b": "",  # 零宽空格
    "\ufeff": "",  # BOM
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u3010": "[",
    "\u3011": "]",
}

_WHITESPACE_RE = re.compile(r"[ \t]{2,}")


def _convert_char(char: str) -> str:
    """单个字符的全角→半角转换。"""
    code = ord(char)
    if _FULLWIDTH_START <= code <= _FULLWIDTH_END:
        return chr(code - _FULLWIDTH_OFFSET)
    mapped = _EXTRA_MAP.get(char)
    if mapped is not None:
        return mapped
    return char


def normalize_text(text: str) -> str:
    """规范化单行文本（幂等）。"""
    if not text:
        return ""
    converted = "".join(_convert_char(char) for char in text)
    collapsed = _WHITESPACE_RE.sub(" ", converted)
    return collapsed.strip()
