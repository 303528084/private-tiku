# private-tiku 实现说明（T01–T04）

> 本文档记录**实际落地情况**：文件清单、启动命令、依赖版本决策、真实验证输出、与架构文档的偏离和已知限制。
> 架构依据：`docs/ARCHITECTURE.md`（v1.0）；需求依据：`docs/PRD.md`。
> 实现者：工程师 寇豆码（software-engineer）

---

## 1. 结论速览

| 项目 | 结果 |
|---|---|
| 交付任务 | T01 基础设施 · T02 数据层与解析引擎 · T03 后端 API · T04 前端页面与组件 |
| 单测（解析引擎 + 6 类异常规则） | **59 passed**（`pytest app/tests/test_parser.py -q`） |
| 前端类型检查 + 构建 | **成功**（`tsc --noEmit && vite build`，998 modules，2.38s） |
| 端到端冒烟（真实 uvicorn + curl） | **35 项检查全通过，FAIL=0** |
| 额外验证（筛选 / 删除 / 级联 / FTS 同步 / 错误码） | **全部符合预期**（见 §5.3） |
| 全局一致性审查 | **IS_PASS: YES** |
| 产出文件数 | **57 个手写文件**（+ 1 个 npm 生成的 `package-lock.json`） |
| **v1.1 缺陷修复（QA 第一轮，D1/D2/D3）** | **已全部修复**：准确率 96.0% → **100.0%**；102 passed + 3 xpassed；冒烟 35/35；万题 17ms（详见 §9） |

---

## 2. 文件清单（实际磁盘路径）

根目录 `C:/Users/Stone/Desktop/题库/private-tiku/`

### 2.1 项目根 / 部署（5）

| 文件 | 说明 |
|---|---|
| `docker-compose.yml` | 单服务编排，`./data` 挂载，端口 `${TIKU_PORT:-8000}`，含 healthcheck |
| `.env.example` | `TIKU_PORT` / `TIKU_DATA_DIR` 样例 |
| `.gitignore` | 忽略 `node_modules` `dist` `data` `__pycache__` `*.db` 等 |
| `.dockerignore` | 缩减构建上下文（额外文件，见 §6.4） |
| `README.md` | 部署与使用说明（含依赖版本决策说明） |

### 2.2 后端（28）

| 文件 | 说明 |
|---|---|
| `backend/Dockerfile` | 多阶段：`node:20-alpine` 构建前端 → `python:3.11-slim` 运行 |
| `backend/requirements.txt` | 依赖约束区间（见 §4） |
| `backend/pytest.ini` | `testpaths=app/tests` |
| `backend/app/__init__.py` | 包声明 + 版本 |
| `backend/app/main.py` | FastAPI 实例、lifespan 初始化、`/api` 挂载、StaticFiles + SPA 回退 |
| `backend/app/config.py` | `BASE_DIR/DATA_DIR/DB_PATH/UPLOAD_DIR/FRONTEND_DIST/ALLOWED_EXT` 等常量 |
| `backend/app/db.py` | 连接工厂、DDL、FTS5 trigram 降级、行序列化、批量取异常 |
| `backend/app/models.py` | 枚举、错误码、`ApiError`、`ok/fail`、请求 schema |
| `backend/app/api/__init__.py` | 路由聚合 + `/health` `/stats` + 统一异常处理器 |
| `backend/app/api/imports.py` | 上传解析 / 列表 / 详情 / commit / 删除 |
| `backend/app/api/questions.py` | 详情 / 编辑（异常全量重建）/ 删除 |
| `backend/app/api/search.py` | FTS5 MATCH + LIKE 降级 + 筛选 + 服务端 `<mark>` 高亮 |
| `backend/app/api/anomalies.py` | 异常列表（含题目快照 + 汇总）/ 标记已处理 |
| `backend/app/api/export.py` | JSON / Markdown 文件流 |
| `backend/app/parser/__init__.py` | 解析管线导出 |
| `backend/app/parser/reader.py` | DOCX/TXT/PDF → `Line(seq, text, page)`，chardet 编码探测 |
| `backend/app/parser/normalize.py` | 全半角统一、去空白空行、标点统一 |
| `backend/app/parser/splitter.py` | 题号正则切块、题号归一化、无题号伪块 |
| `backend/app/parser/extractor.py` | 题干/选项/答案/解析/题型抽取 + 答案规范化 |
| `backend/app/parser/validator.py` | 6 类异常规则判定 |
| `backend/app/parser/pipeline.py` | `run(path)` / `run_lines(lines)` 编排 |
| `backend/app/tests/__init__.py` | 测试包 |
| `backend/app/tests/test_parser.py` | 59 项单测 |
| `backend/app/tests/fixtures/sample_basic.txt` | 7 题样本（含 1 题缺答案、2 处重复题号、1 题选项不足） |
| `backend/app/tests/fixtures/sample_messy.txt` | 无题号文档（验证 `parse_error`） |
| `backend/app/tests/fixtures/sample_gbk.txt` | GBK 编码 2 题（验证 chardet） |
| `backend/app/tests/fixtures/sample_questions.docx` | python-docx 生成的 3 题 |
| `backend/app/tests/fixtures/sample_questions.pdf` | 手写最小 PDF 字节流，2 题（中文可提取） |
| `backend/scripts/gen_fixtures.py` | fixture 生成脚本（含 PDF 中文方案失败时的 ASCII 回退） |
| `backend/scripts/smoke_test.sh` | 端到端冒烟脚本（自带启停 uvicorn + 临时数据目录） |

> 注：上表 28 行含 5 个 fixtures 文件。

### 2.3 前端（24）

