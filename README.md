# private-tiku · 私有护理题库系统

把散乱的护理试题文档（DOCX / TXT / PDF）变成**秒级可检索、可校对异常**的私有题库，数据全部留在内网与本地磁盘。

- **批量导入**：多文件拖拽上传，单次 ≥20 个文件，同步解析后先暂存、确认才入库
- **规则解析**：题号 / 题干 / 选项 / 答案 / 解析 / 题型（单选・多选・判断）自动识别，纯正则 + 状态机，可解释可复现
- **全文检索**：SQLite FTS5 `trigram` 中文子串匹配 + `<mark>` 高亮，万级题量毫秒级响应；短词自动退化为 LIKE
- **异常校对**：缺答案 / 缺选项 / 选项不足 / 无题干 / 题号重复 / 解析异常 六类规则，可筛选、可标记已处理
- **一键导出**：JSON / Markdown 文件下载
- **单容器部署**：`docker compose up -d`，数据落 `./data` 卷

---

## 1. 快速开始

### 方式 A：Docker（推荐，单容器）

```bash
cp .env.example .env      # 可选：修改对外端口 TIKU_PORT
docker compose up -d --build
# 打开 http://<服务器IP>:8000
docker compose logs -f    # 查看日志
```

数据（SQLite + 上传的原始文件）持久化在宿主机 `./data`：

```
data/
├── tiku.db          # SQLite（含 FTS5 索引）
└── uploads/         # 上传的原始文件，按批次分目录
```

### 方式 B：本地开发（Node 22 + Python 3.13 已验证）

```bash
# 1) 后端依赖
python -m pip install -r backend/requirements.txt

# 2) 前端构建（产物由后端托管）
cd frontend && npm install && npm run build && cd ..

# 3) 启动
cd backend && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 打开 http://127.0.0.1:8000 ；接口文档 http://127.0.0.1:8000/docs
```

前端热更新开发（可选，需后端已在 8000 端口运行）：

```bash
cd frontend && npm run dev    # http://127.0.0.1:5173 ，/api 已代理到 8000
```

> **依赖说明**：`backend/requirements.txt` 采用「下限 + 主版本上限」约束而非全 `==` 死锁。
> 原因：架构文档 §8 的示例版本中 `pydantic==2.7.4`（及其 `pydantic-core`）在 Python 3.13 上没有
> cp313 wheel，安装时需要源码编译并失败。改用约束区间后，Python 3.13（本机）与容器内
> Python 3.11（`python:3.11-slim`）都能二进制安装。工程上如需完全可复现构建，
> 可在目标环境执行 `pip freeze > requirements.lock.txt`。

---

## 2. 目录结构

```
private-tiku/
├── docker-compose.yml       # 单服务编排：挂载 ./data，暴露 8000
├── .env.example             # 端口 / 数据目录样例
├── .dockerignore / .gitignore
├── backend/
│   ├── Dockerfile           # 多阶段：node:20 构建前端 → python:3.11-slim 运行
│   ├── requirements.txt
│   ├── pytest.ini
│   ├── scripts/
│   │   ├── gen_fixtures.py  # 生成 TXT(GBK)/DOCX/PDF 测试样本
│   │   └── smoke_test.sh    # 端到端闭环冒烟（自带启动/关闭后端）
│   └── app/
│       ├── main.py          # FastAPI 实例、路由注册、StaticFiles + SPA 回退
│       ├── config.py        # 路径/端口/常量
│       ├── db.py            # sqlite3 连接、DDL、FTS5 降级、序列化
│       ├── models.py        # Pydantic schema + 枚举 + 错误码
│       ├── api/             # imports / questions / search / anomalies / export
│       ├── parser/          # reader → normalize → splitter → extractor → validator → pipeline
│       └── tests/           # test_parser.py + fixtures
└── frontend/
    ├── vite.config.ts       # base '/'，dev 代理 /api
    ├── tailwind.config.js   # 关闭 preflight，避免与 MUI 冲突
    └── src/
        ├── api/client.ts    # axios 封装（baseURL /api），统一解包 + 错误
        ├── components/      # Layout / QuestionCard / Highlight / UploadDropzone / FilterBar / StatusChip
        └── pages/           # Search / Import / Anomaly / QuestionDetail / Stats
```

---

## 3. 使用流程

1. **导入**：`导入文档` → 拖入 DOCX/TXT/PDF → 上传后立即解析 → 「预览」查看每个文件的题数与异常数
2. **入库**：点「全部入库」（`POST /api/imports/{id}/commit`），题目从 `pending` 变 `published` 后才可被检索
3. **搜索**：首页输入关键词（空格分隔多关键词，AND 语义），支持按题型 / 仅看异常 / 来源文档筛选
4. **校对**：`异常校对` 页按类型筛选，逐题「标记已校对」；点「查看/编辑」直接修改题干、选项、答案、解析，保存后**自动重算异常**
5. **导出**：`导入统计` 页「导出 Markdown / JSON」

