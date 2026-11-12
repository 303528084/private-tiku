"""生成解析测试用的三格式 fixture：TXT(GBK) / DOCX / PDF。

用法（在 backend 目录下）::

    python scripts/gen_fixtures.py

产物写入 ``app/tests/fixtures/``：

* ``sample_gbk.txt``        —— GBK 编码题库，验证 chardet 探测
* ``sample_questions.docx`` —— python-docx 生成
* ``sample_questions.pdf``  —— 手写最小 PDF 字节流（不引入 reportlab 运行时依赖）

PDF 说明：pdfplumber 只能读不能写，因此这里手工拼装 PDF 字节流。
优先尝试「Type0 + Identity-H + 自定义 ToUnicode」以支持中文；
若提取校验失败则自动回退为 Helvetica(ASCII) 版本，保证 fixture 始终可用。
"""

from __future__ import annotations

import sys
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "app" / "tests" / "fixtures"

GBK_QUESTIONS = """内科护理学单选（GBK 编码）

1. 慢性阻塞性肺疾病患者氧疗的流量一般为？
A. 1～2L/min
B. 3～4L/min
C. 5～6L/min
D. 8～10L/min
答案：A
解析：持续低流量低浓度吸氧，避免二氧化碳潴留。

2. 消化性溃疡疼痛的典型节律中，十二指肠溃疡多表现为？
A. 餐后痛
B. 饥饿痛
C. 持续性钝痛
D. 无规律痛
答案：B
"""

DOCX_QUESTIONS = [
    "外科护理学试题",
    "1. 成人烧伤面积估算中，双上肢占体表面积的百分比是？",
    "A. 9%",
    "B. 18%",
    "C. 27%",
    "D. 46%",
    "答案：B",
    "解析：双上肢（含双手）共 18%。",
    "2. 张力性气胸的首要处理措施是？",
    "A. 立即排气减压",
    "B. 静脉补液",
    "C. 应用抗生素",
    "D. 口服止痛药",
    "答案：A",
    "3. 全身麻醉患者清醒前应采取的体位是？",
    "A. 去枕平卧头偏向一侧",
    "B. 半坐卧位",
    "C. 俯卧位",
    "D. 头高足低位",
    "答案：A",
]

PDF_QUESTIONS_ZH = [
    "妇产科护理学试题",
    "1. 正常足月妊娠的羊水量约为？",
    "A. 300ml",
    "B. 800ml",
    "C. 2000ml",
    "D. 3000ml",
    "答案：B",
    "2. 产后出血最常见的原因是？",
    "A. 子宫收缩乏力",
    "B. 软产道裂伤",
    "C. 胎盘因素",
    "D. 凝血功能障碍",
    "答案：A",
]

PDF_QUESTIONS_ASCII = [
    "Nursing Test Sample",
    "1. Which arrhythmia causes pulse deficit?",
    "A. Atrial fibrillation",
    "B. Ventricular fibrillation",
    "C. Atrial flutter",
    "D. Premature beat",
    "Answer: A",
    "2. The normal adult axillary temperature is 36.0-37.0 C.",
    "Answer: True",
]


# ---------------------------------------------------------------------------
# TXT(GBK)
# ---------------------------------------------------------------------------
def write_gbk_txt() -> Path:
    path = FIXTURE_DIR / "sample_gbk.txt"
    path.write_bytes(GBK_QUESTIONS.encode("gbk"))
    return path


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------
def write_docx() -> Path:
    from docx import Document

    document = Document()
    for text in DOCX_QUESTIONS:
        document.add_paragraph(text)
    path = FIXTURE_DIR / "sample_questions.docx"
    document.save(str(path))
    return path


# ---------------------------------------------------------------------------
# PDF（手写字节流）
# ---------------------------------------------------------------------------
def _to_unicode_cmap(chars: list[str]) -> bytes:
    """为 PDF 生成 ToUnicode CMap（CID 即 Unicode 码位，见 Identity-H）。

    注意：BFChar 块每组最多 100 条，超出需拆分。
    """
    entries = sorted({f"{ord(char):04X}" for char in chars})
    header = (
        "/CIDInit /ProcSet findresource begin\n"
        "12 dict begin\n"
        "begincmap\n"
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        "/CMapName /Adobe-Identity-UCS def\n"
        "/CMapType 2 def\n"
        "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
    )
    body_parts: list[str] = []
    for start in range(0, len(entries), 100):
        chunk = entries[start:start + 100]
        body_parts.append(f"{len(chunk)} beginbfchar\n")
        for code in chunk:
            body_parts.append(f"<{code}> <{code}>\n")
        body_parts.append("endbfchar\n")
    footer = (
        "endcmap\n"
        "CMapName currentdict /CMap defineresource pop\n"
        "end\n"
        "end\n"
    )
    return (header + "".join(body_parts) + footer).encode("ascii")