| 文件 | 说明 |
|---|---|
| `frontend/package.json` | 依赖与脚本（`build = tsc --noEmit && vite build`） |
| `frontend/vite.config.ts` | `base '/'`、dev 代理 `/api` → 8000 |
| `frontend/tsconfig.json` | strict + `noUnusedLocals` + `jsx: react-jsx` |
| `frontend/tailwind.config.js` | 扫描 `src`，`preflight: false`（避免与 MUI 冲突） |
| `frontend/postcss.config.js` | tailwindcss + autoprefixer |
| `frontend/index.html` | SPA 入口 |
| `frontend/src/index.css` | Tailwind 指令 + `mark` 高亮样式 + 选项网格 |
| `frontend/src/main.tsx` | React 挂载 + ThemeProvider + CssBaseline + BrowserRouter |
| `frontend/src/App.tsx` | 路由表（5 页 + 404） |
| `frontend/src/api/client.ts` | axios 封装、类型定义、`api.*` 方法（唯一请求出口） |
| `frontend/src/components/Layout.tsx` | 侧边导航 + 顶部栏 + 概览统计 |
| `frontend/src/components/QuestionCard.tsx` | 结果卡片（题型/题干/选项/答案/异常） |
| `frontend/src/components/Highlight.tsx` | `<mark>` 白名单安全渲染（DOM 清洗 + 文本重转义） |
| `frontend/src/components/UploadDropzone.tsx` | 多文件拖拽/选择、类型与体积校验 |
| `frontend/src/components/FilterBar.tsx` | 题型 / 仅异常 / 来源文档 |
| `frontend/src/components/StatusChip.tsx` | 题型/异常/题目状态/导入状态/文件状态徽标 |
| `frontend/src/pages/SearchPage.tsx` | 搜索 + 筛选 + 高亮 + 分页 + took_ms |
| `frontend/src/pages/ImportPage.tsx` | 上传 + 进度 + 批次表格 + 预览弹窗 + 入库/删除 |
| `frontend/src/pages/AnomalyPage.tsx` | 异常筛选 + 逐条标记已处理 |
| `frontend/src/pages/QuestionDetailPage.tsx` | 题干/选项/答案/解析/题型编辑 + 删除 + 异常列表 |
| `frontend/src/pages/StatsPage.tsx` | 统计卡片 + 批次明细 + 导出入口 |

（前端另有 `package-lock.json`，由 `npm install` 生成，未计入手写文件数。）

### 2.4 文档（1）

| 文件 | 说明 |
|---|---|
| `docs/IMPLEMENTATION.md` | 本文件 |

> `docs/PRD.md`、`docs/ARCHITECTURE.md` **未作任何修改**。

---

## 3. 启动命令（已验证）

### 3.1 本地

```bash
# 依赖（本机 Python 3.13.14）
python -m pip install -r backend/requirements.txt

# 前端构建（产物由 FastAPI 托管）
cd frontend && npm install && npm run build && cd ..

# 启动后端（工作目录必须是 backend/）
cd backend && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# → http://127.0.0.1:8000          前端
# → http://127.0.0.1:8000/docs     接口文档
# → http://127.0.0.1:8000/api/health
```

### 3.2 Docker（未在本机启动，按任务要求只做配置交付）

```bash
cp .env.example .env
docker compose up -d --build
```

---

## 4. 依赖版本决策（重要偏离说明）

**架构 §8 给的示例是全 `==` 死锁版本，其中 `pydantic==2.7.4` 在 Python 3.13 上无法安装**（`pydantic-core` 无 cp313 wheel，需源码编译 Rust，失败）。

实际 `backend/requirements.txt`：

```
fastapi>=0.115,<1.0
uvicorn[standard]>=0.30,<1.0
python-multipart>=0.0.9
python-docx>=1.1,<2.0
pdfplumber>=0.11,<1.0
chardet>=5.2,<6.0
pydantic>=2.9,<3.0
pytest>=8.2,<9.0
httpx>=0.27,<1.0
```

安装实测解析到的版本（Python 3.13.14 / Windows）：

```
fastapi 0.162.5 · starlette 1.10.0 · pydantic 2.14.3 · pydantic-core 2.50.4
uvicorn 0.54.0 · python-docx 1.2.0 · pdfplumber 0.11.10 · pdfminer.six 20260107
chardet 5.2.0 · python-multipart 0.0.33 · pytest 8.4.2 · httpx 0.28.1
```

容器内为 `python:3.11-slim`，同一份 requirements 会解析到 3.11 对应的兼容版本；
`Dockerfile` 未额外锁死版本，保持与本地一致，避免"本地能跑、容器跑不了"。

前端依赖按架构 §8 的版本区间声明（React 18.3.1 / MUI 5.16 / Vite 5.3 / Tailwind 3.4 / TS 5.5），
实际安装 209 个包，构建通过。

---

## 5. 验证记录（真实输出摘要）

### 5.1 解析引擎单测

```
$ cd backend && python -m pytest app/tests/test_parser.py -q
...........................................................              [100%]
59 passed in 0.28s
```

覆盖：normalize（全半角/空行/空格压缩/中文句号保留）、splitter（题号识别 6 组参数化、伪块）、
extractor（标准块 / 题干内联选项 / 单行题 / 判断题 / 括号选项 / 续行合并 / parse_error）、
validator（6 类异常逐条 + 顺序 + 判断题不产生选项异常）、reader（TXT-UTF8 / TXT-GBK / DOCX / PDF /
非法扩展名 / 空文件）、pipeline（TXT 7 题 4 异常 / 无题号 / DOCX 3 题 / PDF 2 题 / GBK 2 题 / 行数统计）。

### 5.2 端到端冒烟（`bash backend/scripts/smoke_test.sh`）

