# 在飞牛 NAS（fnOS）上部署 private-tiku

本目录提供一套「拉镜像即用」的 Docker Compose 编排，NAS 上**不需要源码**。

- 部署目录：`/vol1/1000/docker/private-tiku`
- 镜像：`303528084/private-tiku`（Docker Hub，**私有仓库**，CI 每次推送 `main` 自动构建）
- 数据：全部落在部署目录的 `data/` 子目录（SQLite 库 + 上传的原始文件）

| 文件 | 用途 |
|---|---|
| `docker-compose.yml` | 单服务编排（`tiku`），端口 / 时区 / 数据目录变量化 |
| `.env.example` | 配置样例，复制成 `.env` 后可改 |

---

## 1. 前置条件

1. **机型必须是 x86_64**。镜像只提供 `linux/amd64`，`arm64` 机型（部分 ARM 版飞牛设备）拉取后会直接
   `exec format error`，无法运行。
2. **Docker Hub 仓库是私有的** → 匿名 `docker pull` 返回 `401 unauthorized`，必须先登录。
3. NAS 已安装 Docker（fnOS 自带 Docker 应用，装好即有 `docker compose` 子命令）。
4. 部署目录所在存储空间有空间余量：镜像约数百 MB，数据按你导入的题量增长。

> 检查 `docker compose` 是否可用：`docker compose version`（应输出 `Docker Compose version v2+`）。

---

## 2. 登录 Docker Hub（只需一次）

```bash
docker login -u 303528084
# 提示 Password 时粘贴 Docker Hub 的 Access Token
```

- 密码**必须是 Access Token**（Docker Hub → Account Settings → Personal access tokens →
  Generate new token，权限选 **Read** 即可拉取），**不要用账号登录密码**。
- 凭证保存在 NAS 的 `~/.docker/config.json`，长期有效；换 token 或失效时重新执行一次即可。

---

## 3. 部署步骤

```bash
mkdir -p /vol1/1000/docker/private-tiku/data
cd /vol1/1000/docker/private-tiku
# 把 docker-compose.yml 与 .env.example 放进这个目录，然后：
cp .env.example .env          # 按需修改端口 / 时区 / 数据目录
docker compose pull
docker compose up -d
docker compose ps
curl -s http://10.10.10.250:8000/api/health
```

最后一条应返回：

```json
{"code":0,"message":"ok","data":{"status":"ok","fts_trigram":true}}
```

`docker compose ps` 里 `STATUS` 应在 30 秒左右从 `health: starting` 变为 `healthy`
（首次启动要建库与 FTS5 索引，编排里给了 15s `start_period`）。

### 目录结构（部署完成后）

```
/vol1/1000/docker/private-tiku
├── docker-compose.yml
├── .env
└── data/                     # 挂载到容器 /app/data
    ├── tiku.db               # SQLite（WAL 模式，含 FTS5 索引）
    ├── tiku.db-wal / -shm
    └── uploads/              # 上传的原始文件，按批次分目录
```

---

## 4. 访问

浏览器打开 **<http://10.10.10.250:8000>** —— 即是题库搜索首页。

接口文档：<http://10.10.10.250:8000/docs>；健康检查：<http://10.10.10.250:8000/api/health>

> 端口被占用时改 `.env` 里的 `TIKU_PORT`（例如 `18000`）后 `docker compose up -d`，
> 访问地址随之变化；容器内部始终监听 8000，不受影响。
>
> 无登录/鉴权（单管理员模式），**不要直接暴露到公网**；需要外网访问请前置带鉴权的反向代理。

---

## 5. 升级与回滚

```bash
cd /vol1/1000/docker/private-tiku
docker compose pull
docker compose up -d          # 只重建有变化的容器，data/ 不受影响
```

回滚到某个具体构建（CI 每次推送都会产出一个 `sha-<短 SHA>` 标签，见 GitHub Actions 运行日志或
Docker Hub 的 Tags 页）：

```bash
# 方式 A：临时用环境变量覆盖（不改 .env）
TIKU_TAG=sha-abc1234 docker compose pull
TIKU_TAG=sha-abc1234 docker compose up -d

# 方式 B：写进 .env 固化
sed -i 's/^TIKU_TAG=.*/TIKU_TAG=sha-abc1234/' .env
docker compose pull && docker compose up -d
```

