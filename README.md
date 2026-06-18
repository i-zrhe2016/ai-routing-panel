# xray-routing-panel

`xray-routing-panel` 是一套以 `Xray REALITY` 为数据面的控制面板，当前实现重点是：

- 用 Web 面板维护入口端口、到期时间、流量上限、租户凭据和订阅链接
- 直接生成 Xray 多端口 `inbounds`，不再依赖 `nginx`
- 通过 Xray API 和 `access.log` 维护连接数、流量统计与健康状态
- 按小时分析访问域名，自动生成 AI 分流规则

> 当前版本已经移除 `nginx`。如果你在仓库里还看到旧的 `stream-access.log`、历史备份文件名或旧文档残留，那些都属于历史遗留，不是当前运行链路的一部分。

## 当前架构

- `xray-routing-panel`
  - Flask 面板和 JSON API
  - 管理 `data/panel.db`
  - 根据数据库中的有效端口生成 `app/xray/runtime/panel-ports.json`
  - 调用 `python -m app.xray.render_config` 渲染 `app/xray/runtime/config.json`
  - 在本地或通过 SSH 远程校验、同步并重启默认数据面节点
  - 可独立显示并重启 AI 节点
- `xray-reality`
  - 实际承载 `VLESS + REALITY` 入口流量
  - 默认监听 `0.0.0.0:443`
  - 暴露 Xray API，默认 `127.0.0.1:10085`
- `xray-ai-domain-manager`
  - 定时分析 `app/xray/logs/access.log`
  - 识别 AI 域名并生成 `dynamic-routing.json`
  - 规则变化时重新渲染配置并重启 `xray-reality`
- `xray-routing-panel-db-backup`
  - 定时备份 `panel.db`

## 适合场景

- 你需要一个可视化面板来管理 Xray 入口端口和租户订阅
- 你希望基于真实访问日志持续收敛 AI 域名，而不是手工维护静态规则
- 你希望 AI 相关流量自动改走专用上游，其余流量保持默认路由
- 你需要按端口设置到期时间、流量上限和独立租户凭据

## 运行模式

### 1. 推荐：完整栈

```bash
python -m app.xray.render_config
docker compose --profile xray up -d --build
```

会启动：

- `xray-routing-panel`
- `xray-routing-panel-db-backup`
- `xray-reality`
- `xray-ai-domain-manager`

这是仓库当前的主路径，也是文档默认假设的部署方式。

### 2. 只启动面板

```bash
docker compose up -d --build
```

只会启动：

- `xray-routing-panel`
- `xray-routing-panel-db-backup`

这种模式下：

- 面板、数据库、租户凭据、订阅地址和备份仍然可用
- 面板仍会渲染 Xray 配置文件
- 但如果没有额外运行中的 Xray 数据面，新增的端口不会实际承载代理流量
- 默认 `GET /healthz` 仍要求 Xray 可用；如果你只想把它当管理 UI，可设置 `PANEL_HEALTH_REQUIRES_XRAY=0`

### 3. 外部 / 本地 Xray 模式

如果你不想使用 `docker-compose.yml` 里的 `xray-reality` 容器，也可以让面板对接宿主机自己的 Xray：

- 设置 `XRAY_LOCAL_BIN=/path/to/xray`
- 设置 `XRAY_API_SERVER=127.0.0.1:10085`
- 让你的本地 Xray 进程自行加载 `app/xray/runtime/config.json`

注意：

- 这种模式下，面板会渲染配置并校验 `xray run -test`
- 但**不会自动重启**你自己的本地 Xray 进程
- Xray 进程的启动、重载和守护需要你自己负责

### 4. 控制面 / 数据面分离模式

如果你要把控制面独立部署，并远程控制默认节点和 AI 节点：

- 默认节点至少配置：
  - `DEFAULT_NODE_SSH_TARGET`
  - `DEFAULT_NODE_CONFIG_PATH`
  - `DEFAULT_NODE_PANEL_PORTS_PATH`
  - `DEFAULT_NODE_ACCESS_LOG_PATH`
  - `DEFAULT_NODE_RESTART_COMMAND`，或者配置可访问的 `DEFAULT_NODE_CONTAINER_NAME`