```
$ bash scripts/smoke_test.sh
== 0. 启动后端（临时数据目录）==
== 1. GET /api/health ==
  {"code":0,"message":"ok","data":{"status":"ok","fts_trigram":true}}
== 2. POST /api/imports（3 个文件：txt + docx + 无题号 txt）==
  {"code":0,...,"data":{"import_id":1,"files":[
     {"file_id":1,"filename":"sample_basic.txt","status":"parsed","total_questions":7,"anomaly_count":4},
     {"file_id":2,"filename":"sample_questions.docx","status":"parsed","total_questions":3,"anomaly_count":0},
     {"file_id":3,"filename":"sample_messy.txt","status":"parsed","total_questions":1,"anomaly_count":2}]}}
  import_id=1  文件数=3  题目总数=11
== 3. GET /api/imports/{id} ==     [PASS] status=pending_confirm / 文件明细 / 异常计数
== 4. POST /api/imports/{id}/commit ==     [PASS] 入库题数 = 11
== 5. GET /api/search?q=房颤 ==
  "options":{"A":"<mark>房颤</mark>",...}  "analysis":"...(略)心<mark>房颤</mark>动。"  "total":2,"took_ms":3
== 5b. GET /api/search?q=脉搏短绌 心律失常（≥3 字 → FTS5 MATCH）==  [PASS] total=1
== 6. GET /api/search?q=房颤&type=multiple ==  [PASS] total=0
== 7. GET /api/anomalies?handled=0 ==
  [PASS] 含 missing_answer / duplicate_number / parse_error   未处理异常总数=6
== 8. PATCH /api/anomalies/5 ==
  {"code":0,...,"handled":1}
== 9. PUT /api/questions/11（补答案并重算异常）==
  {"code":0,...,"answer":"A",...,"anomalies":[]}
== 10. GET /api/export?format=md ==
  [PASS] Content-Disposition: attachment / text/markdown / 正文含「# 私有护理题库导出」
  导出文件大小：2860 字节
== 11. GET /api/export?format=json ==  [PASS] 含 "items"
== 12. GET /api/stats ==
  {"total_published":11,"total_pending":0,"anomaly_open":4,"total_imports":1,"total_files":3,"total_questions":11}
== 13. GET /（StaticFiles 托管前端）==   HTTP 200，返回含 <div id="root"> 的 SPA HTML
== 14. 404 规范 ==
  {"code":40401,"message":"题目 999999 不存在","data":null}

===================================================
冒烟测试结果：PASS=35  FAIL=0
===================================================
```

### 5.3 额外验证（TestClient，覆盖冒烟脚本未覆盖的路径）

```
upload: 0 files: [('sample_basic.txt','parsed',7,4), ('sample_questions.pdf','parsed',2,0)]
commit: {'committed': 9}
search only_anomaly total: 4 ids: [4,5,6,7] all have anomalies: True
search doc_id=2 type=single total: 2 files: {'sample_questions.pdf'}
delete question: {'deleted': 1}
only_anomaly total after delete: 3          ← FTS 与异常同步正确
search after delete still works, total: 1
delete import: {'deleted': 1, 'deleted_files': 2, 'deleted_questions': 8}
stats after delete: {'total_published':0,'total_pending':0,'anomaly_open':0,'total_imports':0,'total_files':0,'total_questions':0}
search after import delete total: 0
anomalies after delete: 0                   ← 级联删除干净
404 check: {'code':40401,'message':'导入记录 999 不存在','data':None}
422 check(code): {'code':42201,'message':'全部文件类型不受支持，仅接受 .docx/.txt/.pdf','data':{...}}
```

### 5.4 前端构建

```
$ cd frontend && npm install --no-audit --no-fund
added 209 packages in 1m

$ npm run build
> tsc --noEmit && vite build
vite v5.4.21 building for production...
✓ 998 modules transformed.
dist/index.html                   0.47 kB │ gzip:   0.38 kB
dist/assets/index-BoA6bceR.css    3.86 kB │ gzip:   1.02 kB
dist/assets/index-DBLTAw6Z.js   510.72 kB │ gzip: 161.56 kB
✓ built in 2.38s
```

### 5.5 路由与接口契约核对（`app.openapi()`）

```
/api/anomalies                    ['get']
/api/anomalies/{anomaly_id}       ['patch']
/api/export                       ['get']
/api/export/download              ['get']        ← 额外辅助端点
/api/health                       ['get']
/api/imports                      ['get', 'post']
/api/imports/{import_id}          ['delete', 'get']
/api/imports/{import_id}/commit   ['post']
/api/questions/{question_id}      ['delete', 'get', 'put']
/api/search                       ['get']
/api/stats                        ['get']
```

与架构 §4 表格**逐条对齐**（唯一新增为 `/api/export/download`，辅助前端获取导出 URL）。

---

## 6. 与架构文档的偏离及原因

### 6.1 依赖版本（必改，已获任务方指示）

见 §4。`requirements.txt` 由全 `==` 改为区间约束，否则 Python 3.13 装不上。

### 6.2 新增 `backend/scripts/`（2 个脚本）

架构文件树未列脚本目录。新增原因：
- `gen_fixtures.py`：pdfplumber 只能读不能写，PDF fixture 必须由脚本生成（手写最小 PDF 字节流）；
  同时用 python-docx 生成 DOCX、GBK 编码写入 TXT，保证 fixture 可复现。
- `smoke_test.sh`：把 T03 的 curl 全链路验证固化为可重复执行、自动清理的脚本，供 QA 复用。

### 6.3 新增 `backend/pytest.ini`、`backend/app/tests/__init__.py`

前者固定 `testpaths` 让 `python -m pytest` 在 `backend/` 下开箱可跑；后者使 `from app.parser import ...`
在 pytest 收集时可用（`rootdir` 插入 `backend/`）。

### 6.4 新增 `.dockerignore`

避免把 `node_modules`（200+ MB）与 `data/` 塞进构建上下文。属工程必需，不影响架构约定。

### 6.5 `/health` 与 `/stats` 放在 `app/api/__init__.py`

架构 §2 文件树没有 `system.py`，§4 却要求这两个端点。为保持文件树与文档一致，直接在聚合模块
注册，不新增文件。

### 6.6 若干实现细节决策（未改变契约）