其它可用标签：`latest`（默认分支最新）、`1.2.3` / `1.2`（版本 tag 推送时生成）。
升级/回滚都只替换镜像，不触碰 `data/`。

---

## 6. 备份与迁移

数据只有一处：宿主机 `./data`（SQLite 库 + 上传的原始文件）。

**冷备份（推荐，最稳）**：

```bash
cd /vol1/1000/docker/private-tiku
docker compose stop
# 输出到上一级目录；按你自己的备份位置改目标路径
tar czf ../private-tiku-backup-$(date +%F).tar.gz docker-compose.yml .env data
docker compose start
```

**热备份**：数据库是 WAL 模式，直接 `cp tiku.db` 可能拿到不一致快照。容器运行中请用 SQLite 的
在线备份：

```bash
docker compose exec tiku python -c "import sqlite3;s=sqlite3.connect('/app/data/tiku.db');d=sqlite3.connect('/app/data/backup.db');s.backup(d);d.close();s.close()"
# 然后把 /app/data/backup.db 拷出来（宿主机上就是 ./data/backup.db）
```

> 上传的原始文件（`data/uploads/`）是普通文件，可随时直接拷贝。

**迁移到新机器**：停容器 → 整体拷贝 `data/` 与 `docker-compose.yml`、`.env` →
新机器上照第 2、3 节登录并 `docker compose up -d` 即可。

**定时备份工具**：仓库新增 `backend/scripts/backup_db.py`，零依赖、可**单独复制**到本部署目录
运行（SQLite 在线备份 + 自动 `PRAGMA integrity_check`，另支持校验 / 列举 / 按份数清理）：

```bash
cd /vol1/1000/docker/private-tiku
python3 backup_db.py --data-dir ./data --backup-dir ../backups --prune --keep 7
python3 backup_db.py --verify ../backups/tiku-20260101-020000.db
```

备份输出务必放在 `data/` **之外**（`data/` 是容器挂载卷，同卷等于没备份），不要放同步盘；
用 fnOS「计划任务」每天定时调用即可。恢复步骤与演练要求见仓库根 `README.md` §10。

---

## 7. 常见问题

| 现象 | 原因与处理 |
|---|---|
| `pull access denied` / `401 Unauthorized` / `unauthorized` | 没有登录，或 token 无 Read 权限，或 token 已过期。重跑 `docker login -u 303528084`（密码填 Access Token） |
| 报 `Additional property name is not allowed` 或提示 `compose` 版本过低 | 宿主上是 Compose v1（`docker-compose`）。本编排要求 `docker compose`（v2+，含 `name:` 顶层字段），升级 NAS 上的 Docker/Compose |
| `exec format error` / 容器起来就退出，`docker compose logs` 提示格式错误 | ARM 机型 + amd64 镜像，无解，需在 x86_64 设备上运行 |
| `bind: address already in use` / 启动失败提示端口占用 | 宿主 8000 被占用，改 `.env` 的 `TIKU_PORT`（如 18000）后 `docker compose up -d` |
| 页面能开但题目的时间/导入时间差 8 小时 | 时区没生效：确认 `.env` 里 `TZ=Asia/Shanghai`，且没有用 `-e TZ=UTC` 之类的方式覆盖；改完 `docker compose up -d` 重建容器 |
| `docker compose ps` 一直是 `unhealthy` | `docker compose logs --tail=100 tiku` 看后端日志；常见是 `data/` 无写权限或磁盘满 |
| 日志文件越来越大 | 编排已限制 `json-file` 单文件 10MB × 3 份；若你改过 `logging`，恢复即可 |

---

## 8. 注意

- **不要把 `data/` 提交进 git，也不要放到 Moment / iCloud / OneDrive 等同步盘**：
  SQLite 在同步盘上被并发改写会破坏一致性，导致库损坏。
- 备份要包含 `.env`（不然新机器上的端口/时区配置就丢了）。
- 需要改「镜像从哪来」时改 `.env` 的 `TIKU_IMAGE` / `TIKU_TAG`，不要改 `docker-compose.yml`。
- 想改成源码构建（不拉镜像）见 `docker-compose.yml` 文件末尾的注释。
