# xray-routing-panel

`xray-routing-panel` 是一个面向开发者的 Xray REALITY 控制面，负责统一管理单一数据面上的入口端口、租户订阅、流量配额和 AI 路由产物。

## 核心能力

- Web 面板和 JSON API 统一管理监听端口、备注、到期时间、流量上限和租户凭据。
- 根据数据库状态生成 `app/xray/runtime/panel-ports.json` 和 `app/xray/runtime/config.json`。
- 通过 Docker、本地二进制或 SSH 管理唯一 `data_plane`，并读取 Xray API / `access.log` 做统计。
- 按小时分析访问域名，生成动态 AI 路由规则、报表和数据库聚合结果。

## 当前架构

- `xray-routing-panel`
  - Flask UI 和 JSON API
  - 维护 `data/panel.db`
  - 渲染、校验、同步并重启单一 `data_plane`
- `xray-reality-local` 或外部数据面
  - 实际承载 `VLESS + REALITY` 流量
  - 数据面模式由 `docker`、`local`、`ssh`、`unmanaged` 四类自动判定
- `xray-ai-domain-manager`
  - 读取 `app/xray/logs/access.log`
  - 输出 `dynamic-routing.json`、小时域名报表和 `ai_domains` 聚合
- `xray-routing-panel-db-backup`
  - 负责 `panel.db` 定时备份

当前首页只展示 `data_plane_status` 和 `ai_routing_status`；AI 不再建模成独立节点。

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

4. 渲染配置并启动完整栈：

```bash
python -m app.xray.render_config
docker compose --profile xray up -d --build
```

默认地址：

- 面板首页：`http://服务器IP:18080`
- 探针页：`http://服务器IP:18080/probe-dashboard`
- 健康检查：`http://服务器IP:18080/healthz`

常见变体：

- 只启动面板：`docker compose up -d --build`
- 远端数据面模式：配置 `DATAPLANE_SSH_TARGET`、`DATAPLANE_CONFIG_PATH`、`DATAPLANE_PANEL_PORTS_PATH`、`DATAPLANE_ACCESS_LOG_PATH`
- 如果控制面和数据面分离，`DATAPLANE_PROBE_HOST` 应改成远端入口 IP 或域名，而不是 `127.0.0.1`

## 代码入口

- [app/web.py](app/web.py): Web 路由、页面和 JSON API
- [app/state.py](app/state.py): 控制逻辑、维护循环、统计同步、探针
- [app/xray/render_config.py](app/xray/render_config.py): 渲染 Xray 服务端和客户端产物
- [app/xray/ai_domain_manager.py](app/xray/ai_domain_manager.py): AI 域名分类、动态路由、报表
- [docker-compose.yml](docker-compose.yml): 本地 compose 栈
- [k8s/](k8s/): K3s 清单

## 常用接口摘要

- `GET /`: 首页
- `GET /api/dashboard`: 首页完整状态
- `POST /api/ports`: 新建监听端口
- `PUT /api/ports/<port_id>`: 更新端口配置
- `POST /api/data-plane/restart`: 重启唯一数据面
- `GET /healthz`: 返回 `{"ok": <bool>, "data_plane_running": <bool>}`
- `GET /probe-dashboard`: TCP 探针监控页
- `GET /ai-domain-dashboard`: AI 域名统计页

完整接口说明见 [docs/api.md](docs/api.md)。

## 核心配置摘要

- 根目录 `.env`：面板地址、管理员认证、AI 路由开关、远端数据面接入参数
- `app/xray/.env`：REALITY 基础参数、AI 上游、分类器和 MCP 配置
- `DATAPLANE_PROBE_HOST`：探针连接目标；远端模式下应指向远端入口而不是本地回环
- `PANEL_HEALTH_REQUIRES_XRAY`：只跑面板时可设为 `0`

完整变量清单见 [docs/configuration.md](docs/configuration.md)。

## 文档导航

- [docs/README.md](docs/README.md): 文档首页
- [docs/architecture.md](docs/architecture.md): 当前架构、模式判定和产物流转
- [docs/development.md](docs/development.md): 本地开发、启动方式和常用命令
- [docs/configuration.md](docs/configuration.md): 根 `.env` 和 `app/xray/.env` 配置说明
- [docs/api.md](docs/api.md): Web/API 路径、请求字段和返回体
- [docs/operations.md](docs/operations.md): 健康检查、统计、探针和排障
- [docs/ai-routing.md](docs/ai-routing.md): AI 路由链路、上游选择和 MCP 工具
- [docs/kubernetes.md](docs/kubernetes.md): K3s 分阶段部署说明

历史分散 README 已收口到 `docs/`；旧位置文件只保留入口和跳转，避免断链。
