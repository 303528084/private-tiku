"""万级题量检索性能验证（PRD P0-3：万级题量下查询 <1s）。

做法：
1. 在**临时目录**建库（不污染仓库 ``data/``），批量写入 10,000 道已入库题目，
   其中约 10% 挂异常；questions_fts 由 AFTER INSERT 触发器自动同步；
2. 直接调用 ``app.api.search.search_questions``（与 HTTP 接口同一条代码路径），
   分别测量 2 字词（走 LIKE 降级）与 3 字以上词（走 FTS5 MATCH）的 ``took_ms``，
   各跑 5 次取中位数；
3. 输出与 PRD 阈值（1000ms）的对比结论。

用法::

    cd backend && python scripts/perf_10k.py [--rows 10000] [--runs 5]
"""

from __future__ import annotations

import argparse
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))


def _prepare_temp_env() -> Path:
    """把数据目录指向临时目录（必须在 import app.config 之前）。"""
    temp_dir = Path(tempfile.mkdtemp(prefix="tiku-perf-"))
    os.environ["TIKU_DATA_DIR"] = str(temp_dir)
    os.environ.pop("TIKU_DB_PATH", None)
    return temp_dir


# 中文语料片段：构造与真实护理题库接近的题干/选项/答案/解析
STEMS = [
    "患者出现脉搏短绌时应首先考虑的心律失常是?",
    "心肺复苏有效指征不包括下列哪一项?",
    "静脉输液过程中发生空气栓塞应立即采取的体位是?",
    "预防压疮最关键的护理措施是?",
    "糖尿病酮症酸中毒患者的呼吸气味呈?",
    "上消化道出血患者排出的大便性状为?",
    "休克早期最具特征性的血流动力学改变是?",
    "慢性阻塞性肺疾病患者氧疗应采用的方式是?",
    "甲状腺大部切除术后最危急的并发症是?",
    "留置导尿患者预防尿路感染最重要的护理措施是?",
]
PROMPTS = ["下列叙述正确的是?", "以下说法错误的是?", "首选的处理措施是?", "最可能的诊断是?"]
OPTIONS = {
    "A": "房颤",
    "B": "室颤",
    "C": "心力衰竭急性加重",
    "D": "电解质紊乱与酸碱失衡",
}
ANALYSIS = "结合临床表现与辅助检查综合判断，注意与其他心律失常鉴别。"


def seed(conn, rows: int) -> None:
    """批量写入题目与异常（FTS 由触发器同步）。"""
    conn.execute("INSERT INTO imports (status, total_files) VALUES ('committed', 1)")
    import_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.execute(
        "INSERT INTO import_files (import_id, filename, file_type, status, total_questions)"
        " VALUES (?, 'perf.txt', 'txt', 'parsed', ?)",
        (import_id, rows),
    )
    file_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    sql = (
        "INSERT INTO questions (import_file_id, question_no, qtype, stem, options_json,"
        " answer, analysis, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'published')"
    )
    import json

    questions = []
    for index in range(rows):
        stem = f"{STEMS[index % len(STEMS)]}{PROMPTS[index % len(PROMPTS)]}"
        options = dict(OPTIONS)
        options["A"] = f"{OPTIONS['A']}（第{index + 1}题）"
        questions.append(
            (
                file_id,
                str(index + 1),
                "single",
                stem,
                json.dumps(options, ensure_ascii=False),
                "A",
                ANALYSIS,
            )
        )
    conn.executemany(sql, questions)

    # 约 10% 挂一条未处理异常
    conn.execute(
        "INSERT INTO anomalies (question_id, type, detail, handled)"
        " SELECT id, 'missing_answer', '批量造数用', 0 FROM questions WHERE id % 10 = 0"
    )
    conn.commit()


def measure(question: str, runs: int) -> dict:
    """对同一查询跑 runs 次，返回中位数与全部耗时。"""
    from app.api.search import search_questions

    samples = []
    totals = []
    for _ in range(runs):
        result = search_questions(q=question, size=20)
        samples.append(result["took_ms"])
        totals.append(result["total"])
    return {
        "q": question,
        "runs": runs,
        "ms_samples": samples,
        "median_ms": statistics.median(samples),
        "max_ms": max(samples),
        "total": totals[-1],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=10000)
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()

    temp_dir = _prepare_temp_env()

    from app import config, db

    db.reset_init_flag()
    db.init_db()
    print("=" * 66)
    print(f"万级检索性能验证：{args.rows} 道题 / {args.runs} 次取样取中位数")
    print("=" * 66)
    print(f"临时数据目录：{config.DATA_DIR}")

    started = time.perf_counter()
    with db.get_conn() as conn:
        seed(conn, args.rows)
    seed_seconds = time.perf_counter() - started
    print(f"造数耗时：{seed_seconds:.1f}s")

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT (SELECT COUNT(*) FROM questions) AS q,"
            "       (SELECT COUNT(*) FROM anomalies) AS a,"
            "       (SELECT COUNT(*) FROM questions_fts) AS f"
        ).fetchone()
    print(
        f"库内规模：questions={row['q']}  anomalies={row['a']}  "
        f"questions_fts={row['f']}  db={config.DB_PATH.stat().st_size / 1024 / 1024:.1f}MB"
    )
    print(f"FTS trigram 可用：{db.FTS_TRIGRAM}")

    results = [
        measure("房颤", args.runs),                       # 2 字 → LIKE 降级
        measure("心律失常", args.runs),                    # 4 字 → FTS5 MATCH
        measure("脉搏短绌 心律失常", args.runs),            # 多关键词 → FTS5 MATCH
    ]

    print()
    for item in results:
        path = "FTS5 MATCH" if len(item["q"].split()) == 1 and len(item["q"]) >= 3 else (
            "FTS5 MATCH(多词)" if len(item["q"].split()) > 1 else "LIKE 降级"
        )
        print(f"查询 {item['q']!r:<18} 路径={path:<16} 命中={item['total']:<6} "
              f"took_ms={item['ms_samples']} 中位数={item['median_ms']}ms 最大={item['max_ms']}ms")

    worst = max(item["median_ms"] for item in results)
    print()
    print(f"最差中位数：{worst}ms   PRD 阈值：1000ms")
    verdict = "PASS" if worst < 1000 else "FAIL"
    print(f"结论：{verdict}")

    shutil.rmtree(temp_dir, ignore_errors=True)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