| 点 | 决策 | 理由 |
|---|---|---|
| 高亮实现 | 用 FTS/LIKE 做**召回**，高亮在 Python 侧「先 `html.escape` 再按命中区间包 `<mark>`」 | `snippet()` 在 trigram 分词器下 token 语义不稳定，易切出破碎片段；自实现可保证「先转义后加标记」的安全顺序与多关键词合并 |
| 全角标点 | U+FF01–FF5E 统一转半角（含 `？：，．（）`），中文句号 `。` 保留 | 遵循 §5「全半角统一、统一标点」；`。` 保留可避免题干句末被误判为题号行 |
| 题号归一化 | `003` → `3` 后再做重复统计 | 否则 `3` 与 `03` 统计不出重复 |
| 不支持类型的处理 | 单个文件不支持 → 该文件记 `failed` 并说明原因；**全部**文件都不支持 → HTTP 422 + `42201` | 同时满足 PRD P0-1「失败文件单独列出」与架构的错误码规范 |
| 二次上传同一文件 | 文件名落盘 `{uuid}{ext}`，原始名入库；文件放在 `uploads/import_{id}/` | §8 要求 uuid 落盘；按批次分目录使 `DELETE /api/imports/{id}` 能连带清理文件（DDL 无 path 列） |
| 删除批次 | 显式 `DELETE FROM questions` 再删文件/批次 | SQLite 的 `ON DELETE CASCADE` **不会**触发 `AFTER DELETE` 触发器，显式删除才能让 FTS 同步清理 |
| 搜索 query 规范化 | 对 `q` 先跑 `normalize_text` | 用户输入全角字符也能命中库内半角文本 |
| `anomaly_count` | 列表接口用子查询聚合 `import_files.anomaly_count` | 让「批次明细」页能直接展示异常数，不新增表字段 |

---

## 7. 已知限制

1. **扫描件 PDF 不支持**：无文本层的 PDF → `ReaderError("PDF 无可提取文本…")`，该文件记为 `failed`，不阻塞同批次其他文件。
2. **短词检索退化**：trigram 要求 ≥3 字符；`房颤`（2 字）走 `LIKE`（实测 `took_ms=3`）。万级仍 <100ms，十万级需换检索引擎。
3. **无鉴权**：单管理员内网模式，公网暴露需前置反向代理鉴权。
4. **PDF 中文依赖 ToUnicode**：fixture 用手写 Type0 + Identity-H + 自定义 ToUnicode CMap，实测 pdfplumber 可正确提取中文；
   但真实文档若字体缺 ToUnicode，可能提取出 `(cid:xx)` —— 属 PDF 源文件问题，非解析器缺陷。
5. **`data/` 不自动 VACUUM**：大批量删除后 SQLite 文件不收缩。
6. **上传失败仍留批次记录**：全部文件类型不支持时，`imports` 表会留下一条 `failed` 记录（便于溯源），前端列表可见。
7. **`python-docx` 只读段落与表格**，图片/文本框内文字不提取。
8. **Docker 镜像未在本机实际构建**（任务明确不要求），`Dockerfile` / `compose` 的路径映射已按
   `config.BASE_DIR = /app` 推导核对：`COPY --from=web /web/dist /app/frontend/dist` 与
   `WORKDIR /app/backend` 匹配 `FRONTEND_DIST=/app/frontend/dist`。

---

## 8. 全局一致性审查（IS_PASS: YES）

| 检查项 | 结果 | 证据 |
|---|---|---|
| 前端请求路径 ↔ 后端路由 | ✅ | `client.ts` 14 处调用全部命中 `openapi()` 中的 11 个路径（baseURL `/api`） |
| 响应外壳 `{code,message,data}` | ✅ | 成功/参数错误/404/422/500 全部走统一结构（含 `RequestValidationError` 与兜底 `Exception` 处理器） |
| 字段 snake_case | ✅ | `question_no / import_file_id / took_ms / only_anomaly / doc_id / anomaly_count / qtype` |
| 枚举值一致 | ✅ | 题型 4 值、异常 6 值、题目状态 2 值、导入状态 4 值：后端 `models.py` ↔ 前端 `client.ts` ↔ `StatusChip.tsx` 三处逐一对应 |
| 题目 JSON 结构 | ✅ | §8 约定字段（`id/import_file_id/question_no/qtype/stem/options/answer/analysis/status/anomalies`）全部由 `db.question_to_dict` 输出，搜索项在此基础上追加 `highlight` 与 `filename` |
| 高亮安全 | ✅ | 后端 `html.escape` → `<mark>`；前端 `Highlight.tsx` 用 DOMParser 白名单清洗并重转义文本节点 |
| 数据库 DDL | ✅ | 与架构 §3 逐字段一致；仅额外用了 `CREATE INDEX idx_if_import`（性能用，不改结构） |
| FTS 同步 | ✅ | 三触发器 + 启动 `rebuild`；删除题目/批次后搜索与异常统计立即归零（§5.3 实测） |
| 解析管线签名 | ✅ | `reader.read → normalize → splitter.split → extractor.extract → validator.validate → pipeline.run`，与 §5 一致 |
| 循环依赖 / 未使用导入 | ✅ | `tsc --noEmit`（strict + noUnusedLocals）通过；Python 侧无循环 import（`api/__init__` → 子模块 → `db/models` 单向） |

**结论：IS_PASS: YES**，可交付 QA（T05）。

---

# v1.1 缺陷修复（QA 第一轮验收回派）

QA 第一轮结论：T05 PASS（准确率 96%、91 passed/0 failed、万题 17ms、e2e 28/28），但确证 3 条源码缺陷。
本节记录修复内容、证据与新的偏离。**未改动** QA 的测试文件（`test_api.py` / `test_accuracy.py` / `test_known_issues.py`），
也**未改动** `docs/PRD.md` 与 `docs/ARCHITECTURE.md`。

## 9.1 D3（严重）：题号 `（79）` / `第80题` 形态不识别 + 答案被后一题覆盖

### 改动

**① `app/parser/splitter.py` —— 题号正则增补两个分支**

```python
_BRACKET_NO = r"[（(]\s*(\d{1,4})\s*[）)]"                      # （79）
_CHAPTER_NO = r"第\s*(\d{1,4})\s*题(?:\s*[.、。．)）:：])?"        # 第80题. / 第80题:
_PLAIN_NO   = r"(?:第\s*)?(\d{1,4})\s*[.、。．)）]"               # 12. / 12、 （原后缀式）
NO_PREFIX_RE = re.compile(r"^\s*(?:" + _BRACKET_NO + "|" + _CHAPTER_NO + "|" + _PLAIN_NO + r")\s*")
```

