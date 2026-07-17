# xray-routing-panel

`xray-routing-panel` 是一个面向开发者和运维的 Xray REALITY 控制面，用来统一管理普通数据面和 AI 节点上的监听端口、租户订阅、流量配额、AI 路由产物，以及基于 Cloudflare 的 DNS 故障切换。

## 核心能力

- 管理后台（Vue + Naive UI 单页应用）和 JSON API 统一管理监听端口、备注、到期时间、流量上限、租户凭据和订阅链接。
- 面向终端用户的**订阅者门户**：客户注册/登录、浏览套餐、下单、上传支付凭证、人工审核开通、查看订阅与续费。每个端口租户即客户，原“租户面板”统一为门户中的订阅详情（Clash/V2Ray/VLESS 订阅链接、流量用量、凭据）。
- 根据数据库状态生成 `app/xray/runtime/panel-ports.json` 和 `app/xray/runtime/config.json`。
- 通过 Docker、本地二进制或 SSH 管理唯一 `data_plane`，并读取 Xray API / `access.log` 做统计。
- 按小时分析访问域名，生成动态 AI 路由规则、报表和数据库聚合结果。
- 基于公网 TCP 探测和 Cloudflare API 做单记录 DNS 故障切换，并支持自动回切。
- 管理后台「监控」标签内嵌 Grafana 图表（数据源自 Prometheus），展示主机系统资源与每端口流量/连接速率；配置/订单等数据仍由面板自身（SQLite）提供。详见 [operations.md](operations.md)。
- 可选启用控制面备用 Xray，配合 DNS 切换让控制面本机接管流量。
- 每天备份 `panel.db`，并可选地加密切片后发布到 npm registry。

## 当前架构

- `xray-routing-panel`（控制面）
  - Flask 作为 JSON API + SPA 壳服务端：托管管理后台 SPA（`/`）、订阅者门户 SPA（`/portal`）、服务端渲染的公共/认证页（`/customer/login`、`/customer/register`、`/plans`、`/checkout`）以及探针/AI 仪表盘
  - 前端为独立的 Vite 工程（`frontend/`），构建出 `app/static/admin/*` 与 `app/static/portal/*`
  - 维护 `data/panel.db`（客户、套餐、订单、服务订阅、支付凭证，以及端口/流量/AI/DNS 状态）
  - 通过 SSH 纳管两个远端节点：
    - **普通数据面**：渲染、校验、同步并重启唯一 `data_plane`
    - **AI 节点**：渲染、推送并重启 `ai_node`（接收数据面转发的 AI 流量，freedom 直出）
  - 维护 `dns_failover_state` / `dns_failover_history`
- 普通数据面（`xray-reality-local` 或远端数据面）
  - 实际承载 `VLESS + REALITY` 流量
  - 数据面模式由 `docker`、`local`、`ssh`、`unmanaged` 四类自动判定
  - 运行 `ai_domain_manager`，生成 `dynamic-routing.json` 将 AI 域名流量转发到 AI 节点
- AI 节点（远端独立机器）
  - 运行 VLESS + REALITY Xray，监听 `AI_UPSTREAM_PORT`，接收数据面转发的 AI 流量
  - freedom 直出，不做域名分类、不运行 `ai_domain_manager`
  - 复用普通数据面同一套 REALITY 参数
  - 详见 [ai-node-deployment.md](ai-node-deployment.md)
- `xray-reality-backup`（控制面备用 Xray）
  - 可选的控制面备用 Xray，双模式运行：
    - **relay 模式**（AI 节点正常时）：将所有流量转发到 AI 节点
    - **直出模式**（AI 节点也故障时）：freedom 直出
  - 在 DNS 切换后接管入口流量
  - 详见 [dns-failover.md](dns-failover.md)
- `xray-ai-domain-manager`
  - 读取 `app/xray/logs/access.log`
  - 输出 `dynamic-routing.json`、小时域名报表和 `ai_domains` 聚合
  - AI 上游不可达时自动回退（删除 `dynamic-routing.json`，流量回退数据面直出）
- `xray-routing-panel-db-backup`
  - 负责 `panel.db` 定时备份，并在启用时触发上传链路
- `db-backup-uploader`
  - 负责将数据库备份加密、切片、发布和恢复

