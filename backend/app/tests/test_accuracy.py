"""解析准确率门禁测试（PRD P0-2：抽样 100 题准确率 ≥90%）。

样本与标注由 ``scripts/gen_accuracy_fixture.py`` 生成：
* ``fixtures/accuracy_100.txt``      待解析的原始文档（含多种真实脏排版）
* ``fixtures/accuracy_100_truth.json`` 人工标注的题干/选项/答案/题型

比对逻辑在 ``scripts/accuracy_check.py``，本文件只做门禁与明细输出。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND / "scripts"))

import accuracy_check  # noqa: E402  (需先插入 sys.path)

THRESHOLD = accuracy_check.THRESHOLD


@pytest.fixture(scope="module")
def report() -> dict:
    """整个模块复用一次解析结果。"""
    return accuracy_check.evaluate()


def test_fixture_has_100_annotated_questions(report: dict) -> None:
    assert report["total"] == 100
    assert report["matched"] + len(report["missing"]) == 100


def test_accuracy_meets_prd_threshold(report: dict) -> None:
    """四项（题干/选项/答案/题型）全对率必须 ≥90%。"""
    if report["accuracy"] < THRESHOLD:
        accuracy_check.print_report(report, verbose=True)
    assert report["accuracy"] >= THRESHOLD, (
        f"解析准确率 {report['accuracy']}% 低于 PRD 要求的 {THRESHOLD}%；"
        f"失败题：{report['failures']}；未识别：{report['missing']}"
    )


def test_each_field_accuracy_is_reasonable(report: dict) -> None:
    """分项准确率同样不得低于 90%（防止整题全对率被个别维度拖累）。"""
    for field, value in report["field_accuracy"].items():
        assert value >= THRESHOLD, f"{field} 准确率 {value}% 低于 {THRESHOLD}%"


def test_judge_and_multiple_answers_are_normalized(report: dict) -> None:
    """判断题答案统一为 √/×、多选字母升序，是本样本的核心断言之一。"""
    assert report["field_accuracy"]["answer"] >= 90.0
    assert report["field_accuracy"]["qtype"] >= 90.0
