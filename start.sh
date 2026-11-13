#!/usr/bin/env bash
#
# 私有护理题库系统 · 一键启动（Linux / macOS）
#
# 幂等：重复运行只会补齐缺失的部分，不会重复装依赖、不会重复构建前端。
#
#   ./start.sh
#
# 做的事（每一步都可单独重跑）：
#   1. 定位仓库根（不依赖当前工作目录，从符号链接启动也能找对）
#   2. 找 Python ≥ 3.11（python3 → python）
#   3. 复用或创建 .venv
#   4. 按 requirements.txt 的哈希判断是否需要重装依赖（.venv/.deps-stamp）
#   5. frontend/dist 缺失时构建前端（需要 Node.js）
#   6. .env 缺失时从 .env.example 复制
#   7. 交给 scripts/launcher.py：找空闲端口 → 起 uvicorn → 就绪后打开浏览器
#
# 端口/健康探测/浏览器打开都在 scripts/launcher.py 里（有单测覆盖），
# 因为这几件事在 shell 里既不可测也不可读。

set -euo pipefail

# ---------------------------------------------------------------------------
# 1) 定位仓库根
# ---------------------------------------------------------------------------
# BASH_SOURCE 可能是相对路径、可能是符号链接，先 cd 到其目录再 pwd -P 取真实路径。
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$SCRIPT_DIR"
cd "$ROOT"

VENV="$ROOT/.venv"
VENV_PY="$VENV/bin/python"
STAMP="$VENV/.deps-stamp"
REQ="$ROOT/backend/requirements.txt"
LAUNCHER="$ROOT/scripts/launcher.py"
MIN_PY="3.11"

log() { printf '%s\n' "$*"; }
die() { printf '[错误] %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 2) 找 Python ≥ 3.11
# ---------------------------------------------------------------------------
BOOT_PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >/dev/null 2>&1; then
      BOOT_PY="$candidate"
      break
    fi
  fi
done
if [ -z "$BOOT_PY" ]; then
  die "需要 Python ${MIN_PY}+，未找到可用的 python3/python。请先安装 Python。"
fi

# ---------------------------------------------------------------------------
# 3) 复用或创建虚拟环境
# ---------------------------------------------------------------------------
if [ -x "$VENV_PY" ]; then
  log "[1/6] 复用已有虚拟环境 .venv（$("$VENV_PY" -V 2>&1)）"
else
  log "[1/6] 创建虚拟环境 .venv ..."
  "$BOOT_PY" -m venv "$VENV"
fi

# ---------------------------------------------------------------------------
# 4) 按 requirements.txt 的哈希决定是否重装依赖
# ---------------------------------------------------------------------------
# 判据是「内容哈希变了」而不是「venv 不存在」：否则每次启动都要等一遍 pip。
# 哈希比较与 pip 调用都在 scripts/launcher.py 的 deps 子命令里，与 start.bat
# 共用同一份实现（有单测覆盖）。
log "[2/6] 检查后端依赖 ..."
"$VENV_PY" "$LAUNCHER" deps --requirements "$REQ" --stamp "$STAMP"

# ---------------------------------------------------------------------------
# 5) 前端产物
# ---------------------------------------------------------------------------
if [ -f "$ROOT/frontend/dist/index.html" ]; then
  log "[3/6] 前端产物已存在（frontend/dist）"
else
  log "[3/6] 前端产物缺失，开始构建 ..."
  if ! command -v npm >/dev/null 2>&1; then
    die "未找到 npm。请先安装 Node.js 18+，或手动执行：cd frontend && npm install && npm run build"
  fi
  (
    cd "$ROOT/frontend"
    if [ -f package-lock.json ]; then
      npm ci
    else
      npm install
    fi
    npm run build
  )
fi

# ---------------------------------------------------------------------------
# 6) .env
# ---------------------------------------------------------------------------
if [ -f "$ROOT/.env" ]; then
  log "[4/6] 复用已有 .env"
else
  cp "$ROOT/.env.example" "$ROOT/.env"
  log "[4/6] 已生成 .env（从 .env.example 复制）"
  log "      如需访问口令：编辑 .env 里的 TIKU_ACCESS_TOKEN 后重新启动。"
fi

# ---------------------------------------------------------------------------
# 7) 起服务（端口 / 就绪探测 / 打开浏览器都在 launcher.py 里）
# ---------------------------------------------------------------------------
log "[5/6] 检查端口与服务 ..."
log "[6/6] 启动 uvicorn（Ctrl+C 停止）"

exec "$VENV_PY" "$LAUNCHER" serve