首页当前聚合展示：

- 三节点状态（普通数据面、AI 节点、控制面备用）和当前流量导向
- `data_plane_status`
- `ai_routing_status`
- `dns_failover_status`

AI 节点作为独立受管节点纳管。AI 节点故障不涉及 DNS 切换，由 `ai_domain_manager` 自动回退。数据面故障时 DNS 切到控制面备用，根据 AI 节点健康度自动选择 relay 或直出模式。

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

> 前端构建：`docker compose --build` 使用多阶段 Dockerfile，会在 `node:20` 构建阶段自动 `npm ci && npm run build` 生成 SPA 产物并拷入运行镜像；打包产物不再提交到仓库。**本地非 Docker 运行**需先手动构建一次：
>
> ```bash
> cd frontend && npm ci && npm run build   # 生成 app/static/{admin,portal}
> ```

默认地址：

- 管理后台：`http://服务器IP:18080/`
- 订阅者门户：`http://服务器IP:18080/portal`
- 公共套餐页：`http://服务器IP:18080/plans`
- 租户订阅直达：`http://服务器IP:18080/tenant/<tenant_token>`
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

### AI 节点

AI 节点是远端独立机器上的 VLESS + REALITY Xray，接收数据面转发的 AI 流量并 freedom 直出。详见 [ai-node-deployment.md](ai-node-deployment.md)。

至少配置：

- `AI_NODE_SSH_TARGET`
- `AI_NODE_SSH_OPTIONS`
- `AI_NODE_CONFIG_PATH`
- `AI_NODE_PROBE_HOST`
- `AI_UPSTREAM_HOST` / `AI_UPSTREAM_PORT`（在 `app/xray/.env` 中）

### 控制面备用 Xray

适合主数据面在远端、控制面本机作为备用接管节点的场景。双模式运行：AI 节点正常时 relay 到 AI 节点，AI 节点也故障时 freedom 直出。详见 [dns-failover.md](dns-failover.md)。

- 启用 `CONTROL_PLANE_BACKUP_XRAY_ENABLED=1`
- 启动 `xray-reality-backup`：`docker compose --profile backup-xray up -d xray-reality-backup`
- 如需自动推导备用 IP，可留空 `DNS_FAILOVER_BACKUP_CONTENT`
- `CONTROL_PLANE_BACKUP_UPSTREAM_URL` 在 AI 节点纳管时从 AI 节点公网 IP + REALITY 参数自动派生

重要限制：

- `xray-reality-local` 和 `xray-reality-backup` 如果绑定同一端口，不能在同一台机器上同时接管同一个入口
- 常见用法是"主数据面在远端，控制面本机只作为备用"

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
- DNS 故障切换运行在独立 worker 中，不会被数据面 SSH、日志同步或流量统计阻塞
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

管理后台（需管理员会话 / Basic 认证）：

- `GET /`: 管理后台 SPA 壳
- `GET /api/dashboard`: 首页完整状态
- `POST /api/ports`: 新建监听端口
- `PUT /api/ports/<port_id>`: 更新端口配置
- `POST /api/plans` / `PUT /api/plans/<id>`: 套餐增改
- `GET /api/orders` / `POST /api/orders/<id>/{fulfill,reject,cancel}`: 订单审核与开通
- `POST /api/data-plane/restart`: 重启数据面
- `GET /api/ai-node/status`: 获取 AI 节点状态
- `POST /api/ai-node/restart`: 重启 AI 节点
- `GET /api/dns-failover`: 获取 DNS 故障切换状态
- `POST /api/dns-failover/check`: 立即执行一次 DNS 检测
- `POST /api/dns-failover/switch`: 手动切主备

订阅者门户（客户会话）：

- `GET /portal`、`GET /portal/<path>`: 门户 SPA 壳（vue-router history）
- `GET /api/customer/{me,overview,subscriptions[/<id>],orders[/<no>],plans}`: 门户数据
- `POST /api/customer/orders`、`.../payment-proof`、`.../<id>/renew`: 下单、传支付凭证、续费
- `POST /api/customer/auth/{login,register,logout}`: 客户认证

租户直达（token / 每端口凭据）：

- `GET /tenant/<tenant_token>`: 门户单订阅只读模式壳
- `GET /api/tenant/<tenant_token>/subscription`、`POST .../login`