- AI 节点按需配置：
  - `AI_NODE_HOST`
  - `AI_NODE_PORT`
  - `AI_NODE_SSH_TARGET`
  - `AI_NODE_RESTART_COMMAND`，或者配置 `AI_NODE_CONTAINER_NAME`

在这个模式下：

- 控制面先在本地渲染 `config.json` / `panel-ports.json`
- 再通过 SSH 上传到默认数据面节点
- 默认节点连接数统计通过 SSH 增量读取远端 `access.log`
- 默认节点字节流量统计继续走远端 Xray API `statsquery`
- 首页会显示“默认节点”和“AI 节点”两个独立状态，并支持分别重启

## 快速开始

### 1. 前置条件

- Linux 宿主机
- 已安装 Docker 和 Docker Compose
- 以下端口未被占用：
  - `18080`，面板端口
  - `443`，Xray 默认监听端口
  - `31098`，默认初始化端口和探针观察端口
- 如果你想启用自动域名分类，满足下面至少一项：
  - 宿主机安装了 `codex` CLI，且 `codex login status` 可用
  - 在 `app/xray/.env` 中配置可用的 OpenAI 兼容接口

### 2. 生成并填写 Xray REALITY 参数

```bash
./app/xray/generate-secrets.sh
cp app/xray/.env.example app/xray/.env
```

至少需要修改这些值：

- `XRAY_PUBLIC_HOST`
- `XRAY_CLIENT_UUID`
- `XRAY_REALITY_PRIVATE_KEY`
- `XRAY_REALITY_PUBLIC_KEY`
- `XRAY_REALITY_SHORT_ID`
- `XRAY_SERVER_NAME`
- `XRAY_DEST`

如果你希望客户端连接的是面板管理的入口端口，例如 `31098`：

- 保持 `XRAY_LISTEN_PORT=443`
- 设置 `XRAY_PUBLIC_PORT=31098`
- 保持 `SEED_LISTEN_PORT=31098`，或者在面板中创建自己的入口端口

### 3. 可选：设置面板公开地址和管理员认证

```bash
cp .env.example .env
```

根目录 `.env` 默认只提供最常用的覆盖项：

- `PANEL_PUBLIC_URL`
- `PANEL_USERNAME`
- `PANEL_PASSWORD`
- `PANEL_SECRET_KEY`

### 4. 渲染配置并启动

```bash
python -m app.xray.render_config
docker compose --profile xray up -d --build
```

默认访问地址：

- 面板首页：`http://服务器IP:18080`
- 探针监控页：`http://服务器IP:18080/probe-dashboard`
- 健康检查：`http://服务器IP:18080/healthz`

### 5. 验证是否正常

```bash
docker compose ps
curl http://127.0.0.1:18080/healthz
docker compose --profile xray logs -f xray-reality
docker compose --profile xray logs -f xray-ai-domain-manager
```

如果你要立即执行一次 AI 域名分析：

```bash
docker compose --profile xray run --rm xray-ai-domain-manager python -m app.xray.ai_domain_manager --once
```

常用检查文件：

- `app/xray/runtime/config.json`
- `app/xray/runtime/client-test.json`
- `app/xray/runtime/panel-ports.json`
- `app/xray/runtime/dynamic-routing.json`
- `app/xray/reports/hourly-domains/latest.txt`
- `app/xray/reports/hourly-domains/latest.json`

## 面板能管理什么

当前首页支持这些核心操作：

- 新建监听端口
- 编辑备注、到期时间、流量上限
- 启用 / 停用 / 删除端口
- 为每个端口自动生成：
  - `tenant_token`
  - `subscription_token`
  - `tenant_username`
  - `tenant_password`
- 生成每个端口独立的：
  - `Clash` 订阅
  - `V2Ray` 订阅
  - `vless://` 直连分享链接
- 重置租户面板地址、租户账号密码和订阅地址
- 查看总流量、今日流量、连接数和最近探针结果

补充说明：

- 当前所有端口记录在启动时会被标准化为 `DEFAULT_UPSTREAM_HOST:DEFAULT_UPSTREAM_PORT`
- 当前 UI / API 不提供“每个端口配置不同上游”的入口
- 面板当前更像“Xray 入口端口控制面”，而不是通用反代编排器

