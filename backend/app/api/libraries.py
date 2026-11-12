"""题库（来源文档）API：为手录页的「题库名称」下拉提供候选数据。

口径与导入列表一致：按 ``import_files.filename`` 分组、计数走实时聚合、
``question_count = 0`` 的记录不出现（删空了的库不再作为候选）。
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from .. import config, db
from ..models import ok

router = APIRouter(prefix="/libraries", tags=["libraries"])


@router.get("")
def list_libraries() -> Dict[str, Any]:
    """题库名称列表（含历史 docx/txt/pdf 来源与手录库）。

    Returns:
        ``{"items": [{"id", "name", "file_type", "kind", "question_count", "last_updated"}]}``；
        ``kind`` ∈ ``manual|json|md|docx|txt|pdf|other``（``file_type`` 直出，脏值归 ``other``）；
        排序 ``last_updated DESC, name ASC``（刚录过的库排最前），上限
        :data:`app.config.LIBRARY_LIST_LIMIT`。
    """
    with db.get_conn() as conn:
        items = db.list_libraries(conn, limit=config.LIBRARY_LIST_LIMIT)
    return ok({"items": items})
