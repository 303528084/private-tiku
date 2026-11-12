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

---

## 9. CI / Docker Hub 自动发布

> 流水线文件在**仓库根**：`.github/workflows/docker-publish.yml`。
> 本项目位于仓库子目录 `private-tiku/`，因此流水线里的构建上下文是 `./private-tiku`、
> Dockerfile 是 `./private-tiku/backend/Dockerfile`（等价于 `docker-compose.yml` 的
> `context: .` + `dockerfile: backend/Dockerfile`）。**不要把 `.github/` 放进 `private-tiku/`**，
> GitHub 只读取仓库根目录的 `.github/workflows/`。

### 9.1 首次运行前的一次性准备

1. **Docker Hub 侧**：登录 https://hub.docker.com → Create repository → 新建仓库
   **`private-tiku`**（建议先设为 Private）。镜像名由 `DOCKERHUB_USERNAME` 拼出
   `${DOCKERHUB_USERNAME}/private-tiku`，所以仓库名必须叫 `private-tiku`；
   若不想手工建，可在 Docker Hub 账号设置里允许自动创建（push 时自动建 public 仓库）。
2. **Docker Hub 访问令牌**：Account Settings → **Personal access tokens** → Generate new token，
   权限选 **Read & Write**。这**不是**你的登录密码；保存后只显示一次。
3. **GitHub 仓库 Secrets**（仓库 → Settings → Secrets and variables → Actions → New repository secret）：

   | Secret 名 | 值 | 说明 |
   |---|---|---|
   | `DOCKERHUB_USERNAME` | Docker Hub 用户名 | 用于登录与拼接镜像名，**不写死在仓库里** |
   | `DOCKERHUB_TOKEN` | 上面创建的 PAT | Read & Write token，勿用密码 |

   缺任一 Secret 时会在第二个步骤（Checkout 之后的前置校验）以可读错误直接失败（不会抛出难懂的 401）。

### 9.2 触发方式

| 触发 | 条件 | 结果 |
|---|---|---|
| push 到 `main` | 仅当 `private-tiku/**` 或该 workflow 文件本身变更 | 构建 → 自检 → 推送 |
| push tag `v*` | 任何以 `v` 开头的标签；semver 形式（如 `v1.2.3`）会额外产出 `1.2.3` / `1.2` | 构建 → 自检 → 推送 |
| 手动 `workflow_dispatch` | Actions 页面 Run workflow，可填一个额外 `tag` 输入 | 构建 → 自检 → 推送 |

> 未配置 `pull_request` 触发：PR 不推镜像，且 fork PR 读不到 Secrets。
> 同一分支/标签的连续推送会取消排队中的旧任务（`concurrency` + `cancel-in-progress`）。

```bash
git tag v1.2.3 && git push origin v1.2.3   # 发布 1.2.3 / 1.2 标签
```

### 9.3 产物标签含义

| 标签 | 生成条件 | 说明 |
|---|---|---|
| `latest` | 仅 `main` 分支 | 默认分支最新可运行镜像 |
| `sha-<短 SHA>` | 每次构建 | 精确回溯到提交，任何触发方式都有 |
| `1.2.3` | push tag `v1.2.3` | 精确版本 |
| `1.2` | push tag `v1.2.3` | 滚动小版本，便于跟随补丁更新 |
| `v1.2.3` | push tag `v*` | 原始 tag 名原样保留（非 semver 的 tag 如 `v2024-10-release` 只会有这一种） |
| 自定义值 | 手动触发填了 `tag` | 例如 `1.2.3-rc1`，用于预发验证 |

使用示例：

```bash
docker pull <你的DockerHub用户名>/private-tiku:latest
docker run -d -p 8000:8000 -v "$PWD/data:/app/data" <你的DockerHub用户名>/private-tiku:latest
```

### 9.4 流水线的质量关卡

推送前镜像必须被证明能跑，否则 `latest` 绝不被覆盖：

1. `build`（`load: true`，只进本地 daemon，不推远端）；
2. 起容器 `docker run -d -p 8000:8000`，轮询 `GET /api/health` **最长约 60–150s**
   （共 30 轮，每轮 `sleep 2s` + `curl --max-time 3s`，最坏 30 × (2+3) = 150s；实测失败路径约 2 分钟），
   要求 HTTP 200 且响应体含 `"status":"ok"`，失败则打印 `docker logs` 并退出非 0；
3. 额外断言 `GET /` 返回前端 `index.html`，防止多阶段构建的前端产物丢失；
4. 全部通过后才 `docker login` + 推送到 Docker Hub（第二次构建命中 `type=gha` 缓存，不重复编译）。

缓存用 GitHub Actions 缓存（`cache-from/cache-to: type=gha`），跨运行复用 npm / pip 层。
`provenance: false`：`load: true` 不支持导出 manifest list；需要 SBOM/来源证明时改为
`push` 直推并去掉 `load` 步骤。

### 9.5 仅 amd64 构建

流水线固定 `platforms: linux/amd64`（GitHub 托管 runner 原生架构，无需 QEMU）。
要出 arm64（Apple Silicon / 树莓派服务器）：加 `docker/setup-qemu-action@v4` 并把
`platforms` 改为 `linux/amd64,linux/arm64`，代价是构建时间与镜像体积约翻倍，
且不能再 `load` 到本地做容器自检（需改为直接 push 后 `docker pull` 验证）。

