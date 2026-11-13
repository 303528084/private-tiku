"""Pydantic 请求/响应 schema 与全局枚举、错误码常量。

统一响应约定：``{code: int, message: str, data: Any}``；字段一律 snake_case。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 错误码（架构 §4）
# ---------------------------------------------------------------------------
CODE_OK: int = 0
CODE_PARAM_ERROR: int = 40001
CODE_NOT_FOUND: int = 40401
CODE_UNSUPPORTED_TYPE: int = 42201
CODE_PARSE_FAILED: int = 50001
CODE_INTERNAL_ERROR: int = 50000


class QType(str, Enum):
    """题型枚举。"""

    single = "single"
    multiple = "multiple"
    judge = "judge"
    unknown = "unknown"


class AnomalyType(str, Enum):
    """异常枚举（固定 6 类，架构 §6）。"""

    missing_answer = "missing_answer"
    missing_options = "missing_options"
    too_few_options = "too_few_options"
    empty_stem = "empty_stem"
    duplicate_number = "duplicate_number"
    parse_error = "parse_error"


class QuestionStatus(str, Enum):
    """题目状态。"""

    pending = "pending"
    published = "published"


class ImportStatus(str, Enum):
    """导入批次状态。"""

    parsing = "parsing"
    pending_confirm = "pending_confirm"
    committed = "committed"
    failed = "failed"


class FileStatus(str, Enum):
    """单文件解析状态。"""

    parsed = "parsed"
    failed = "failed"


# 便于校验集合
QTYPE_VALUES: frozenset[str] = frozenset(item.value for item in QType)
ANOMALY_TYPE_VALUES: frozenset[str] = frozenset(item.value for item in AnomalyType)


# ---------------------------------------------------------------------------
# 异常与响应外壳
# ---------------------------------------------------------------------------
class ApiError(Exception):
    """业务异常：携带业务码 + HTTP 状态码，由全局异常处理器转换为统一响应体。"""

    def __init__(
        self,
        message: str,
        code: int = CODE_PARAM_ERROR,
        http_status: int = 400,
        data: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.message: str = message
        self.code: int = code
        self.http_status: int = http_status
        self.data: Optional[Any] = data


def ok(data: Any = None, message: str = "ok") -> Dict[str, Any]:
    """构造成功响应体。"""
    return {"code": CODE_OK, "message": message, "data": data}


def fail(message: str, code: int = CODE_PARAM_ERROR, data: Any = None) -> Dict[str, Any]:
    """构造失败响应体（供异常处理器使用）。"""
    return {"code": code, "message": message, "data": data}


# ---------------------------------------------------------------------------
# 请求 schema
# ---------------------------------------------------------------------------
class QuestionUpdateRequest(BaseModel):
    """PUT /api/questions/{id} 请求体。"""

    stem: str = Field(default="", description="题干")
    options: Dict[str, str] = Field(default_factory=dict, description='选项 {"A": "..."}')
    answer: str = Field(default="", description="答案")
    analysis: str = Field(default="", description="解析")
    qtype: Optional[QType] = Field(default=None, description="题型，缺省时按答案自动判定")


class QuestionCreateRequest(QuestionUpdateRequest):
    """POST /api/questions 请求体（手录单题）。

    在 :class:`QuestionUpdateRequest` 的字段基础上增加「题库名称」与「题号」。
    与 PUT 的差异：``qtype`` 改为**必填字符串**并由接口层逐字校验，以便在缺失或
    非法时返回契约规定的 ``题型只能是 single / multiple / judge``（而不落到
    Pydantic 的通用枚举报错文案上）。
    """

    qtype: str = Field(default="", description="题型：single / multiple / judge（必填）")
    library_name: Optional[str] = Field(
        default=None, description="题库名称；省略时落缺省「手动录入」"
    )
    question_no: Optional[str] = Field(
        default=None, description="题号；留空时取该题库内纯数字题号 max+1"
    )


class AnomalyPatchRequest(BaseModel):
    """PATCH /api/anomalies/{id} 请求体。"""

    handled: bool = Field(default=True, description="true 标记为已处理")


class LoginRequest(BaseModel):
    """POST /api/auth/login 请求体（v1.4 T01 门禁）。"""

    token: str = Field(default="", description="访问口令（TIKU_ACCESS_TOKEN 的值）")


# ---------------------------------------------------------------------------
# 响应 schema（供 OpenAPI 文档展示；运行期返回 dict）
# ---------------------------------------------------------------------------
class QuestionPayload(BaseModel):
    """题目 JSON 结构（架构 §8）。"""

    id: int
    import_file_id: int
    question_no: str
    qtype: str
    stem: str
    options: Dict[str, str] = Field(default_factory=dict)
    answer: str = ""
    analysis: str = ""
    status: str = QuestionStatus.pending.value
    anomalies: list[Dict[str, Any]] = Field(default_factory=list)


class SearchQuery(BaseModel):
    """GET /api/search 查询参数（文档用途）。"""

    q: str = ""
    type: Optional[str] = None
    only_anomaly: bool = False
    doc_id: Optional[int] = None
    page: int = 1
    size: int = 20
