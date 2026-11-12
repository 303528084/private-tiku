"""文案一致性守卫：后端常量 ↔ 前端 ``src/constants/import.ts`` 字面量。

为什么要这个用例：PRD §v1.3.7 要求「旧格式报错文案前端预拦截与后端兜底必须字字
一致」。文案同时存在于两处（后端 ``config.UNSUPPORTED_EXT_TEMPLATE``、前端
``constants/import.ts::unsupportedExtMessage()``），任何单边修改都会导致用户看到
两种说法，因此用本用例把两处钉在一起 —— **改文案必须同时改两处并跑本用例**。

前端文件本期（T1–T5）尚未落地，文件不存在时跳过而不是失败。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

import pytest

from app import config

FRONTEND_CONSTANTS: Path = (
    Path(__file__).resolve().parents[3] / "frontend" / "src" / "constants" / "import.ts"
)
"""前端文案常量文件（由前端任务 T6 落地）。"""

_STRING_LITERAL_RE = re.compile(
    r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`(?:[^`\\]|\\.)*`', re.S
)
_ACCEPT_EXT_RE = re.compile(r"ACCEPT_EXT[^=]*=\s*(\[[^\]]*\]|`[^`]*`)", re.S)
_EXT_TOKEN_RE = re.compile(r"\.[A-Za-z0-9]+")


def _read_frontend_source() -> str:
    """读取前端常量文件；不存在时跳过（本期前端尚未落地）。"""
    if not FRONTEND_CONSTANTS.is_file():
        pytest.skip(f"前端常量待 T6 落地：{FRONTEND_CONSTANTS} 不存在")
    return FRONTEND_CONSTANTS.read_text(encoding="utf-8")


def _string_literals(source: str) -> List[str]:
    """取出源码里所有字符串字面量（去掉引号并反转义）。"""
    literals: List[str] = []
    for match in _STRING_LITERAL_RE.finditer(source):
        raw = match.group(0)[1:-1]
        unescaped = (
            raw.replace("\\n", "\n")
            .replace('\\"', '"')
            .replace("\\'", "'")
            .replace("\\\\", "\\")
        )
        literals.append(unescaped)
    return literals


def _render_frontend_template(literal: str) -> str:
    """把前端模板字面量的插值占位替换成后端用的扩展名。"""
    return literal.replace("${ext}", ".docx").replace("{ext}", ".docx")


def test_unsupported_ext_message_matches_frontend() -> None:
    """``config.unsupported_ext_message('.docx')`` 必须与前端模板渲染结果逐字相同。"""
    source = _read_frontend_source()
    expected = config.unsupported_ext_message(".docx")
    rendered = [_render_frontend_template(literal) for literal in _string_literals(source)]
    assert expected in rendered, (
        "前后端旧格式报错文案不一致：\n"
        f"  后端：{expected}\n"
        f"  前端候选：{[item for item in rendered if '不支持' in item]}"
    )


def test_default_library_name_matches_frontend() -> None:
    """``config.DEFAULT_LIBRARY_NAME`` 必须在前端以同一字面量出现。"""
    source = _read_frontend_source()
    assert config.DEFAULT_LIBRARY_NAME in _string_literals(source), (
        "前后端缺省题库名不一致：" f"后端为 {config.DEFAULT_LIBRARY_NAME!r}"
    )


def test_accept_ext_matches_backend_allowed_ext() -> None:
    """前端 ``ACCEPT_EXT`` 的可选扩展名集合必须与后端 ``ALLOWED_EXT`` 完全一致。"""
    source = _read_frontend_source()
    matched = _ACCEPT_EXT_RE.search(source)
    assert matched is not None, "前端 constants/import.ts 未声明 ACCEPT_EXT"
    declared = {token.lower() for token in _EXT_TOKEN_RE.findall(matched.group(1))}
    assert declared == set(config.ALLOWED_EXT), (
        f"可选扩展名不一致：前端 {sorted(declared)} vs 后端 {sorted(config.ALLOWED_EXT)}"
    )


def test_backend_template_only_mentions_supported_formats() -> None:
    """后端模板本身必须点名 .json / .md（防止文案漂移成「仅支持 JSON」之类）。"""
    message = config.unsupported_ext_message("docx")
    assert ".json" in message and ".md" in message
    assert "「.docx」" in message  # 扩展名自动补点并加引号
    assert config.unsupported_ext_message(".markdown").startswith("不支持的文件类型「.markdown」")
