# private-tiku 架构设计（v1.0）

私有护理题库系统：DOCX/TXT/PDF 批量导入 → 规则解析 → 异常标记 → FTS5 全文检索 → 异常校对 → 导出；单容器 Docker 私有化部署，数据全部落 `./data`。

## 1. 技术选型
| 层 | 选型 | 理由 |
|---|---|---|
| 后端 | Python 3.11 + FastAPI + uvicorn | 轻量、自带 OpenAPI、StaticFiles 可托管前端产物 |
| 数据库 | sqlite3 标准库（不引 SQLAlchemy/Alembic） | 单机内网、万级题量、零中间件；直接 SQL 可控 |
| 检索 | SQLite FTS5 `tokenize='trigram'` + `snippet()` | 中文子串匹配无需分词库；命中高亮；万级 <50ms |
| 解析 | 纯 Python 正则 + 状态机（python-docx / pdfplumber / chardet） | 可解释、可复现、无模型依赖，准确率可控 |
| 前端 | Vite + React 18 + MUI 5 + Tailwind 3 | `npm run build` 静态产物由 FastAPI 托管，单容器 |
| 部署 | docker-compose 单服务 + 多阶段 Dockerfile | node 构建前端 → python 运行；SQLite 挂载 `./data` |

## 2. 文件树
```
private-tiku/
├── docker-compose.yml      # 单服务编排，挂载 ./data，暴露端口
├── .env.example            # 环境变量样例（端口/数据目录）
├── README.md               # 部署与使用说明
├── .gitignore              # 忽略 node_modules/dist/data/__pycache__
├── backend/
│   ├── Dockerfile          # 多阶段：node:20 构建前端 → python:3.11-slim 运行
│   ├── requirements.txt    # 后端依赖（见 §8）
│   └── app/
│       ├── __init__.py
│       ├── main.py         # FastAPI 实例、路由注册、StaticFiles 托管 frontend/dist
│       ├── config.py       # 路径/端口/常量（DATA_DIR、DB_PATH、ALLOWED_EXT）
│       ├── db.py           # sqlite3 连接工厂、建表 DDL、FTS 降级检测、初始化
│       ├── models.py       # Pydantic 请求/响应 schema + 枚举常量
│       ├── api/
│       │   ├── __init__.py # APIRouter 聚合
│       │   ├── imports.py  # 上传解析/列表/详情/确认入库/删除
│       │   ├── questions.py# 题目详情/编辑/删除
│       │   ├── search.py   # 全文搜索 + 筛选 + 高亮
│       │   ├── anomalies.py# 异常列表 / 标记已处理
│       │   └── export.py   # JSON/Markdown 导出
│       ├── parser/
│       │   ├── __init__.py
│       │   ├── reader.py   # DOCX/TXT/PDF → 原始文本行（带行号/页码）
│       │   ├── normalize.py# 全半角统一、去空白空行、统一标点
│       │   ├── splitter.py # 按题号正则切分题目块
│       │   ├── extractor.py# 题干/选项/答案/解析/题型抽取
│       │   ├── validator.py# 6 类异常规则判定
│       │   └── pipeline.py # 编排 reader→normalize→splitter→extractor→validator
│       └── tests/
│           ├── test_parser.py  # 解析/异常规则单测
│           ├── test_api.py     # API 集成测试（TestClient）
│           └── fixtures/       # 样例 docx/txt/pdf
├── frontend/
│   ├── package.json        # 前端依赖与脚本
│   ├── vite.config.ts      # 构建配置，base '/'，dev 代理 /api
│   ├── tailwind.config.js  # Tailwind 扫描 src
│   ├── postcss.config.js   # postcss 插件
│   ├── index.html          # SPA 入口
│   ├── tsconfig.json       # TS 配置
│   └── src/
│       ├── main.tsx        # React 挂载 + ThemeProvider + Router
│       ├── App.tsx         # 路由表与整体布局
│       ├── api/client.ts   # axios 封装，baseURL /api，统一解包与报错
│       ├── pages/
│       │   ├── SearchPage.tsx  # 首页：搜索框/筛选/结果高亮
│       │   ├── ImportPage.tsx  # 拖拽上传/导入列表/预览确认入库
│       │   ├── AnomalyPage.tsx # 异常列表/筛选/标记已处理
│       │   ├── QuestionDetailPage.tsx # 题目详情与编辑
│       │   └── StatsPage.tsx   # 题库总量/异常数/导入统计
│       └── components/
│           ├── Layout.tsx      # 侧边导航 + 内容区
│           ├── QuestionCard.tsx# 搜索结果卡片（题型/题干/选项/答案/异常标签）
│           ├── Highlight.tsx   # 安全渲染 <mark> 高亮片段
│           ├── UploadDropzone.tsx # 多文件拖拽/选择上传
│           ├── FilterBar.tsx   # 题型/仅异常/文档筛选
│           └── StatusChip.tsx  # 状态/异常类型彩色徽标
```

