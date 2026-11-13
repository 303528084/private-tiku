"""题库 Markdown 渲染：``GET /api/export?format=md`` 与分享包 ``bank.md`` 共用。

**为什么单独成一个模块**：分享包需要与普通导出**逐字相同**的正文（这样
``bank.md`` 才能被自家 Markdown 解析器往返吃下，见 ``parser/markdown.py``），
而 ``share/`` 不允许 import ``api/``（分层方向），所以把渲染实现放在这里，
``api/export.py`` 反过来复用它。``api/export.py::_render_markdown`` 保留为
同名包装，历史调用方（含测试）无需改动。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

QTYPE_LABEL: Dict[str, str] = {
    "single": "单选题",
    "multiple": "多选题",
    "judge": "判断题",
    "unknown": "未知题型",
}
"""题型 → 中文标签（导出正文与 manifest 的 ``qtype_distribution`` 共用）。"""

ANOMALY_LABEL: Dict[str, str] = {
    "missing_answer": "缺答案",
    "missing_options": "缺选项",
    "too_few_options": "选项不足",
    "empty_stem": "无题干",
    "duplicate_number": "题号重复",
    "parse_error": "解析异常",
}
"""异常类型 → 中文标签。"""


def render_markdown(questions: List[Dict[str, Any]]) -> str:
    """渲染为结构化 Markdown（可被 :func:`app.parser.markdown.parse_bank` 往返解析）。

    Args:
        questions: ``db.question_to_dict`` 产出的题目列表。

    Returns:
        Markdown 全文（``\\n`` 分隔，末尾无额外空行）。
    """
    lines: List[str] = ["# 私有护理题库导出", ""]
    lines.append(f"- 导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 题目总数：{len(questions)}")
    lines.append("")
    for question in questions:
        title = f"## {question['question_no']}. [{QTYPE_LABEL.get(question['qtype'], question['qtype'])}]"
        lines.append(title)
        lines.append("")
        lines.append(question["stem"] or "（题干为空）")
        lines.append("")
        for letter, value in question["options"].items():
            lines.append(f"- **{letter}.** {value}")
        if question["options"]:
            lines.append("")
        answer = question["answer"] or "（无答案）"
        lines.append(f"**答案**：{answer}")
        if question["analysis"]:
            lines.append("")
            lines.append(f"**解析**：{question['analysis']}")
        if question["anomalies"]:
            labels = "、".join(
                ANOMALY_LABEL.get(item["type"], item["type"]) for item in question["anomalies"]
            )
            lines.append("")
            lines.append(f"**异常标记**：{labels}")
        if question.get("filename"):
            lines.append("")
            lines.append(f"**来源**：{question['filename']}")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


__all__ = ["ANOMALY_LABEL", "QTYPE_LABEL", "render_markdown"]