- 三种形态按锚点优先级依次尝试；`match_no()` 与 `strip_no_prefix()` 共用同一正则，
  因此括号/「第N题」前缀会被**整段剥离**，不会把 `（79）` 留在题干里。
- 误判防护：括号分支要求括号内是 1–4 位**纯数字**，所以填空题 `（  ）`、选项式 `（A）`、
  超长数字 `（12345）` 都不会被当作题号（已加参数化单测固定该行为）。
- 题号仍归一化为 `str(int(no))`，重复题号统计不受 `003` / `3` 影响。

**② `app/parser/extractor.py` —— 答案行改为「第一条生效」**

```python
# 只取「第一条」答案行：同一块内若因题号未被识别而混入后续题目的
# 「答案：」行，不能被它覆盖真实答案（v1.1 修复 D3-2）。
if token and not answer_raw:
    answer_raw = token
    if inline_analysis:
        analysis_parts.append(inline_analysis)
```

### 证据（`python scripts/accuracy_check.py`）

| | 修复前 | 修复后 |
|---|---|---|
| 切分出的题目块 | 97（配对 97） | **100（配对 100）** |
| 四项全对 | 96 | **100** |
| 整体准确率 | **96.0%** | **100.0%** |
| 分项准确率 | stem 97.0 / options 97.0 / answer 96.0 / qtype 96.0 | **stem 100 / options 100 / answer 100 / qtype 100** |
| 未切出的题 | 79 / 80 / 81 三题整段丢失 | **0** |
| 四项有错的题 | 78（answer `ABCDE` 被覆盖成 `A`，qtype multiple→single） | **0** |

修复后报告尾部：

```
四项全对        : 100
整体准确率      : 100.0%   （PRD 要求 ≥90.0%）
分项准确率      : stem=100.0%  options=100.0%  answer=100.0%  qtype=100.0%
结论：PASS
```

## 9.2 D1（一般）：`anomaly_count` 是解析期快照，编辑/删除后不刷新

### 改动（改为读取侧实时聚合，冗余列降级为历史快照）

`app/db.py` 新增两组实时聚合列，并在序列化函数里「实时优先、冗余列兜底」：

```python
IMPORT_LIVE_COLUMNS = """
  (SELECT COUNT(*) FROM questions q JOIN import_files f ON f.id = q.import_file_id
     WHERE f.import_id = i.id) AS total_questions_live,
  (SELECT COUNT(*) FROM questions q JOIN import_files f ON f.id = q.import_file_id
     WHERE f.import_id = i.id AND q.status = 'published') AS committed_questions_live,
  (SELECT COUNT(*) FROM anomalies a JOIN questions q ON q.id = a.question_id
     JOIN import_files f ON f.id = q.import_file_id
     WHERE f.import_id = i.id AND a.handled = 0) AS anomaly_count_live,
  (SELECT COUNT(*) FROM anomalies a JOIN questions q ON q.id = a.question_id
     JOIN import_files f ON f.id = q.import_file_id
     WHERE f.import_id = i.id) AS anomaly_total_live
"""
FILE_LIVE_COLUMNS = "... 同构，按 f.id 聚合 ..."
```

- `import_to_dict()` / `import_file_to_dict()` 通过 `_pick_int(data, "*_live", "*")` 取实时值；
  `imports.committed_questions`、`import_files.anomaly_count` 两列仍按原样写入，但**不再被采信**。
- `app/api/imports.py`：`_ensure_import()`、`GET /api/imports`、`GET /api/imports/{id}` 三处查询
  改为 `SELECT i.*, {IMPORT_LIVE_COLUMNS}` / `SELECT f.*, {FILE_LIVE_COLUMNS}`。
- **字段语义明确化**（响应结构只有新增，无删除/改名，前端已同步）：
  - `anomaly_count` = 该文件/批次**未处理**异常数（实时，与 `/api/stats.anomaly_open`、`/api/anomalies` 恒等）
  - `anomaly_total` = **新增**，该文件/批次全部异常数（含已处理，实时）
  - `committed_questions` = 该文件已入库题数（实时，删除题目后可回退）— 文件维度**新增**
  - `total_questions` = 该文件当前题目数（实时，删除题目后可回退）

### 证据（真实闭环）

```
① 上传后：files[0].anomaly_count = 1（首题缺答案）
② 入库后：/imports/{id} files[0] anomaly_count=1 anomaly_total=1 total=2 committed=2
          /imports      items[0] anomaly_count=1 anomaly_total=1 total=2 committed=2
          /stats anomaly_open=1 ；/anomalies total=1          ← 四处一致
③ PUT 修好答案后：
          /imports/{id} files[0] anomaly_count=0 anomaly_total=0
          /imports      items[0] anomaly_count=0 anomaly_total=0
          /stats anomaly_open=0 ；/anomalies total=0          ← 四处一致（修复前此处仍为 1）
④ 删除前 batch committed_questions=2 total_questions=2
   删除后 batch committed_questions=1 total_questions=1      ← 入库数可回退
```

## 9.3 D2（轻微）：LIKE 降级匹配 `options_json` 原文，结构字符命中全库

### 改动

`app/api/search.py`：

1. `LIKE_TEXT_COLUMNS = ("stem", "answer", "analysis")` —— **移除 `options_json`**。
2. 选项改用 SQLite **JSON1** 只匹配「值」：

```sql
EXISTS (SELECT 1 FROM json_each(
         CASE WHEN json_valid(q.options_json) THEN q.options_json ELSE '{}' END)
       WHERE json_each.value LIKE ? ESCAPE '\')
```

   （`json_valid` + `CASE` 兜底，避免历史脏数据触发 `malformed JSON` 异常）