## 认证、租户和订阅路径

管理员认证规则：

- 未设置 `PANEL_USERNAME` / `PANEL_PASSWORD` 时，首页和 `/api/*` 默认无需登录
- 只要两者任意一个被设置，首页、探针页和 `/api/*` 就需要管理员认证
- Web 页面支持表单登录
- API 同时支持 `Authorization: Basic ...`
- `GET /healthz` 永远不要求登录

租户相关路径：

- 管理员登录页：`/login`
- 租户登录页：`/tenant/<tenant_token>/login`
- 租户面板：`/tenant/<tenant_token>`
- 租户订阅：
  - `/tenant-subscriptions/<subscription_token>`
  - `/tenant-subscriptions/<subscription_token>/clash`
  - `/tenant-subscriptions/<subscription_token>/v2ray`
- 历史兼容全局订阅：
  - `/<token>/<listen_port>`
  - `/<token>/<listen_port>/clash`
  - `/<token>/<listen_port>/v2ray`

注意：

- 每个端口都有独立租户用户名和密码
- 管理员登录后可直接访问租户面板
- 订阅内容依赖 `app/xray/runtime/client-test.json`
- 如果还没执行 `python -m app.xray.render_config`，订阅和分享链接会显示不可用

## 流量统计、配额和健康检查

当前统计链路分成两部分：

- 连接数来自 `app/xray/logs/access.log`
  - 只统计带 `panel-<listen_port>` inbound tag 的访问日志
- 字节流量来自 Xray API `statsquery`
  - 查询模式是 `inbound>>>panel-`
  - 每次读取后会执行 `-reset`

因此：

- `total_connections` 依赖访问日志增量同步
- `total_bytes_sent` / `total_bytes_received` 依赖 Xray API 周期拉取
- 流量上限判断使用：

```text
累计下行 + 累计上行
```

端口自动维护规则：

- 到期端口会在维护循环中自动删除
- 达到流量上限的端口会自动停用
- “重置流量并启用”会清零累计流量与当日流量；连接数不会被清零

健康检查：

- 接口：`GET /healthz`
- 返回体：`{"ok": <bool>, "xray_running": <bool>}`
- 默认要求 Xray 正在运行；如需只检查面板进程本身，可设置 `PANEL_HEALTH_REQUIRES_XRAY=0`

## 探针监控

当 `PROBE_ENABLED=1` 时，服务会周期性对 `XRAY_PROBE_HOST:<listen_port>` 做 TCP 连通性探测，并提供独立监控页：

- 页面：`/probe-dashboard`
- 时间范围：`1h`、`24h`、`7d`
- 默认显示：
  - 如果设置了 `PROBE_TEST_LISTEN_PORT`，固定显示该端口
  - 否则自动选第一条已启用端口

这个监控页只反映入口 TCP 可达性，不替代业务级探活。

## AI Routing

完整实现细节见 [app/xray/README.md](app/xray/README.md)。根目录 README 只保留当前主链路：

1. `xray-reality` 把访问域名写入 `app/xray/logs/access.log`
2. `xray-ai-domain-manager` 读取最近一小时日志
3. 先应用内建 AI 域名规则
4. 对未知域名优先调用本机 `codex`
5. 如本机 `codex` 不可用，再回退到 OpenAI 兼容接口
6. 生成并写回：
   - `app/xray/runtime/ai-domain-decisions.json`
   - `app/xray/runtime/dynamic-routing.json`
   - `app/xray/reports/hourly-domains/latest.{txt,json}`
   - `data/panel.db` 中的 `ai_domains`、`ai_domain_observations`
7. 路由变化时重新渲染配置并重启 `xray-reality`

常用命令：

```bash
docker compose --profile xray run --rm xray-ai-domain-manager python -m app.xray.ai_domain_manager --once
docker compose --profile xray logs -f xray-ai-domain-manager
cat app/xray/reports/hourly-domains/latest.txt
```

默认 compose 还依赖宿主机这些路径来运行 `codex` 分类：

- `/root/.codex`
- `/root/.nvm/versions/node`

如果你的环境不是这两个路径，需要改 `docker-compose.yml` 里的挂载。

## Google Search + OpenRouter MCP

