# xray-routing-panel

`xray-routing-panel` 是一个面向开发者和运维的 Xray REALITY 控制面，用来统一管理单一数据面上的监听端口、租户订阅、流量配额、AI 路由产物，以及基于 Cloudflare 的 DNS 故障切换。

## 核心能力

- Web 面板和 JSON API 统一管理监听端口、备注、到期时间、流量上限、租户凭据和订阅链接。
- 根据数据库状态生成 `app/xray/runtime/panel-ports.json` 和 `app/xray/runtime/config.json`。
- 通过 Docker、本地二进制或 SSH 管理唯一 `data_plane`，并读取 Xray API / `access.log` 做统计。
- 按小时分析访问域名，生成动态 AI 路由规则、报表和数据库聚合结果。
- 基于公网 TCP 探测和 Cloudflare API 做单记录 DNS 故障切换，并支持自动回切。
- 可选启用控制面备用 Xray，配合 DNS 切换让控制面本机接管流量。
- 每天备份 `panel.db`，并可选地加密切片后发布到 npm registry。

## 当前架构

- `xray-routing-panel`
  - Flask UI 和 JSON API
  - 维护 `data/panel.db`
  - 渲染、校验、同步并重启单一 `data_plane`
  - 维护 `dns_failover_state` / `dns_failover_history`
- `xray-reality-local` 或外部数据面
  - 实际承载 `VLESS + REALITY` 流量
  - 数据面模式由 `docker`、`local`、`ssh`、`unmanaged` 四类自动判定
- `xray-reality-backup`
  - 可选的控制面备用 Xray
  - 复用同一份 REALITY 配置，在 DNS 切换后接管入口流量
- `xray-ai-domain-manager`
  - 读取 `app/xray/logs/access.log`
  - 输出 `dynamic-routing.json`、小时域名报表和 `ai_domains` 聚合
- `xray-routing-panel-db-backup`
  - 负责 `panel.db` 定时备份，并在启用时触发上传链路
- `db-backup-uploader`
  - 负责将数据库备份加密、切片、发布和恢复

首页当前聚合展示：

- `data_plane_status`
- `ai_routing_status`
- `dns_failover_status`

AI 不再建模成独立节点，且 AI 状态不参与任何 DNS 故障切换判断。

## 快速开始

1. 生成 REALITY 参数：

```bash
./app/xray/generate-secrets.sh
```

2. 准备 Xray 配置：

```bash
cp app/xray/.env.example app/xray/.env
```

至少填写：

- `XRAY_PUBLIC_HOST`
- `XRAY_CLIENT_UUID`
- `XRAY_REALITY_PRIVATE_KEY`
- `XRAY_REALITY_PUBLIC_KEY`
- `XRAY_REALITY_SHORT_ID`
- `XRAY_SERVER_NAME`
- `XRAY_DEST`

3. 准备面板覆盖项：

```bash
cp .env.example .env
```

常用项：

- `PANEL_PUBLIC_URL`
- `PANEL_USERNAME`
- `PANEL_PASSWORD`
- `PANEL_SECRET_KEY`
- `DATAPLANE_SSH_TARGET`
- `DATAPLANE_PROBE_HOST`
- `DNS_FAILOVER_ENABLED`
- `DB_BACKUP_UPLOADER_ENABLED`

4. 渲染配置：

```bash
python -m app.xray.render_config
```

5. 按部署模式启动：

- 只启动面板和数据库备份：

```bash
docker compose up -d --build
```

- 启动本地完整栈（面板 + 本地 Xray + AI 路由）：

```bash
docker compose --profile xray up -d --build
```

- 启动控制面备用 Xray：

```bash
docker compose --profile backup-xray up -d xray-reality-backup
```

默认地址：

- 面板首页：`http://服务器IP:18080`
- 探针页：`http://服务器IP:18080/probe-dashboard`
- AI 域名页：`http://服务器IP:18080/ai-domain-dashboard`
- 健康检查：`http://服务器IP:18080/healthz`

## 常见部署变体

### 只跑控制面

- 使用 `docker compose up -d --build`
- 如不依赖本地 Xray，建议设置 `PANEL_HEALTH_REQUIRES_XRAY=0`

### 远端数据面

至少配置：

- `DATAPLANE_SSH_TARGET`
- `DATAPLANE_CONFIG_PATH`
- `DATAPLANE_PANEL_PORTS_PATH`
- `DATAPLANE_ACCESS_LOG_PATH`
- `DATAPLANE_PROBE_HOST`

注意：

- 控制面会先在本地渲染，再通过 SSH 上传产物
- 如果控制面和数据面分离，`DATAPLANE_PROBE_HOST` 应改成远端入口 IP 或域名，而不是 `127.0.0.1`

### 控制面备用 Xray

适合主数据面在远端、控制面本机作为备用接管节点的场景。

- 启用 `CONTROL_PLANE_BACKUP_XRAY_ENABLED=1`
- 启动 `xray-reality-backup`
- 如需自动推导备用 IP，可留空 `DNS_FAILOVER_BACKUP_CONTENT`

重要限制：

- `xray-reality-local` 和 `xray-reality-backup` 如果绑定同一端口，不能在同一台机器上同时接管同一个入口
- 常见用法是“主数据面在远端，控制面本机只作为备用”

## DNS 故障切换快速配置

最小可用配置示例：