3. `json1_available()` 在首次调用时探测一次并缓存；探测失败时回退为
   「仅当检索词含有非结构字符才 LIKE `options_json`」，即结构字符词仍不会命中。
4. `_can_use_fts()` 增加约束：只由 JSON 结构字符组成的检索词不走 `MATCH`
   （避免 trigram 在 `options_json` 原文里命中 `"`,`:` 等片段）。
5. 语义收敛：查询串**非空**但拆不出关键词（例如只有 `,` `;` 这类分隔符）时返回**空集**，
   不再退化成「列出全库」。空查询 `q=''` 仍保持「浏览全部已入库题目」。

### 证据（同一份题库，旧 SQL 与新接口对照）

| 查询词 | 旧实现（直接 LIKE `options_json`） | 新实现 | 说明 |
|---|---|---|---|
| `"` | 2（全库） | **0** | 结构字符不再命中 |
| `{` | 2（全库） | **0** | |
| `}` | 2（全库） | **0** | |
| `:` | 2（全库） | **0** | |
| `,` | 2（全库） | **0** | 分隔符 → 视为无效检索词 |
| `A` | 2 | 2 | 语义保留：命中 `answer`/选项文本里的字母 A，**不再因 JSON 键 `"A"` 命中** |
| `房颤` | 1 | 1 | 正常检索无回归 |
| `答案` | 0 | 0 | 答案标签不落库 |

JSON1 可用性实测：`json1 可用: True`（本机 SQLite 3.53.1；`python:3.11-slim` 的 libsqlite3 ≥3.38 亦内置）。

## 9.4 回归结果（全部真跑）

```
$ cd backend && python -m pytest -q
102 passed, 3 xpassed, 1 warning in 4.43s
  XPASS test_known_issues.py::test_file_anomaly_count_refreshed_after_edit
  XPASS test_known_issues.py::test_structure_characters_should_not_match_everything
  XPASS test_known_issues.py::test_parenthesized_question_number_should_split
（QA 上一轮基线为 91 passed + 3 xfailed；本轮 70 项解析单测 + QA 32 项，3 条缺陷用例转 XPASS）

$ python scripts/accuracy_check.py
整体准确率 : 100.0%（修复前 96.0%）；四项全对 100/100

$ python scripts/perf_10k.py
查询 '房颤'        路径=LIKE 降级     命中=10000  中位数=14ms
查询 '心律失常'      路径=FTS5 MATCH   命中=10000  中位数=17ms
查询 '脉搏短绌 心律失常' 路径=FTS5 MATCH 命中=1000   中位数=12ms
最差中位数：17ms   PRD 阈值：1000ms   结论：PASS

$ bash scripts/smoke_test.sh
冒烟测试结果：PASS=35  FAIL=0

$ cd frontend && npm run build
✓ 998 modules transformed.  dist/assets/index-DC7eagjV.js 510.92 kB  ✓ built in 2.38s
```

## 9.5 v1.1 新增偏离

| 点 | 改动 | 原因 |
|---|---|---|
| 题号形态 | 由架构 §5 的单一后缀式正则，扩展为「括号式 / 第N题式 / 后缀式」三选一 | §5 的正则无法覆盖 `（79）`、`第80题` 这类真实排版；扩展后仍保留原后缀式行为（回归用例已固定） |
| 答案行语义 | 由「后写覆盖」改为「首条生效」 | 题号漏识别时后一题答案会覆盖当前题；首条生效更符合"本题答案"的直觉 |
| `anomaly_count` 语义 | 由「解析期异常总数」改为「**未处理**异常数实时值」，并新增 `anomaly_total` | 与 `/api/stats`、`/api/anomalies` 的实时口径对齐；保留总数需另设字段 |
| 响应字段 | `/api/imports`、`/api/imports/{id}` 新增 `anomaly_total`、文件维度 `committed_questions` | 只增不改，前端同步展示；旧消费方不受影响 |
| 空检索词 | 非空但无有效关键词 → 返回空集 | 避免 `q=','` 这类输入退化成"列出全库" |
| 选项文本匹配 | 依赖 SQLite JSON1（不可用时自动降级） | JSON1 自 SQLite 3.38 内置，两个目标环境均满足；降级路径保证结构字符仍不命中 |

---

# v1.1.1 收尾修复（QA 第二轮回派）

QA 第 2 轮回归判 PASS（128 passed / 1 xfailed / 0 failed），新发现 2 条，本轮只修这两条，未做其他重构。
未改 `docs/PRD.md`、`docs/ARCHITECTURE.md`，未动 QA 的测试文件，未新增文件。

## 10.1 D4：含引号检索词在 FTS / LIKE 两条路径结果不一致

### 改法（一行级，剥首尾引号）

`app/api/search.py` 的 `parse_terms()` 增加 `_strip_wrapping_quotes()`：
剥掉每个检索词**首尾**的 `" ' “ ” ‘ ’`，剥空则丢弃该词；若全部词被剥空，
沿用上轮语义「非空查询但无有效关键词 → 返回空集」。

```python
WRAPPING_QUOTE_CHARS = "\"'“”‘’"

def _strip_wrapping_quotes(term: str) -> str:
    stripped = (term or "").strip()
    while stripped and stripped[0] in WRAPPING_QUOTE_CHARS:
        stripped = stripped[1:]
    while stripped and stripped[-1] in WRAPPING_QUOTE_CHARS:
        stripped = stripped[:-1]
    return stripped.strip()
```

**为什么不用「含引号就退回 LIKE」**：那会让引号检索词永久走慢路径（10k 数据 14ms → 仍可用，但
语义上 `"房颤"` 与 `房颤` 应完全等价），而且 FTS 与 LIKE 的匹配列仍不同（FTS 索引 `options_json` 原文）。
剥引号后两条路径拿到的是**同一个词**，一致性由构造保证，且不牺牲性能。高亮也自然受益
（`highlight_html` 收到的是剥引号后的词，`<mark>房颤</mark>` 正常包裹）。

### 改前 / 改后（接口级，切换 `db.FTS_TRIGRAM` 强制两条路径）