## 3. 数据库 DDL
```sql
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS imports (            -- 一次批量导入
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  status TEXT NOT NULL DEFAULT 'parsing',       -- parsing|pending_confirm|committed|failed
  total_files INTEGER NOT NULL DEFAULT 0,
  ok_files INTEGER NOT NULL DEFAULT 0,
  failed_files INTEGER NOT NULL DEFAULT 0,
  total_questions INTEGER NOT NULL DEFAULT 0,
  committed_questions INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS import_files (       -- 单个文件
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  import_id INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
  filename TEXT NOT NULL,
  file_type TEXT NOT NULL,                       -- docx|txt|pdf
  status TEXT NOT NULL DEFAULT 'parsed',         -- parsed|failed
  error TEXT NOT NULL DEFAULT '',
  total_questions INTEGER NOT NULL DEFAULT 0,
  anomaly_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS questions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  import_file_id INTEGER NOT NULL REFERENCES import_files(id) ON DELETE CASCADE,
  question_no TEXT NOT NULL,                     -- 原文档题号
  qtype TEXT NOT NULL,                           -- single|multiple|judge|unknown
  stem TEXT NOT NULL,
  options_json TEXT NOT NULL DEFAULT '{}',       -- {"A":"...","B":"..."}
  answer TEXT NOT NULL DEFAULT '',               -- 单选"A"/多选"ABD"/判断"√"|"×"
  analysis TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',        -- pending|published
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_q_file   ON questions(import_file_id);
CREATE INDEX IF NOT EXISTS idx_q_status ON questions(status);
CREATE INDEX IF NOT EXISTS idx_q_type   ON questions(qtype);
CREATE TABLE IF NOT EXISTS anomalies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  type TEXT NOT NULL,                            -- 见 §6 异常枚举
  detail TEXT NOT NULL DEFAULT '',
  handled INTEGER NOT NULL DEFAULT 0,            -- 0 未处理 / 1 已处理
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_a_q     ON anomalies(question_id);
CREATE INDEX IF NOT EXISTS idx_a_state ON anomalies(type, handled);

-- FTS5 外部内容表：列与 questions 对齐，content_rowid 关联主键
CREATE VIRTUAL TABLE IF NOT EXISTS questions_fts USING fts5(
  stem, options_json, answer, analysis,
  content='questions', content_rowid='id', tokenize='trigram'
);
-- 三触发器：FTS 与 questions 自动同步
CREATE TRIGGER IF NOT EXISTS q_ai AFTER INSERT ON questions BEGIN
  INSERT INTO questions_fts(rowid, stem, options_json, answer, analysis)
  VALUES (new.id, new.stem, new.options_json, new.answer, new.analysis);
END;
CREATE TRIGGER IF NOT EXISTS q_ad AFTER DELETE ON questions BEGIN
  INSERT INTO questions_fts(questions_fts, rowid, stem, options_json, answer, analysis)
  VALUES ('delete', old.id, old.stem, old.options_json, old.answer, old.analysis);
END;
CREATE TRIGGER IF NOT EXISTS q_au AFTER UPDATE ON questions BEGIN
  INSERT INTO questions_fts(questions_fts, rowid, stem, options_json, answer, analysis)
  VALUES ('delete', old.id, old.stem, old.options_json, old.answer, old.analysis);
  INSERT INTO questions_fts(rowid, stem, options_json, answer, analysis)
  VALUES (new.id, new.stem, new.options_json, new.answer, new.analysis);
END;
```
**FTS 降级策略**：`db.py` 建 FTS 表时 try/except——SQLite 不支持 `trigram`（<3.34）则改 `tokenize='unicode61'` 并置全局标志 `FTS_TRIGRAM=False`；搜索层据标志决定用 `MATCH` 还是 `LIKE`。**trigram 限制**：`MATCH` 需 ≥3 字符；<3 字符（如"房颤"）退化为 `WHERE stem LIKE '%q%' OR options_json LIKE ...`（万级 LIKE 仍 <100ms）。多关键词：`"kw1" AND "kw2"`。

