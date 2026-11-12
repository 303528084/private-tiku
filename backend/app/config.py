"""全局配置：目录、端口、上传与检索相关常量。

所有路径均可通过环境变量覆盖，容器内通过 ``TIKU_DATA_DIR=/app/data`` 指向挂载卷。

v1.3 起导入格式收敛为 **JSON + Markdown**：``.docx/.txt/.pdf`` 不再接受上传，
其历史数据（``import_files.file_type`` 为 docx/txt/pdf 的记录）原样保留。
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# 目录布局
# ---------------------------------------------------------------------------
# 本地布局：  <repo>/backend/app/config.py
# 容器布局：  /app/backend/app/config.py
# 两种情况 BASE_DIR 都指向「仓库根 / 应用根」，frontend/dist 与之同级。
BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent

DATA_DIR: Path = Path(os.getenv("TIKU_DATA_DIR") or (BASE_DIR / "data")).resolve()
"""数据根目录：SQLite 数据库与上传的原始文件都落在这里。"""

DB_PATH: Path = Path(os.getenv("TIKU_DB_PATH") or (DATA_DIR / "tiku.db")).resolve()
"""SQLite 数据库文件路径。"""

UPLOAD_DIR: Path = DATA_DIR / "uploads"
"""原始文件落盘目录，按 import_id 分子目录。"""

FRONTEND_DIST: Path = Path(os.getenv("TIKU_FRONTEND_DIST") or (BASE_DIR / "frontend" / "dist")).resolve()
"""前端构建产物目录，存在时由 FastAPI StaticFiles 托管。"""

# ---------------------------------------------------------------------------
# 服务参数
# ---------------------------------------------------------------------------
APP_PORT: int = int(os.getenv("TIKU_PORT", "8000"))

MAX_FILES_PER_REQUEST: int = 20
"""单次批量导入的文件数量上限（PRD P0-1 要求 ≥20）。"""

MAX_FILE_SIZE: int = 20 * 1024 * 1024
"""单个文件大小上限（字节）。"""

ALLOWED_EXT: frozenset[str] = frozenset({".json", ".md", ".markdown"})
"""允许上传的扩展名（小写，含点）。

v1.3 起仅接受结构化格式：``.json`` 走字段映射，``.md`` / ``.markdown`` 走
Markdown 状态机解析；``.docx/.txt/.pdf`` 一律拒绝（见 :func:`unsupported_ext_message`）。
"""

FILE_TYPE_BY_EXT: dict[str, str] = {".json": "json", ".md": "md", ".markdown": "md"}
"""扩展名 → ``import_files.file_type``；``.markdown`` 与 ``.md`` 合并为 ``md``。"""

ALLOWED_EXT_HINT: str = ".json/.md"
"""错误文案里展示的受支持扩展名（与 ``ALLOWED_EXT`` 必须同步）。"""

UNSUPPORTED_EXT_TEMPLATE: str = (
    "不支持的文件类型「{ext}」：v1.3 起仅支持 .json / .md 导入。"
    "已有的 docx/txt/pdf 题库不受影响，可直接搜索与导出。"
)
"""旧格式拒绝文案模板（PRD §v1.3.7）。

前端 ``frontend/src/constants/import.ts::unsupportedExtMessage()`` 必须给出**逐字
相同**的文案，由 ``tests/test_text_parity.py`` 做字面量校验 —— 改文案必须同时改两处。
"""

UNSUPPORTED_EXT_PREFIX: str = "不支持的文件类型"
"""旧格式拒绝文案的固定前缀（供 API 判定「是否全部为类型错误」）。"""

MANUAL_FILE_TYPE: str = "manual"
"""手录题库的 ``import_files.file_type``（仅由 ``POST /api/questions`` 产生）。"""

DEFAULT_LIBRARY_NAME: str = "手动录入"
"""手录时未指定题库名称的缺省值（前端下拉与后端兜底共用同一字面量）。"""

# ---------------------------------------------------------------------------
# 检索 / 分页
# ---------------------------------------------------------------------------
DEFAULT_PAGE_SIZE: int = 20
MAX_PAGE_SIZE: int = 100
FTS_MIN_TOKEN_LEN: int = 3
"""trigram 分词器要求 MATCH 关键词长度 ≥3，短于该值退化为 LIKE。"""

MIN_OPTION_COUNT: int = 4
"""「选项不足」阈值：护理选择题通常四选项（架构 §6）。"""

SNIPPET_HEAD: int = 240
"""前端卡片上题干默认截断长度（不参与检索，仅用于导出/预览）。"""

# ---------------------------------------------------------------------------
# 手录（POST /api/questions）字段上限
# ---------------------------------------------------------------------------
MAX_LIBRARY_NAME_LEN: int = 120
"""题库名称长度上限（字符数）。"""

MAX_QUESTION_NO_LEN: int = 32
"""题号长度上限（字符数）。"""

LIBRARY_LIST_LIMIT: int = 500
"""``GET /api/libraries`` 返回条数上限（防御性上限）。"""


def ensure_dirs() -> None:
    """创建运行期必需目录（幂等）。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def import_upload_dir(import_id: int) -> Path:
    """返回某次导入的原始文件目录（不创建）。"""
    return UPLOAD_DIR / f"import_{import_id}"


def unsupported_ext_message(ext: str) -> str:
    """生成「旧格式不受支持」的提示文案（PRD §v1.3.7 逐字）。

    Args:
        ext: 文件扩展名，可带点也可不带（``.docx`` / ``docx``），大小写不敏感。

    Returns:
        形如 ``不支持的文件类型「.docx」：v1.3 起仅支持 .json / .md 导入。…`` 的文案。
    """
    suffix = (ext or "").strip().lower()
    if not suffix:
        suffix = "未知"
    elif not suffix.startswith("."):
        suffix = f".{suffix}"
    return UNSUPPORTED_EXT_TEMPLATE.format(ext=suffix)
