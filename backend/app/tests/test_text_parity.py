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


def test_filename_input_normalization_contract() -> None:
    """「文件名入参」前后端口径约定：钉住差异 + 证明真实链路安全。

    后端 ``config.unsupported_ext_message()`` 对含点串只做「小写 + 补前导点」、
    **不取后缀**；前端 ``constants/import.ts::unsupportedExtMessage()`` 会把含点
    串当文件名取后缀。两者对同一个全名会给出不同文案，本用例把这一差异钉住，
    同时静态证明真实调用方传入的是已归一化的 ``.ext``，故用户可见文案始终逐字
    一致 —— 防止将来调用方改成传全名时出现两种说法。
    """
    # 1) 后端：含点串只补点、不取后缀
    assert "「.old.docx」" in config.unsupported_ext_message("old.DOCX")
    # 2) 裸后缀 / 带点后缀 / 带空白三种归一化输入，前后端口径一致
    assert (
        config.unsupported_ext_message(".docx")
        == config.unsupported_ext_message("docx")
        == config.unsupported_ext_message(" DOCX ")
    )
    # 3) 真实链路：调用方传的是已归一化后缀，而非文件全名
    dropzone = FRONTEND_CONSTANTS.parent.parent / "components" / "UploadDropzone.tsx"
    assert dropzone.is_file(), f"前端上传组件缺失：{dropzone}"
    dropzone_source = dropzone.read_text(encoding="utf-8")
    assert "unsupportedExtMessage(fileExt(file.name) || file.name)" in dropzone_source
    # 4) 前端函数体确实具备「取后缀」能力（差异来源），且仍复用 fileExt
    constants_source = _read_frontend_source()
    function_re = re.compile(
        r"export function unsupportedExtMessage[^{]*\{(?P<body>.*?)\n\}", re.S
    )
    matched = function_re.search(constants_source)
    assert matched is not None, "前端未声明 unsupportedExtMessage 函数"
    assert "fileExt(" in matched.group("body")
