"""全局配置：目录、端口、上传与检索相关常量。

所有路径均可通过环境变量覆盖，容器内通过 ``TIKU_DATA_DIR=/app/data`` 指向挂载卷。
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

ALLOWED_EXT: frozenset[str] = frozenset({".docx", ".txt", ".pdf"})
"""允许上传的扩展名（小写，含点）。"""

FILE_TYPE_BY_EXT: dict[str, str] = {".docx": "docx", ".txt": "txt", ".pdf": "pdf"}

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


def ensure_dirs() -> None:
    """创建运行期必需目录（幂等）。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def import_upload_dir(import_id: int) -> Path:
    """返回某次导入的原始文件目录（不创建）。"""
    return UPLOAD_DIR / f"import_{import_id}"