## 4. API 设计（统一响应 `{code,message,data}`，snake_case）
| 方法 | 路径 | 说明 | 请求 | 响应 data |
|---|---|---|---|---|
| POST | /api/imports | multipart 多文件上传→同步解析→暂存待确认 | form `files[]` | `{import_id, files:[{file_id,filename,status,error,total_questions,anomaly_count}]}` |
| GET | /api/imports | 导入记录列表 | `?page=&size=` | `{items:[import],total}` |
| GET | /api/imports/{id} | 导入详情（含文件明细） | - | `{import, files:[...]}` |
| POST | /api/imports/{id}/commit | 确认入库（pending→published） | - | `{committed}` |
| DELETE | /api/imports/{id} | 删除导入及其题目/异常（级联） | - | `{deleted}` |
| GET | /api/search | 全文搜索 + 筛选 + 高亮 | `?q=&type=&only_anomaly=&doc_id=&page=&size=` | `{items:[question+highlight],total,took_ms}` |
| GET | /api/questions/{id} | 题目详情（含异常列表） | - | `{question, anomalies}` |
| PUT | /api/questions/{id} | 编辑题目并重算异常 | `{stem,options,answer,analysis,qtype}` | `{question, anomalies}` |
| DELETE | /api/questions/{id} | 删除题目（触发 FTS 同步删除） | - | `{deleted}` |
| GET | /api/anomalies | 异常列表 | `?type=&handled=&page=&size=` | `{items:[{anomaly,question}],total}` |
| PATCH | /api/anomalies/{id} | 标记异常已处理 | `{handled:true}` | `{anomaly}` |
| GET | /api/export | 导出题库 | `?format=json\|md` | 文件流（Content-Disposition） |
| GET | /api/stats | 统计面板 | - | `{total_published,total_pending,anomaly_open,total_imports}` |
| GET | /api/health | 健康检查 | - | `{status:'ok'}` |
**错误码**：`0` 成功；`40001` 参数错误；`40401` 资源不存在；`42201` 文件类型不支持；`50001` 解析失败。HTTP 状态码配合（400/404/422/500），body 仍为统一结构。
## 5. 解析管线（输入输出）
```
Reader.read(path) -> list[Line(seq:int, text:str, page:int|None)]
  按扩展名分发：python-docx 取段落；pdfplumber 取每页文本行；chardet 探测编码后按行读
normalize(lines) -> list[Line]   # 全角→半角、去首尾空白、去空行、统一（ ）．等标点
splitter.split(lines) -> list[Block(no_text:str, lines:list[Line])]
  题号正则 ^\s*(?:第?\s*)?(\d{1,4})\s*[.、．。)）]\s* 命中即开新块
extractor.extract(block) -> Parsed(no, stem, options:dict, answer, analysis, qtype)
  · 首行去题号→题干；题干内联选项用 "A. xxx B. xxx" 拆分
  · 选项行正则 ^[A-H][.、．)）]\s* 逐行收集
  · 答案行 (?:参考)?答案[:：]?\s*(\S+)；解析行 解析[:：]?\s*(.+)
validator.validate(parsed, no_counter) -> list[Anomaly(type, detail)]
pipeline.run(file) -> list[(Parsed, list[Anomaly])]   # 供 imports.py 落库
```

## 6. 题型判定与异常规则
**题型判定（按优先级）**

| 条件 | qtype |
|---|---|
| 无选项且答案 ∈ {对,错,正确,错误,√,×,T,F} | `judge` |
| 答案含 ≥2 个字母 | `multiple` |
| 答案恰 1 个字母（A-H） | `single` |
| 其他无法识别 | `unknown` |

**异常规则（枚举固定）**

| type | 判定规则 |
|---|---|
| `missing_answer` | answer 为空 |
| `missing_options` | qtype∈{single,multiple} 且选项数=0 |
| `too_few_options` | qtype∈{single,multiple} 且 0<选项数<4 |
| `empty_stem` | 题干去空白后长度 <3 |
| `duplicate_number` | 同一文件内题号重复（Counter>1） |
| `parse_error` | 切分/抽取抛异常或无题号可识别 |

**答案规范化**：多选字母升序大写；判断统一为 `√`/`×`（对/正确/T/√→√，错/错误/F/×→×）。

