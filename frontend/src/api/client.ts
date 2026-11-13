/**
 * 统一 API 客户端：所有请求走 axios 实例（baseURL `/api`），
 * 自动解包 `{code, message, data}` 外壳，非 0 业务码统一抛 ApiError。
 */
import axios, { AxiosError, AxiosProgressEvent } from 'axios';

export const API_BASE = '/api';

/** 统一响应外壳。 */
export interface ApiEnvelope<T> {
  code: number;
  message: string;
  data: T;
}

/** 业务/网络异常。 */
export class ApiError extends Error {
  readonly code: number;

  readonly status: number;

  constructor(message: string, code: number, status: number) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
  }
}

/** 把任意异常转成可展示文案。 */
export function toErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

const http = axios.create({
  baseURL: API_BASE,
  timeout: 60000,
});

/** 登录页路径（门禁会话失效时的跳转目标）。 */
export const LOGIN_PATH = '/login';

/**
 * 会话失效时跳登录页。
 *
 * 刻意**不清理任何全局状态**：只做一次整页跳转，避免「清状态 → 触发请求 →
 * 又 401 → 又跳转」的循环。已在登录页时直接返回（登录页自身的 401 由页面展示）。
 */
function redirectToLogin(): void {
  if (typeof window === 'undefined') {
    return;
  }
  if (window.location.pathname === LOGIN_PATH) {
    return;
  }
  window.location.replace(LOGIN_PATH);
}

http.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ApiEnvelope<unknown>>) => {
    const status = error.response?.status ?? 0;
    const body = error.response?.data;
    const url = error.config?.url ?? '';
    // 门禁 401 → 跳登录页；/auth/* 自身的 401（口令错误）交给页面展示，不跳转。
    if (status === 401 && !url.includes('/auth/')) {
      redirectToLogin();
    }
    if (body && typeof body === 'object' && typeof body.code === 'number') {
      throw new ApiError(body.message || '请求失败', body.code, status);
    }
    if (error.code === 'ECONNABORTED') {
      throw new ApiError('请求超时，请稍后重试', -1, status);
    }
    throw new ApiError(error.message || '网络异常，请确认后端已启动', -1, status);
  },
);

async function request<T>(promise: Promise<{ data: ApiEnvelope<T> }>): Promise<T> {
  const response = await promise;
  const body = response.data;
  if (!body || typeof body.code !== 'number') {
    throw new ApiError('响应格式异常', -1, 200);
  }
  if (body.code !== 0) {
    throw new ApiError(body.message || '请求失败', body.code, 200);
  }
  return body.data;
}

// ---------------------------------------------------------------------------
// 类型定义（与后端 snake_case 字段严格对齐）
// ---------------------------------------------------------------------------
export type QType = 'single' | 'multiple' | 'judge' | 'unknown';

export type AnomalyType =
  | 'missing_answer'
  | 'missing_options'
  | 'too_few_options'
  | 'empty_stem'
  | 'duplicate_number'
  | 'parse_error';

export type QuestionStatus = 'pending' | 'published';

export type ImportStatus = 'parsing' | 'pending_confirm' | 'committed' | 'failed';

export type FileStatus = 'parsed' | 'failed';

export interface Anomaly {
  id: number;
  question_id: number;
  type: AnomalyType;
  detail: string;
  handled: number;
  created_at?: string | null;
}

export interface Question {
  id: number;
  import_file_id: number;
  question_no: string;
  qtype: QType;
  stem: string;
  options: Record<string, string>;
  answer: string;
  analysis: string;
  status: QuestionStatus;
  created_at?: string | null;
  updated_at?: string | null;
  anomalies: Anomaly[];
  filename?: string | null;
}

export interface Highlight {
  stem: string;
  options: Record<string, string>;
  answer: string;
  analysis: string;
}

export type SearchItem = Question & { highlight: Highlight };

export interface SearchResult {
  items: SearchItem[];
  total: number;
  took_ms: number;
  page: number;
  size: number;
}

export interface SearchQuery {
  q?: string;
  type?: QType | '';
  only_anomaly?: boolean;
  doc_id?: number | null;
  page?: number;
  size?: number;
}