仓库附带了一个可选 MCP server，用于辅助“未分类域名”的人工 / 半自动归类。

入口：

```bash
python -m app.xray.google_search_mcp
```

或：

```bash
python scripts/google_search_mcp.py
```

它当前是**独立工具**，不是主链路里的自动步骤。默认提供 3 个 tools：

- `collect_uncategorized_domains`
- `search_domains_with_google`
- `classify_domains_with_google`

特点：

- Google 搜索结果直接抓取 HTML 页面，不依赖 Google Search API
- 分类使用 OpenRouter 上的 `openai/gpt-5-nano`
- 最低要求：`OPENROUTER_API_KEY`

## 常用 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/dashboard` | 获取首页完整状态 |
| `POST` | `/api/ports` | 新建监听端口 |
| `PUT` | `/api/ports/<port_id>` | 更新端口配置 |
| `POST` | `/api/ports/<port_id>/toggle` | 启用或停用端口 |
| `DELETE` | `/api/ports/<port_id>` | 删除端口 |
| `POST` | `/api/ports/<port_id>/reset-traffic` | 重置该端口流量 |
| `POST` | `/api/ports/<port_id>/rotate-tenant-token` | 重置租户面板地址 |
| `POST` | `/api/ports/<port_id>/rotate-tenant-credentials` | 重置租户用户名和密码 |
| `POST` | `/api/ports/<port_id>/rotate-subscription-token` | 重置租户订阅地址 |
| `POST` | `/api/subscriptions/rotate` | 重置历史兼容的全局订阅 token |
| `POST` | `/api/nodes/default/restart` | 重启默认数据面节点 |
| `POST` | `/api/nodes/ai/restart` | 重启 AI 节点 |

创建 / 更新端口的请求字段：

- `listen_port`：必填，`1-65535`
- `expires_at`：可选，例如 `2026-06-30T20:00`
- `traffic_limit`：可选，例如 `10G`、`500MB`、`1048576`
- `note`：可选，最多 `200` 字符

示例：

```bash
curl -u admin:secret http://127.0.0.1:18080/api/dashboard

curl -u admin:secret \
  -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:18080/api/ports \
  -d '{
    "listen_port": 32001,
    "expires_at": "2026-06-30T20:00",
    "traffic_limit": "20G",
    "note": "demo-tenant"
  }'
```

大部分写操作成功后都会返回：

```json
{
  "ok": true,
  "message": "...",
  "level": "success",
  "dashboard": {
    "...": "最新首页状态"
  }
}
```

## 环境变量

### 面板 / 运行时变量

下表列的是根服务最常用的变量；容器内路径类覆盖仍以 [docker-compose.yml](docker-compose.yml) 为准。