```
查询词       改前 FTS   改前 LIKE  改前一致   改后 FTS   改后 LIKE  改后一致
'"房颤"'     [1]        []         ❌        [1]        [1]        ✅
'房颤"'      [1]        []         ❌        [1]        [1]        ✅
'"A"'      [1, 2]     []         ❌        [1, 2]     [1, 2]     ✅
'"A'       []         []         ✅        [1, 2]     [1, 2]     ✅
```

（第 4 行改前「一致但都为 0」——它其实也搜不到东西；改后能正常命中答案含 A 的题。）

补充：FTS5 MATCH 层面，旧词 `'"房颤"'` 命中 1 行、`'"A"'` 命中 2 行（trigram 命中 `({"A":"房颤"})` 的 JSON 标点），
剥引号后 `房颤`(2 字) / `A`(1 字) 均低于 trigram 的 3 字下限，统一走 LIKE（三列文本 + `json_each` 选项值），
与「trigram 不可用」的构建行为完全一致。

## 10.2 D5：题内 `（1）（2）` 列举被误切成新题（D3 引入的副作用）

### 改法（括号编号改为「当前题已收尾才切」）

`app/parser/splitter.py`：`_match_detail()` 现在同时返回题号与**形态**
（`bracket` / `chapter` / `plain`），`split()` 仅对 `bracket` 形态加条件：

```python
if kind == NO_KIND_BRACKET and current is not None and not is_block_closed(current):
    current.lines.append(line)   # 题内列举 → 并入当前块
    continue
```

`is_block_closed(block)`：块内任一行命中以下**三个**正则之一即视为已收尾 ——

1. **答案行**（`ANSWER_RE`）；
2. **解析行**（`ANALYSIS_RE`）；
3. **≥1 个选项行**（`OPTION_RE`，定稿时补入）。

````python
def _closing_regexes() -> Tuple[re.Pattern[str], ...]:
    global _closing_patterns
    if _closing_patterns is None:
        from .extractor import ANALYSIS_RE, ANSWER_RE, OPTION_RE
        _closing_patterns = (ANSWER_RE, ANALYSIS_RE, OPTION_RE)
    return _closing_patterns
````

**为什么必须加上「选项行」**：`missing_answer` 是 P0-4 要主动标记的六类异常之一，
而「有选项、答案空着」在护理题库里非常常见（答案待补录）。若这种题不算已收尾，
紧跟其后的括号编号题会被并进来、后题答案错位到前题 —— 属于**内容级错误**，
不能带着交付。补上选项行后该场景能正确切成 2 块，第 1 题也会照常被标 `missing_answer`。
选项行正则是与抽取阶段**共用的同一个对象**，不复制、不会漂移。

三个正则都通过函数内**延迟导入**取得，避免 `splitter → extractor` 循环依赖
（`splitter` 不在模块级 import `extractor`，`extractor` 本来就 import `splitter`，依赖方向不变）。

保持不变：`第N题` 与后缀式 `1.` **无条件**开新块；`（A）` / `（  ）` / `（12345）` 误判防护不变。
**枚举不被误切的不变式**：题内列举的时序天然是「题干 → `（1）（2）` → 选项 → 答案」，
看到 `（1）` 时块内只有题干、还没有任何选项行，三个条件全不满足 → 仍并入当前块。

**为什么不用「题号 = 上一题 +1」的序列判断**：真实题库常按章节重新编号
（第一章 1..50、第二章又从 1 开始），序列断言会把章首题判成列举而合并整章；
而且题号本身有错号、跳号、重复（本项目专门有 `duplicate_number` 异常），
用序列做切分依据会把「错号」放大成「整章塌块」。「上一题是否已收尾」是与编号无关的**结构信号**，更稳。

### 五个方向的自测结果

```
【1】题内列举（题干 → （1）（2）→ 选项 → 答案）
  块数 = 1（期望 1） 选项数 = 4  答案 = ABC  qtype = multiple  异常 = []（无 missing_answer）
  题干 = 预防压疮的护理措施包括? (1)定时翻身 (2)保持皮肤清洁干燥   ← 列举内容保留，不丢信息

【2】混合编号文档（（1）/ 第2题. / 第3题：/ 4.）
  块数 = 4（期望 4）  题号 = ['1','2','3','4']
  第3题题干 = 铺备用床的主要目的是( )?   ← 题干内填空括号未被误判
  第4题 = ('×', 'judge')

【3】accuracy_100.txt（含 （79）（80）（81）/ 第80题.）
  切分块 100（配对 100） 四项全对 100  整体准确率 100.0%  结论 PASS

【4】括号题「有选项、无答案行」+ 下一道括号题有答案
      ← test_bracket_question_with_options_but_no_answer_still_splits
  块数 = 2（期望 2）  题号 = ['1','2']
  Q1: opts=4  answer=''   qtype=unknown  anomalies=['missing_answer']   ← 不再被并掉
  Q2: opts=4  answer='B'  qtype=single   anomalies=[]                   ← 答案未串到 Q1

【5】反向不变式：枚举不切 + 其后的真题要切
      ← test_enumeration_then_real_bracket_question_splits
  块数 = 2（期望 2）
  Q5: opts=4  answer='ABC'  qtype=multiple  无 missing_answer  题干含 (1)定时翻身 (2)保持皮肤清洁干燥
  Q6: opts=4  answer='A'    qtype=single    ← 追加的真实括号题被正确切开
```

### 残留限制（必须记录）

连续括号编号、且**前一道题既无选项又无答案**时仍会并块。实测：

```
（1）铺好的无菌盘有效期为4小时。
（2）氧气筒内氧气可用尽后再更换。
答案：错
→ 块数 1
  stem   = '铺好的无菌盘有效期为4小时。 (2)氧气筒内氧气可用尽后再更换。'   ← 两题题干粘在一起
  answer = '×'   qtype = judge   anomalies = []                          ← 不产生任何异常
```