export interface ImportFileItem {
  id: number;
  file_id: number;
  import_id?: number;
  filename: string;
  file_type?: string;
  status: FileStatus;
  error: string;
  total_questions: number;
  /** 该文件已入库题数（v1.1 起为实时聚合）。 */
  committed_questions?: number;
  /** **未处理**异常数（v1.1 起为实时聚合，与 /api/stats、/api/anomalies 一致）。 */
  anomaly_count: number;
  /** 该文件全部异常数（含已处理，实时聚合）。 */
  anomaly_total?: number;
}

export interface ImportRecord {
  id: number;
  status: ImportStatus;
  total_files: number;
  ok_files: number;
  failed_files: number;
  total_questions: number;
  committed_questions: number;
  /** 列表接口额外返回：该批次下所有文件的**未处理**异常数（实时聚合）。 */
  anomaly_count?: number;
  /** 该批次下全部异常数（含已处理，实时聚合）。 */
  anomaly_total?: number;
  created_at: string;
}

export interface ImportDetail {
  import: ImportRecord;
  files: ImportFileItem[];
}

export interface ImportListResult {
  items: ImportRecord[];
  total: number;
}

export interface UploadResult {
  import_id: number;
  files: ImportFileItem[];
}

export interface AnomalyItem {
  anomaly: Anomaly;
  question: Question;
}

export interface AnomalyListResult {
  items: AnomalyItem[];
  total: number;
  summary: { unhandled: number; handled: number };
  page: number;
  size: number;
}

export interface StatsResult {
  total_published: number;
  total_pending: number;
  anomaly_open: number;
  total_imports: number;
  total_files: number;
  total_questions: number;
}

export interface QuestionPayload {
  stem: string;
  options: Record<string, string>;
  answer: string;
  analysis: string;
  qtype?: QType;
}

export interface QuestionDetailResult {
  question: Question;
  anomalies: Anomaly[];
}

/** 题库（= 来源文档）候选，供手录页的题库名下拉使用（v1.3 新增）。 */
export interface LibraryItem {
  id: number;
  name: string;
  file_type: string;
  /** manual / json / md / docx / txt / pdf / other（后端把脏 file_type 归为 other）。 */
  kind: string;
  question_count: number;
  last_updated: string | null;
}

/** 手录请求体（线上字段为 snake_case）。 */
export interface CreateQuestionPayload {
  stem: string;
  options: Record<string, string>;
  answer: string;
  analysis: string;
  qtype: QType;
  library_name: string;
  question_no?: string | null;
}

/** 手录结果：新建题目 + 该题异常 + 所用题库（created 表示本次新建了题库）。 */
export interface CreateQuestionResult {
  question: Question;
  anomalies: Anomaly[];
  library: {
    id: number;
    name: string;
    file_type: string;
    created: boolean;
    /** 复用已有题库时后端会带回其 batch id。 */
    import_id?: number;
  };
}

/** 门禁状态（v1.4 T01）：前端启动时据此决定是否跳登录页。 */
export interface AuthStatus {
  /** 是否启用了访问门禁（后端 TIKU_ACCESS_TOKEN 非空）。 */
  enabled: boolean;
  /** 当前会话是否已通过校验；未启用门禁时恒为 true。 */
  authenticated: boolean;
}

/** 登录 / 登出结果。 */
export interface AuthActionResult {
  /** 门禁是否启用；false 表示后端未设置口令，无需登录。 */
  enabled: boolean;
}