公共与其他：

- `GET /healthz`: 返回 `{"ok": <bool>, "data_plane_running": <bool>}`
- `GET /metrics`: Prometheus 文本格式指标（需 `METRICS_TOKEN`，`Authorization: Bearer <token>`）；管理后台「监控」标签把这些指标经 Grafana 内嵌出图（需 `GRAFANA_PUBLIC_URL`）
- `GET /probe-dashboard`: TCP 探针监控页
- `GET /ai-domain-dashboard`: AI 域名统计页

完整接口说明见 [api.md](api.md)。

## 核心配置摘要

- 根目录 `.env`
  - 面板地址、管理员认证、数据面接入参数、DNS 故障切换
- `DNS_FAILOVER_*` / `CF_*`
  - Cloudflare DNS 故障切换和自动回切
- `CONTROL_PLANE_BACKUP_XRAY_ENABLED`
  - 是否启用"控制面本机公网 IP + 备用 Xray"自动备用模式（relay / 直出双模式）
- `AI_NODE_*`
  - AI 节点 SSH 纳管参数，详见 [ai-node-deployment.md](ai-node-deployment.md)
- `DB_BACKUP_UPLOADER_*`
  - 数据库备份上传组件配置
- `app/xray/.env`
  - REALITY 基础参数、AI 上游、分类器和 MCP 配置

完整变量清单见 [configuration.md](configuration.md)。

## 代码入口

后端（`app/` 已包化，`app/panel.py` 为入口，导出 `app`/`state`/`main`）：

- [../app/web/](../app/web/): app factory（`create_app`）+ 按域分的视图模块（`admin_views`、`admin_api`、`customer_api`、`customer_views`、`portal_views`、`tenant_views`、`subscription_views`、`health`）与共享 `core.py`（presenter、auth 守卫、`@route` 收集器）
- [../app/state/](../app/state/): `PanelState` facade，组合域 service（`CoreService`、`PortsService`、`TrafficService`、`ProbesService`、`DnsFailoverService`、`AiRoutingService`、`CommerceService`、`DiagnosticsService`）——控制逻辑、维护循环、统计同步、探针、DNS 故障切换、商业化。持有 `data_plane` 和 `ai_node` 两个受管节点控制器
- [../app/config/](../app/config/) / [../app/auth/](../app/auth/): 配置常量/解析器、三套会话（管理员/租户/客户）与 CSRF
- [../app/dns_failover.py](../app/dns_failover.py): Cloudflare API 客户端与切换策略
- [../app/xray/render_config.py](../app/xray/render_config.py): 渲染 Xray 服务端和客户端产物（普通数据面、AI 节点、控制面备用）
- [../app/xray/node_control.py](../app/xray/node_control.py): `DataPlaneController` / `ManagedNodeController`——受管节点控制器（SSH 推送、重启、探测），统一用于普通数据面和 AI 节点
- [../app/xray/ai_domain_manager.py](../app/xray/ai_domain_manager.py): AI 域名分类、动态路由、报表

前端与运维：

- [../frontend/](../frontend/): Vite + Vue 3 + Naive UI + Vitest 工程；`src/shared/`（设计令牌、apiClient、共享组件）、`src/admin/`（后台 SPA）、`src/portal/`（订阅者门户 SPA）。`npm run build` 出 `app/static/{admin,portal}`，`npm test` 跑 Vitest
- [db-backup-uploader.md](db-backup-uploader.md): 数据库备份上传组件
- [../Dockerfile](../Dockerfile): 多阶段构建（node 构建 SPA + pip 安装 Python 依赖）
- [../docker-compose.yml](../docker-compose.yml): 本地 compose 栈
- [../k8s/](../k8s/): K3s 清单

## 开发与测试

```bash
# 后端测试（Python，需先装依赖；项目用 pytest 跑现有 unittest）
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -q

# 前端：构建产物 + 组件/单元测试
cd frontend && npm ci
npm run build     # 生成 app/static/{admin,portal}
npm test          # Vitest
```

## 文档导航

- [根目录 README](../README.md)：仓库根目录入口
- [panel-migration.md](panel-migration.md): 面板迁移
- [fault-tolerance.md](fault-tolerance.md): 三节点故障容错边界
