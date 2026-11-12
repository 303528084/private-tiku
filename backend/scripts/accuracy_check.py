"""解析准确率抽检：对 ``accuracy_100.txt`` 计算「题干/选项/答案/题型四项全对」的准确率。

判定口径（与 PRD P0-2 对应）：
* 题干：NFKC 归一 + 空白压缩后完全相等（跨行题干以单空格连接）；
* 选项：字母与文本逐一相等（键统一大写、文本 strip）；
* 答案：按题型规范化后的字符串相等（判断题为 √/×，多选为升序字母）；
* 题型：``single`` / ``multiple`` / ``judge`` / ``unknown`` 相等。

说明：比对侧的归一化使用标准库 ``unicodedata``（不是解析器的 ``normalize_text``），
避免「用被测实现校验被测实现」。期望值取自 ``accuracy_100_truth.json``（人工标注）。

用法::

    cd backend && python scripts/accuracy_check.py            # 打印报告
    cd backend && python scripts/accuracy_check.py --verbose  # 附带失败题明细
"""

from __future__ import annotations

import json
import sys
import unicodedata
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.parser.pipeline import run  # noqa: E402  (需先插入 sys.path)

FIXTURES = BACKEND / "app" / "tests" / "fixtures"
TXT_PATH = FIXTURES / "accuracy_100.txt"
TRUTH_PATH = FIXTURES / "accuracy_100_truth.json"

THRESHOLD = 90.0  # PRD P0-2：人工抽样 100 题解析准确率 ≥90%
FIELDS = ("stem", "options", "answer", "qtype")


def canon(text: str) -> str:
    """标准归一化：NFKC + 空白压缩 + 去首尾空白。"""
    normalized = unicodedata.normalize("NFKC", text or "")
    return " ".join(normalized.split()).strip()


def canon_options(options: Dict[str, Any]) -> Dict[str, str]:
    """选项按键排序并归一化。"""
    data = {str(key).strip().upper(): canon(str(value)) for key, value in (options or {}).items()}
    return dict(sorted(data.items()))


def load_cases() -> List[Dict[str, Any]]:
    """读取人工标注的期望值。"""
    return json.loads(TRUTH_PATH.read_text(encoding="utf-8"))


def evaluate() -> Dict[str, Any]:
    """执行解析并与 ground truth 比对，返回结构化报告。"""
    truth = load_cases()
    result = run(TXT_PATH)

    # 题号可能重复，按出现顺序用队列匹配
    pending: Dict[str, Deque[Dict[str, Any]]] = {}
    for item in truth:
        pending.setdefault(item["no"], deque()).append(item)

    failures: List[Dict[str, Any]] = []
    extra_blocks: List[Dict[str, Any]] = []
    field_ok = {field: 0 for field in FIELDS}
    matched = 0

    for parsed_item in result.items:
        parsed = parsed_item.parsed
        queue = pending.get(parsed.no or "")
        if not queue:
            extra_blocks.append(
                {"no": parsed.no, "stem": parsed.stem[:40], "qtype": parsed.qtype}
            )
            continue
        expected = queue.popleft()
        matched += 1

        actual = {
            "stem": canon(parsed.stem),
            "options": canon_options(parsed.options),
            "answer": canon(parsed.answer),
            "qtype": parsed.qtype,
        }
        wanted = {
            "stem": canon(expected["stem"]),
            "options": canon_options(expected["options"]),
            "answer": canon(expected["answer"]),
            "qtype": expected["qtype"],
        }

        wrong = [field for field in FIELDS if actual[field] != wanted[field]]
        for field in FIELDS:
            if field not in wrong:
                field_ok[field] += 1
        if wrong:
            failures.append(
                {
                    "no": expected["no"],
                    "wrong": wrong,
                    "expected": {field: wanted[field] for field in wrong},
                    "actual": {field: actual[field] for field in wrong},
                    "anomalies": [anomaly.type for anomaly in parsed_item.anomalies],
                }
            )

    missing = [
        {"no": item["no"], "stem": canon(item["stem"])[:40]}
        for queue in pending.values()
        for item in queue
    ]

    total = len(truth)
    correct = total - len(failures) - len(missing)
    return {
        "total": total,
        "parsed_blocks": result.block_count,
        "matched": matched,
        "correct": correct,
        "accuracy": round(correct * 100.0 / total, 1),
        "field_accuracy": {
            field: round(field_ok[field] * 100.0 / total, 1) for field in FIELDS
        },
        "failures": failures,
        "missing": missing,
        "extra_blocks": extra_blocks,
        "anomaly_total": result.anomaly_count,
    }


def print_report(report: Dict[str, Any], verbose: bool = False) -> None:
    """打印可读报告。"""
    print("=" * 66)
    print("解析准确率抽检（accuracy_100.txt，人工标注 ground truth）")
    print("=" * 66)
    print(f"标注题目数      : {report['total']}")
    print(f"切分出的题目块  : {report['parsed_blocks']}（成功配对 {report['matched']}）")
    print(f"四项全对        : {report['correct']}")
    print(f"整体准确率      : {report['accuracy']}%   （PRD 要求 ≥{THRESHOLD}%）")
    print(f"分项准确率      : " + "  ".join(
        f"{field}={value}%" for field, value in report["field_accuracy"].items()
    ))
    print(f"异常标记总数    : {report['anomaly_total']}")

    if report["missing"]:
        print(f"\n-- 完全未被切分出来的题（{len(report['missing'])}）--")
        for item in report["missing"]:
            print(f"  题号 {item['no']}: {item['stem']}")
    if report["extra_blocks"]:
        print(f"\n-- 多出来的题目块（{len(report['extra_blocks'])}）--")
        for item in report["extra_blocks"]:
            print(f"  题号 {item['no']!r}: {item['stem']}")
    if report["failures"]:
        print(f"\n-- 四项未全对（{len(report['failures'])}）--")
        for item in report["failures"]:
            print(f"  题号 {item['no']}  错在 {item['wrong']}  异常={item['anomalies']}")
            if verbose:
                for field in item["wrong"]:
                    print(f"      期望 {field}: {item['expected'][field]!r}")
                    print(f"      实际 {field}: {item['actual'][field]!r}")

    verdict = "PASS" if report["accuracy"] >= THRESHOLD else "FAIL"
    print("\n结论：" + verdict)


def main() -> int:
    """CLI 入口：准确率不达标时返回非 0。"""
    report = evaluate()
    print_report(report, verbose="--verbose" in sys.argv)
    return 0 if report["accuracy"] >= THRESHOLD else 1


if __name__ == "__main__":
    raise SystemExit(main())