## 7. 任务分解
| 任务 | 标题 | 依赖 | 涉及文件 | 验收点 |
|---|---|---|---|---|
| T01 | 项目基础设施与骨架 | - | docker-compose.yml、.env.example、.gitignore、README.md、backend/Dockerfile、requirements.txt、app/main.py、app/config.py、frontend 全套基座（package.json/vite.config.ts/tailwind.config.js/postcss.config.js/index.html/tsconfig.json/src/main.tsx/src/App.tsx/src/api/client.ts/src/components/Layout.tsx） | `docker compose up -d` 启动；/api/health 返回 ok；StaticFiles 托管可访问；npm build 成功 |
| T02 | 数据层与解析引擎 | T01 | app/db.py、app/models.py、app/parser/__init__.py、reader.py、normalize.py、splitter.py、extractor.py、validator.py、pipeline.py、tests/test_parser.py、tests/fixtures/ | 建表+触发器幂等；三格式解析出结构化题目；6 类异常规则单测通过 |
| T03 | 后端 API | T02 | app/api/__init__.py、imports.py、questions.py、search.py、anomalies.py、export.py、main.py（注册路由） | curl 全链路：上传→列表→详情→commit→search(高亮/筛选)→题目编辑→异常列表/标记→导出；错误码规范 |
| T04 | 前端页面与组件 | T01（可与 T02/T03 并行） | src/pages/SearchPage.tsx、ImportPage.tsx、AnomalyPage.tsx、QuestionDetailPage.tsx、StatsPage.tsx、src/components/QuestionCard.tsx、Highlight.tsx、UploadDropzone.tsx、FilterBar.tsx、StatusChip.tsx | 4+ 页面可用；上传/搜索结果高亮/异常校对/详情编辑闭环；MUI+Tailwind 统一 |
| T05 | QA 测试与端到端验收 | T02、T03、T04 | app/tests/test_api.py、tests/fixtures/、修复缺陷（跨 T02–T04 文件） | 解析准确率抽检 ≥90%；API 全用例通过；docker 一键部署端到端演示（诱导异常→修复→导出）；性能：万题搜索 <1s |

## 8. 依赖清单与共享约定
**backend/requirements.txt**
```
fastapi==0.111.0
uvicorn[standard]==0.30.1
python-multipart==0.0.9
python-docx==1.1.2
pdfplumber==0.11.0
chardet==5.2.0
pydantic==2.7.4
pytest==8.2.2
httpx==0.27.0
```

**frontend/package.json dependencies**
```
react 18.3.1 · react-dom 18.3.1 · react-router-dom 6.24.0 · @mui/material 5.16.0 ·
@mui/icons-material 5.16.0 · @emotion/react 11.11.4 · @emotion/styled 11.11.5 · axios 1.7.2
devDependencies: vite 5.3.3 · @vitejs/plugin-react 4.3.1 · typescript 5.5.3 · tailwindcss 3.4.4 ·
postcss 8.4.39 · autoprefixer 10.4.19 · @types/react 18.3.3 · @types/react-dom 18.3.0
```

**跨文件共享约定**
- 字段一律 `snake_case`；API 响应统一 `{code:0, message:"ok", data:{...}}`。
- 题目 JSON：`{id, import_file_id, question_no, qtype, stem, options:{"A":".."}, answer, analysis, status, anomalies:[{type,detail,handled}]}`。
- 题型枚举：`single|multiple|judge|unknown`；异常枚举：`missing_answer|missing_options|empty_stem|duplicate_number|too_few_options|parse_error`；题目状态：`pending|published`；导入状态：`parsing|pending_confirm|committed|failed`。
- 只检索 `status='published'` 题目；上传仅接受 `.docx/.txt/.pdf`，落盘名 `{uuid}{ext}`，原文件名存库。
- 前端所有请求走 `src/api/client.ts`（baseURL `/api`），错误统一 toast；高亮片段由后端转义后以 `<mark>` 包裹，前端 `Highlight.tsx` 安全渲染（禁直插未转义内容）。
- 异常翻新：`PUT /questions/{id}` 后按最新内容**全量重建**该题 anomalies，避免脏数据残留。

**待明确事项**
1. 题量上限按万级设计；十万级需迁移独立搜索引擎（接口已隔离）。
2. PDF 扫描件 OCR 不在本期（文本型 PDF 之外走 `parse_error`/"无可提取文本"）。
3. "缺选项"阈值定为 <4（护理选择题通常四选项），如需按题型区分可后续调整。
4. 无答案但有解析：正常入库并标 `missing_answer`，不阻断。
5. 权限/多用户未做（单管理员内网模式）；如需公网暴露需前置反代鉴权。