**原因**：此时块内既无答案/解析行，也没有任何选项行，三个收尾条件全不满足 ——
「题内 `（1）（2）` 列举」与「括号题号」在纯文本层面**没有任何结构信号可区分**
（二者同形、同位置、同缩进）。要区分只能引入缩进宽度、编号连续性、题型联合推断等
语义启发式，会把误切风险反向引入到「题干里的 `（1）` 列举」这一更常见的场景，故不做。

**影响面**：仅限「用括号编号 + 前题无选项无答案」的文档，典型是**判断题连排且首题缺答案**。
`accuracy_100.txt` 与 QA 的混合编号样本都不含此组合，故准确率不受影响。

⚠️ **注意这个限制不会自己报错**：合并后的块因为拿到了后题的答案，`missing_answer` 不会触发，
所以异常页上看不出来。缓解方式：

* 此类源文档本身数据残缺（判断题缺答案本该产出 1 条 `missing_answer`），导入后异常数为 0
  反而是「可疑信号」，人工校对时可见；
* 导入页的「切分块数 vs 源文件题数」对比可供快速核对；
* 「有选项 + 有编号」的正常文档不受影响（正是【4】覆盖的场景）。

## 10.3 回归结果（六项全部真跑，数值为 v1.1.1 定稿时的最新一轮）

```
$ cd backend && python -m pytest -q
133 passed, 1 warning in 7.03s          （133 collected / 0 failed / 0 xfail / 0 xpass）

$ python scripts/accuracy_check.py
切分块 100（配对 100） / 四项全对 100 / 整体准确率 100.0% / 异常 5 / 结论 PASS

$ python scripts/perf_10k.py
查询 '房颤'              路径=LIKE 降级    命中=10000  中位数=14ms 最大=18ms
查询 '心律失常'            路径=FTS5 MATCH  命中=10000  中位数=17ms 最大=17ms
查询 '脉搏短绌 心律失常'      路径=FTS5 MATCH 命中=1000   中位数=12ms 最大=12ms
最差中位数：17ms   PRD 阈值：1000ms   结论：PASS

$ bash scripts/smoke_test.sh      → PASS=35  FAIL=0
$ bash scripts/e2e_demo.sh        → PASS=28  FAIL=0
$ cd frontend && npm run build    → ✓ 998 modules transformed，✓ built in 2.56s（0 TS error）
```

**用例数 133 的来源**（便于核对，不是含糊的「跑绿了」）：

| 变化 | 数量 | 说明 |
|---|---|---|
| 扩展开启前的基线 | 129 passed + 1 xpassed | D4 期间 QA 用 `xfail` 标注的那条 |
| 本轮新增（我方 `test_parser.py`） | +2 | `test_bracket_question_with_options_but_no_answer_still_splits`、`test_enumeration_then_real_bracket_question_splits` |
| QA 更新 `test_regressions.py` | +1，同时 `xfail` → 正式用例 | QA 撤掉 D4 的 `xfail` 标记并加严断言（要求两条路径命中集合「相等**且非空**」，防「两边都为 0」的假一致）；QA 文件由 QA 自行改动，我方未触碰 |

因此当前为 `133 passed` 且**没有 xpass** —— xpass 归零是因为 `xfail` 标记已被 QA 撤掉、
该用例转为正式通过，**不是用例被跳过**。

## 10.4 v1.1.1 新增偏离

| 点 | 改动 | 原因 |
|---|---|---|
| 检索词预处理 | `parse_terms()` 剥掉每个词首尾引号（`"` `'` `“”` `‘’`） | 让 FTS / LIKE 两条路径拿到同一个词，一致性由构造保证；`"房颤"` 与 `房颤` 语义等价 |
| 括号题号切分 | 由「无条件开新块」改为「当前块已收尾才开新块」 | 与题内 `（1）（2）` 列举同形；用「上一题是否已收尾（有答案/解析行，或有选项行）」这一结构信号区分，不用题号序列（避免打断按章节重新编号的文档） |
| 收尾判据扩展：**+ ≥1 个选项行**（定稿） | `is_block_closed()` 的收尾条件由「答案/解析行」扩为「答案/解析行 **或** ≥1 个选项行」，即 `_closing_regexes()` 增加 `OPTION_RE` | 「有选项、答案空着」是 `missing_answer` 的高频形态（答案待补录）。不视为收尾则后题答案错位到前题，属内容级错误。详见 §10.5 |
| 模块依赖 | `splitter` 通过函数内延迟导入复用 `extractor` 的答案/解析/选项正则 | 避免 `splitter ↔ extractor` 循环 import，且不复制正则（不会漂移） |

## 10.5 定稿变更记录（§10.2 / §10.3 / §10.4 的补充说明）

本节只记录「这条扩展是什么时候、因为什么被批准落地的」，实现细节、自测与残留限制
统一写在 **§10.2**，回归数字统一写在 **§10.3**，偏离清单统一写在 **§10.4** ——
避免同一件事在多处各写一遍、后来改一处漏一处。

* **变更**：`is_block_closed()` 的收尾判据由「答案行 / 解析行」两条件，扩为
  「答案行 / 解析行 / **≥1 个选项行**」三条件（`_closing_regexes()` 增加 `OPTION_RE`）。
* **时点**：v1.1.1 定稿前，经 team-lead 批准后落地；此前版本只按「答案/解析行」判定收尾。
* **理由**：`missing_answer` 是 P0-4 要主动标记的六类异常之一，「有选项、答案空着」在护理题库里
  很常见（答案待补录）。只按答案/解析判定时，这种题后面紧跟括号编号题会被合并、
  后题答案错位到前题 —— 内容级错误，不能带着交付。
* **未动**：括号编号开新块的判定分支、`第N题` 与 `1.` 的无条件切分、
  `（A）` / `（  ）` / `（12345）` 守卫、以及「不用题号序列」的原则。
* **代价**：多出一个残留限制（连续括号编号且前题既无选项又无答案时仍并块），
  已连同「该块不产生任何异常、需人工核对」的风险点一并记在 §10.2「残留限制」。