// ---------------------------------------------------------------------------
// API 方法
// ---------------------------------------------------------------------------
export const api = {
  /** 健康检查。 */
  health(): Promise<{ status: string; fts_trigram: boolean }> {
    return request(http.get('/health'));
  },

  /** 统计面板。 */
  stats(): Promise<StatsResult> {
    return request(http.get('/stats'));
  },

  /** 全文检索。 */
  search(query: SearchQuery): Promise<SearchResult> {
    const params: Record<string, string | number | boolean> = {};
    if (query.q) {
      params.q = query.q;
    }
    if (query.type) {
      params.type = query.type;
    }
    if (query.only_anomaly) {
      params.only_anomaly = true;
    }
    if (query.doc_id) {
      params.doc_id = query.doc_id;
    }
    params.page = query.page ?? 1;
    params.size = query.size ?? 10;
    return request(http.get('/search', { params }));
  },

  /** 上传并解析多个文件（字段名 files[]）。 */
  uploadFiles(files: File[], onProgress?: (percent: number) => void): Promise<UploadResult> {
    const form = new FormData();
    files.forEach((file) => form.append('files[]', file));
    return request(
      http.post('/imports', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
        onUploadProgress: (event: AxiosProgressEvent) => {
          if (!onProgress || !event.total) {
            return;
          }
          onProgress(Math.round((event.loaded / event.total) * 100));
        },
      }),
    );
  },

  /** 导入批次列表。 */
  listImports(page = 1, size = 20): Promise<ImportListResult> {
    return request(http.get('/imports', { params: { page, size } }));
  },

  /** 导入批次详情（含文件明细）。 */
  getImport(importId: number): Promise<ImportDetail> {
    return request(http.get(`/imports/${importId}`));
  },

  /** 确认入库。 */
  commitImport(importId: number): Promise<{ committed: number }> {
    return request(http.post(`/imports/${importId}/commit`));
  },

  /** 删除导入批次。 */
  deleteImport(importId: number): Promise<{ deleted: number }> {
    return request(http.delete(`/imports/${importId}`));
  },

  /** 题目详情。 */
  getQuestion(questionId: number): Promise<QuestionDetailResult> {
    return request(http.get(`/questions/${questionId}`));
  },

  /** 编辑题目（后端会全量重建异常）。 */
  updateQuestion(questionId: number, payload: QuestionPayload): Promise<QuestionDetailResult> {
    return request(http.put(`/questions/${questionId}`, payload));
  },

  /** 删除题目。 */
  deleteQuestion(questionId: number): Promise<{ deleted: number }> {
    return request(http.delete(`/questions/${questionId}`));
  },

  /** 异常列表。 */
  listAnomalies(params: {
    type?: AnomalyType | '';
    handled?: 0 | 1 | null;
    page?: number;
    size?: number;
  }): Promise<AnomalyListResult> {
    const query: Record<string, string | number> = {
      page: params.page ?? 1,
      size: params.size ?? 10,
    };
    if (params.type) {
      query.type = params.type;
    }
    if (params.handled !== null && params.handled !== undefined) {
      query.handled = params.handled;
    }
    return request(http.get('/anomalies', { params: query }));
  },

  /** 标记异常已处理。 */
  patchAnomaly(anomalyId: number, handled: boolean): Promise<{ anomaly: Anomaly }> {
    return request(http.patch(`/anomalies/${anomalyId}`, { handled }));
  },

  /** 导出文件下载地址（浏览器直接跳转，返回文件流）。 */
  exportUrl(format: 'json' | 'md'): string {
    return `${API_BASE}/export?format=${format}`;
  },

  /** 题库名称候选（手录页下拉数据源，v1.3 新增）。 */
  listLibraries(): Promise<{ items: LibraryItem[] }> {
    return request(http.get('/libraries'));
  },

  /** 手动新增单题（题库不存在则自动新建手录库，v1.3 新增）。 */
  createQuestion(payload: CreateQuestionPayload): Promise<CreateQuestionResult> {
    return request(http.post('/questions', payload));
  },

  /** 门禁状态（v1.4 T01）：`/auth/status` 不受门禁拦截，永远可调。 */
  authStatus(): Promise<AuthStatus> {
    return request(http.get('/auth/status'));
  },

  /** 口令登录；成功后浏览器自动持有 HttpOnly 会话 Cookie。 */
  authLogin(token: string): Promise<AuthActionResult> {
    return request(http.post('/auth/login', { token }));
  },

  /** 退出登录（清除会话 Cookie）。 */
  authLogout(): Promise<AuthActionResult> {
    return request(http.post('/auth/logout'));
  },
};

export default api;