---

## 4. API 速查（统一响应 `{code, message, data}`，字段 snake_case）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查（含 FTS 分词器状态） |
| POST | `/api/imports` | multipart 上传（字段名 `files[]`）→ 同步解析 → 暂存 |
| GET | `/api/imports?page=&size=` | 导入批次列表 |
| GET | `/api/imports/{id}` | 批次详情（含文件明细） |
| POST | `/api/imports/{id}/commit` | 确认入库 |
| DELETE | `/api/imports/{id}` | 删除批次（题目 / 异常级联 + 清理落盘文件） |
| GET | `/api/search?q=&type=&only_anomaly=&doc_id=&page=&size=` | 检索 + 筛选 + 高亮 |
| GET | `/api/questions/{id}` | 题目详情（含异常） |
| PUT | `/api/questions/{id}` | 编辑题目并重算异常 |
| DELETE | `/api/questions/{id}` | 删除题目 |
| GET | `/api/anomalies?type=&handled=&page=&size=` | 异常清单（含题目快照） |
| PATCH | `/api/anomalies/{id}` | 标记已处理 `{"handled": true}` |
| GET | `/api/export?format=json\|md` | 导出文件流 |
| GET | `/api/stats` | 统计面板 |

**错误码**：`0` 成功 · `40001` 参数错误 · `40401` 资源不存在 · `42201` 文件类型不支持 · `50001` 解析失败 · `50000` 内部错误。
HTTP 状态码同步（400/404/422/500），响应 body 仍是统一结构。

枚举：题型 `single|multiple|judge|unknown`；异常 `missing_answer|missing_options|too_few_options|empty_stem|duplicate_number|parse_error`；题目状态 `pending|published`；导入状态 `parsing|pending_confirm|committed|failed`。

---

## 5. 解析规则要点

- **题号**：`^\s*(?:第?\s*)?(\d{1,4})\s*[.、．。)）]\s*` 命中即开新题块，首题号之前的标题被丢弃
- **选项**：行首 `A.` / `A、` / `A)` 逐行收集；题干行内 `A.xx B.xx C.xx D.xx` 自动拆分；选项续行自动合并
- **答案**：`答案：A` / `参考答案：ABD` / `答案: 对`，也支持题干行尾内联答案
- **解析**：`解析：…` / `答案解析：…`
- **规范化**：全角→半角（含全角字母、数字、`．：（）？`），去空行、压缩空格；中文句号 `。` 保留
- **题型判定**：无选项且答案为对错符号 → `judge`；答案 ≥2 字母 → `multiple`；1 个字母 → `single`；否则 `unknown`
- **答案规范化**：多选字母升序大写（`dba` → `ABD`）；判断统一 `√` / `×`（对/正确/T/√ → `√`）
- **文件编码**：TXT 用 chardet 探测（GBK/GB18030/UTF-8 自动识别），PDF 走 pdfplumber（扫描件不支持，会标记 `parse_error`）

---

## 6. 测试

```bash
# 解析引擎与异常规则单测（59 项）
cd backend && python -m pytest app/tests/test_parser.py -q

# 端到端冒烟（自动启停 uvicorn，35 项检查）
cd backend && bash scripts/smoke_test.sh

# 前端类型检查 + 构建
cd frontend && npm run build
```

重新生成测试样本（TXT-GBK / DOCX / PDF）：

```bash
cd backend && python scripts/gen_fixtures.py
```

---

## 7. 已知限制

1. **扫描件 PDF 不做 OCR**：无文本层的 PDF 解析为「无可提取文本」，该文件记为解析失败。
2. **搜索短词**：FTS5 trigram 要求 ≥3 字符，`房颤`（2 字）等短词自动退化为 `LIKE`，万级数据仍 <100ms。
3. **单管理员模式**：无登录 / 权限体系，请勿直接暴露公网；需要时应前置反向代理鉴权。
4. **题库规模**：按万级题量设计；十万级需迁移独立检索引擎（接口已隔离在 `api/search.py`）。
5. **删除题目**：删除后 FTS 索引由触发器同步清理，但 SQLite 文件不会立即收缩（可用 `VACUUM` 手动整理）。
6. **`python-docx` 只读段落与表格**，图片 / 文本框内的文字不会被提取。

---

## 8. 技术选型

| 层 | 选型 |
|---|---|
| 后端 | Python 3.11+ / FastAPI / uvicorn |
| 数据库 | SQLite（标准库 `sqlite3`，无 ORM）+ FTS5 `trigram` |
| 解析 | python-docx / pdfplumber / chardet + 正则状态机 |
| 前端 | Vite 5 / React 18 / MUI 5 / Tailwind 3 / axios |
| 部署 | docker compose 单服务 + 多阶段 Dockerfile |