def _content_type0(lines: list[str]) -> bytes:
    """Type0(CJK) 字体下的文本绘制指令，文本按 UTF-16BE 十六进制串输出。"""
    parts = ["BT", "/F1 12 Tf", "16 TL", "1 0 0 1 40 800 Tm"]
    first = True
    for line in lines:
        encoded = line.encode("utf-16-be").hex().upper()
        if first:
            parts.append(f"<{encoded}> Tj")
            first = False
        else:
            parts.append("T*")
            parts.append(f"<{encoded}> Tj")
    parts.append("ET")
    return "\n".join(parts).encode("ascii")


def _content_helvetica(lines: list[str]) -> bytes:
    """Helvetica(WinAnsi) 字体下的文本绘制指令，仅支持 ASCII。"""

    def escape(text: str) -> str:
        return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    parts = ["BT", "/F1 12 Tf", "16 TL", "1 0 0 1 40 800 Tm"]
    first = True
    for line in lines:
        if first:
            parts.append(f"({escape(line)}) Tj")
            first = False
        else:
            parts.append("T*")
            parts.append(f"({escape(line)}) Tj")
    parts.append("ET")
    return "\n".join(parts).encode("latin-1")


def build_pdf(lines: list[str], cjk: bool = True) -> bytes:
    """拼装一个单页最小 PDF。"""
    if cjk:
        content = _content_type0(lines)
        chars = [char for char in "".join(lines) if char != "\n"]
        objects: list[bytes] = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842]"
            b" /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n"
            + content
            + b"\nendstream",
            b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light"
            b" /Encoding /Identity-H /DescendantFonts [6 0 R] /ToUnicode 7 0 R >>",
            b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light"
            b" /CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 2 >>"
            b" /FontDescriptor 8 0 R /DW 1000 /DW2 [880 -1000] >>",
            b"<< /Length "
            + str(len(_to_unicode_cmap(chars))).encode("ascii")
            + b" >>\nstream\n"
            + _to_unicode_cmap(chars)
            + b"\nendstream",
            b"<< /Type /FontDescriptor /FontName /STSong-Light /Flags 4"
            b" /FontBBox [0 -200 1000 900] /ItalicAngle 0 /Ascent 880 /Descent -120"
            b" /CapHeight 700 /StemV 93 >>",
        ]
    else:
        content = _content_helvetica(lines)
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842]"
            b" /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n"
            + content
            + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        ]

    buffer = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(buffer))
        buffer += f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n"

    xref_offset = len(buffer)
    count = len(objects) + 1
    buffer += f"xref\n0 {count}\n".encode("ascii")
    buffer += b"0000000000 65535 f \n"
    for offset in offsets:
        buffer += f"{offset:010d} 00000 n \n".encode("ascii")
    buffer += (
        f"trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(buffer)


def _extract(path: Path) -> str:
    import pdfplumber

    with pdfplumber.open(str(path)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def write_pdf() -> Path:
    """优先写中文 PDF；若提取结果不含预期中文，则回退 ASCII 版本。"""
    path = FIXTURE_DIR / "sample_questions.pdf"
    candidate = build_pdf(PDF_QUESTIONS_ZH, cjk=True)
    path.write_bytes(candidate)
    try:
        text = _extract(path)
    except Exception as exc:  # pragma: no cover - 依赖异常
        print(f"[pdf] 中文 PDF 提取抛异常：{exc}", file=sys.stderr)
        text = ""
    if "羊水" in text and "产后出血" in text:
        print("[pdf] 中文 Type0/ToUnicode 方案可用")
        return path
    print("[pdf] 中文方案不可用，回退 Helvetica(ASCII) 版本", file=sys.stderr)
    path.write_bytes(build_pdf(PDF_QUESTIONS_ASCII, cjk=False))
    return path


def main() -> int:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    txt_path = write_gbk_txt()
    docx_path = write_docx()
    pdf_path = write_pdf()
    print(f"TXT  : {txt_path}")
    print(f"DOCX : {docx_path}")
    print(f"PDF  : {pdf_path}")
    print("--- PDF 提取预览 ---")
    try:
        print(_extract(pdf_path)[:600])
    except Exception as exc:  # pragma: no cover
        print(f"提取失败：{exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