| 变量名 | 默认值 | 说明 |
| --- | --- | --- |
| `PANEL_HOST` | `0.0.0.0` | Flask 面板监听地址 |
| `PANEL_PORT` | `18080` | Flask 面板监听端口 |
| `PANEL_PUBLIC_URL` | 空 | 面板对外展示地址；也影响生成的订阅链接和安全 Cookie |
| `PANEL_USERNAME` | 空 | 管理员用户名 |
| `PANEL_PASSWORD` | 空 | 管理员密码 |
| `PANEL_SECRET_KEY` | 空 | Session 签名密钥；未设置时每次启动随机生成 |
| `PANEL_HEALTH_REQUIRES_XRAY` | `1` | 是否要求 `/healthz` 同时验证 Xray 可用 |
| `DEFAULT_UPSTREAM_HOST` | `127.0.0.1` | 默认上游主机；启动时会规范化写回所有端口记录 |
| `DEFAULT_UPSTREAM_PORT` | `443` | 默认上游端口 |
| `SEED_LISTEN_PORT` | `31098` | 数据库为空时自动创建的初始端口 |
| `MAINTENANCE_INTERVAL` | `10` | 后台维护循环间隔，单位秒 |
| `PROBE_ENABLED` | `0` | 是否启用 TCP 探针 |
| `PROBE_INTERVAL` | `60` | 探针间隔，单位秒 |
| `PROBE_TIMEOUT` | `3` | 单次探针超时，单位秒 |
| `PROBE_TEST_LISTEN_PORT` | 空 | 监控页固定展示的测试端口 |
| `XRAY_API_SERVER` | `127.0.0.1:10085` | Xray API 地址 |
| `XRAY_LOCAL_BIN` | 空 | 设置后进入“本地 Xray 模式” |
| `XRAY_CONTAINER_NAME` | `xray-reality-local` | 面板管理的 Xray 容器名 |
| `XRAY_DOCKER_BIN` | `docker` | Docker 可执行文件名 |
| `XRAY_STATS_QUERY_TIMEOUT` | `5` | Xray `statsquery` 超时，单位秒 |
| `XRAY_PROBE_HOST` | `127.0.0.1` | 探针连接使用的目标主机 |
| `SUBSCRIPTION_NAME_PREFIX` | `reality` | 生成订阅名称和分享备注时使用的前缀 |
| `DEFAULT_NODE_SSH_TARGET` | 空 | 默认数据面节点 SSH 目标，例如 `root@node-a` |
| `DEFAULT_NODE_SSH_OPTIONS` | 空 | 默认节点 SSH 附加参数 |
| `DEFAULT_NODE_CONFIG_PATH` | 空 | 远端默认节点 `config.json` 路径 |
| `DEFAULT_NODE_PANEL_PORTS_PATH` | 空 | 远端默认节点 `panel-ports.json` 路径 |
| `DEFAULT_NODE_ACCESS_LOG_PATH` | 空 | 远端默认节点 `access.log` 路径 |
| `DEFAULT_NODE_RESTART_COMMAND` | 空 | 默认节点远程重启命令 |
| `AI_NODE_HOST` | 空 | AI 节点主机 |
| `AI_NODE_PORT` | 空 | AI 节点端口 |
| `AI_NODE_SSH_TARGET` | 空 | AI 节点 SSH 目标 |
| `AI_NODE_SSH_OPTIONS` | 空 | AI 节点 SSH 附加参数 |
| `AI_NODE_RESTART_COMMAND` | 空 | AI 节点远程重启命令 |

内部路径和备份相关变量：

- `DATA_DIR`
- `DB_PATH`
- `DB_BACKUP_DIR`
- `DB_BACKUP_KEEP_DAYS`
- `DB_BACKUP_PREFIX`
- `DB_BACKUP_CRON_SCHEDULE`
- `XRAY_ENV_FILE_PATH`
- `XRAY_CONFIG_PATH`
- `XRAY_PANEL_PORTS_PATH`
- `XRAY_ACCESS_LOG_PATH`
- `XRAY_CLIENT_CONFIG_PATH`

### Xray / AI 变量

完整示例见：

- [app/xray/.env.example](app/xray/.env.example)
- [app/xray/README.md](app/xray/README.md)

最关键的几组变量如下。

必填的 REALITY 基础参数：

- `XRAY_PUBLIC_HOST`
- `XRAY_CLIENT_UUID`
- `XRAY_REALITY_PRIVATE_KEY`
- `XRAY_REALITY_PUBLIC_KEY`
- `XRAY_REALITY_SHORT_ID`
- `XRAY_SERVER_NAME`
- `XRAY_DEST`

常用监听和路由参数：

- `XRAY_LISTEN_PORT`
- `XRAY_PUBLIC_PORT`
- `AI_UPSTREAM_HOST`
- `AI_UPSTREAM_PORT`
- `AI_UPSTREAM_FALLBACK_URL`
- `AI_UPSTREAM_FALLBACKS`
- `AI_UPSTREAMS`
- `AI_UPSTREAM_PROBE_TIMEOUT_SECONDS`
- `PANEL_ROUTE_LISTEN_PORT`

分类与模型参数：

- `CODEX_CLASSIFIER_ENABLED`
- `CODEX_TIMEOUT_SECONDS`
- `CODEX_MODEL`
- `CODEX_CLI_JS`
- `CODEX_BIN`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `OPENAI_BASE_URL`
- `OPENAI_ALLOW_NO_KEY`

Google Search MCP / OpenRouter 参数：

- `GOOGLE_SEARCH_NUM_RESULTS`
- `GOOGLE_SEARCH_QUERY_TEMPLATE`
- `GOOGLE_SEARCH_USER_AGENT`
- `OPENROUTER_API_KEY`
- `OPENROUTER_MODEL`
- `OPENROUTER_BASE_URL`
- `OPENROUTER_TIMEOUT_SECONDS`

