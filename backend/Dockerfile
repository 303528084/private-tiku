# private-tiku 单容器镜像：node:20 构建前端 → python:3.11-slim 运行后端
# 构建上下文为仓库根目录（需要同时访问 frontend/ 与 backend/）：
#   docker build -f backend/Dockerfile -t private-tiku .

# ---------- 阶段 1：构建前端静态产物 ----------
FROM node:20-alpine AS web

WORKDIR /web

# 先复制依赖清单以最大化利用层缓存
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

COPY frontend/ ./
RUN npm run build && test -f /web/dist/index.html

# ---------- 阶段 2：运行时 ----------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TIKU_DATA_DIR=/app/data \
    TIKU_PORT=8000

WORKDIR /app/backend

# 固定依赖版本安装（容器内为 Python 3.11，与 requirements.txt 的版本区间一致）
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码（config.BASE_DIR 解析为 /app，前端产物需落在 /app/frontend/dist）
COPY backend/ ./
COPY --from=web /web/dist /app/frontend/dist

RUN mkdir -p /app/data

EXPOSE 8000

# 数据卷挂载点：SQLite 数据库 + 上传的原始文件
VOLUME ["/app/data"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