```env
DNS_FAILOVER_ENABLED=1
DNS_FAILOVER_INTERVAL=10
DNS_FAILOVER_TIMEOUT=2
DNS_FAILOVER_FAILURE_THRESHOLD=2
DNS_FAILOVER_RECOVERY_THRESHOLD=2

DNS_FAILOVER_PROBE_HOST=edge.example.com
DNS_FAILOVER_PROBE_PORT=443

CF_API_TOKEN=replace_me
CF_ZONE_ID=replace_me
CF_DNS_RECORD_ID=replace_me
CF_DNS_RECORD_TYPE=A
CF_DNS_RECORD_NAME=edge.example.com
CF_DNS_RECORD_PROXIED=0
CF_DNS_RECORD_TTL=60

# 留空时自动获取主数据面公网 IP
DNS_FAILOVER_PRIMARY_CONTENT=

CONTROL_PLANE_BACKUP_XRAY_ENABLED=1

# 留空时自动获取控制面本机公网 IP
DNS_FAILOVER_BACKUP_CONTENT=
DNS_FAILOVER_BACKUP_LABEL=控制面备用Xray
```

行为说明：

- 自动切换只看 `DNS_FAILOVER_PROBE_HOST:DNS_FAILOVER_PROBE_PORT`
- 连续失败达到阈值时切到备用，连续成功达到阈值时自动回切
- `DNS_FAILOVER_PRIMARY_CONTENT` 留空时，控制面会自动获取当前数据面的公网 IP
- `DNS_FAILOVER_BACKUP_CONTENT` 留空时，只有在 `CONTROL_PLANE_BACKUP_XRAY_ENABLED=1` 时才会自动获取控制面本机公网 IP
- AI 路由状态只展示，不参与切换决策
- 对 REALITY 这类直连流量，建议保持 `CF_DNS_RECORD_TTL=60` 以尽快生效

相关接口：

- `GET /api/dns-failover`
- `POST /api/dns-failover/check`
- `POST /api/dns-failover/switch`

## 数据库备份上传

默认情况下，`xray-routing-panel-db-backup` 每天 `03:00 UTC` 生成一次本地 SQLite 备份。

如需在备份完成后自动加密分片并上传到 npm：

- 在根 `.env` 中设置 `DB_BACKUP_UPLOADER_ENABLED=1`
- 设置 `DB_BACKUP_UPLOADER_PASSWORD`
- 按需设置 `DB_BACKUP_UPLOADER_SCOPE`
- 把 npm 认证文件放到 `data/db-backup-uploader/.npmrc`，或改写 `DB_BACKUP_UPLOADER_NPMRC_PATH`

先验证链路而不真实发布：

```bash
DB_BACKUP_UPLOADER_ENABLED=1 DB_BACKUP_UPLOADER_DRY_RUN=1 \
docker compose up -d --build xray-routing-panel-db-backup
```

手动触发一轮“备份后上传”：

```bash
docker compose run --rm xray-routing-panel-db-backup \
  python3 /app/scripts/run_db_backup_cycle.py
```

## 常用接口摘要

- `GET /`: 首页
- `GET /api/dashboard`: 首页完整状态
- `POST /api/ports`: 新建监听端口
- `PUT /api/ports/<port_id>`: 更新端口配置
- `POST /api/data-plane/restart`: 重启唯一数据面
- `GET /api/dns-failover`: 获取 DNS 故障切换状态
- `POST /api/dns-failover/check`: 立即执行一次 DNS 检测
- `POST /api/dns-failover/switch`: 手动切主备
- `GET /healthz`: 返回 `{"ok": <bool>, "data_plane_running": <bool>}`
- `GET /probe-dashboard`: TCP 探针监控页
- `GET /ai-domain-dashboard`: AI 域名统计页

完整接口说明见 [docs/api.md](docs/api.md)。

## 核心配置摘要

- 根目录 `.env`
  - 面板地址、管理员认证、数据面接入参数、DNS 故障切换
- `DNS_FAILOVER_*` / `CF_*`
  - Cloudflare DNS 故障切换和自动回切
- `CONTROL_PLANE_BACKUP_XRAY_ENABLED`
  - 是否启用“控制面本机公网 IP + 备用 Xray”自动备用模式
- `DB_BACKUP_UPLOADER_*`
  - 数据库备份上传组件配置
- `app/xray/.env`
  - REALITY 基础参数、AI 上游、分类器和 MCP 配置

完整变量清单见 [docs/configuration.md](docs/configuration.md)。

## 代码入口

- [app/web.py](app/web.py): Web 路由、页面和 JSON API
- [app/state.py](app/state.py): 控制逻辑、维护循环、统计同步、探针、DNS 故障切换
- [app/dns_failover.py](app/dns_failover.py): Cloudflare API 客户端与切换策略
- [app/xray/render_config.py](app/xray/render_config.py): 渲染 Xray 服务端和客户端产物
- [app/xray/ai_domain_manager.py](app/xray/ai_domain_manager.py): AI 域名分类、动态路由、报表
- [components/db-backup-uploader/README.md](components/db-backup-uploader/README.md): 数据库备份上传组件
- [docker-compose.yml](docker-compose.yml): 本地 compose 栈
- [k8s/](k8s/): K3s 清单

## 文档导航

- [docs/README.md](docs/README.md): 文档首页
- [docs/architecture.md](docs/architecture.md): 当前架构、模式判定、产物流转、DNS failover 接线
- [docs/development.md](docs/development.md): 本地开发、启动方式和常用命令
- [docs/configuration.md](docs/configuration.md): 根 `.env`、`app/xray/.env` 和 DNS failover 配置说明
- [docs/api.md](docs/api.md): Web/API 路径、请求字段和返回体
- [docs/operations.md](docs/operations.md): 健康检查、统计、探针、DNS 切换和排障
- [docs/ai-routing.md](docs/ai-routing.md): AI 路由链路、上游选择和 MCP 工具
- [docs/kubernetes.md](docs/kubernetes.md): K3s 分阶段部署说明

历史分散 README 已收口到 `docs/`；旧位置文件只保留入口和跳转，避免断链。