## 目录结构

```text
.
├── .env.example                  # 面板公开地址和管理员认证模板
├── Dockerfile                    # 面板镜像；内置 Python、Docker CLI 和 Xray 二进制
├── docker-compose.yml            # 完整 compose 入口
├── PANEL_MIGRATION.md            # 迁移说明
├── app/
│   ├── panel.py                  # 程序入口
│   ├── web.py                    # Flask 路由
│   ├── state.py                  # 数据库、流量同步、Xray 配置刷新
│   ├── subscriptions.py          # 订阅与分享链接生成
│   ├── templates/                # 页面模板
│   ├── static/                   # 前端资源
│   └── xray/
│       ├── .env.example          # Xray / AI 配置模板
│       ├── README.md             # Xray 子系统详细文档
│       ├── render_config.py      # 渲染 Xray 配置和客户端配置
│       ├── ai_domain_manager.py  # AI 域名分析与动态路由
│       ├── google_search_mcp.py  # 可选 MCP server
│       ├── ai-proxy-outbound.json
│       ├── assets/
│       ├── logs/
│       ├── reports/
│       └── runtime/
├── scripts/
│   ├── backup_db.py
│   ├── render_config.py
│   ├── ai_domain_manager.py
│   └── google_search_mcp.py
├── data/                         # SQLite 数据库
├── backups/                      # 数据库备份
└── k8s/                          # Kubernetes 清单
```

说明：

- `logs/stream-access.log`、旧的 `nginx-*` 备份文件名只属于历史遗留
- 当前实现真正使用的是 `app/xray/logs/access.log`

## 常见排障

- `GET /healthz` 返回 `500`
  - 先看 `docker compose logs -f xray-routing-panel`
  - 再确认 `xray-reality` 是否真的在运行
  - 再确认 `XRAY_API_SERVER` 是否可访问
  - 如果你当前只启动了面板且不需要检查 Xray，把 `PANEL_HEALTH_REQUIRES_XRAY=0`
- 页面里订阅链接显示不可用
  - 先执行 `python -m app.xray.render_config`
  - 再确认 `app/xray/runtime/client-test.json` 存在且能被容器看到
- 创建端口后没有真正开始承载流量
  - 确认 `xray-reality` 容器已启动，或者你已经正确配置 `XRAY_LOCAL_BIN` + 本地 Xray
  - 只启动面板本身并不会自动提供 Xray 数据面
- AI 域名没有新增分类结果
  - 看 `docker compose --profile xray logs -f xray-ai-domain-manager`
  - 手动执行一次 `--once`
  - 确认 `app/xray/logs/access.log` 里有新访问
  - 确认宿主机 `codex login status` 可用，或者 `OPENAI_API_KEY` / `OPENAI_BASE_URL` 已正确配置
- 面板重启后管理员登录失效
  - 显式设置 `PANEL_SECRET_KEY`
- 第一次启动后没有出现默认端口
  - 检查 `SEED_LISTEN_PORT` 是否被清空
  - 如果 `data/panel.db` 里已经有旧数据，就不会再次注入初始化端口

## 常用命令

```bash
python -m app.xray.render_config
docker compose --profile xray up -d --build
docker compose --profile xray logs -f xray-reality
docker compose --profile xray logs -f xray-ai-domain-manager
docker compose --profile xray run --rm xray-ai-domain-manager python -m app.xray.ai_domain_manager --once
docker compose down
```

手动备份数据库：

```bash
python3 ./scripts/backup_db.py --db-path ./data/panel.db --backup-dir ./backups
```

查看 AI 域名聚合结果：

```bash
python3 - <<'PY'
import sqlite3
conn = sqlite3.connect('./data/panel.db')
for row in conn.execute('select domain, classification, total_hits from ai_domains order by domain'):
    print(row)
PY
```

## 相关文档

- Xray 子系统说明：[app/xray/README.md](app/xray/README.md)
- Kubernetes 部署说明：[k8s/README.md](k8s/README.md)
- 迁移说明：[PANEL_MIGRATION.md](PANEL_MIGRATION.md)
