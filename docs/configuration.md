# 配置说明

## 配置入口

- 根目录 `.env`
  - 面板地址、管理员认证、AI 路由开关、远端数据面接入参数
- `app/xray/.env`
  - REALITY 基础参数、AI 上游、分类器、MCP 和 Xray 渲染参数

仓库根目录的 `.env.example` 只覆盖高频项；`docker-compose.yml` 里还会注入一批固定运行时默认值。

## 根 `.env` 常用变量

| 变量 | 说明 |
| --- | --- |
| `PANEL_PUBLIC_URL` | 面板对外地址；影响订阅链接和安全 Cookie |
| `PANEL_USERNAME` / `PANEL_PASSWORD` | 管理员认证；任一设置后首页、探针页和 `/api/*` 都要求登录 |
| `PANEL_SECRET_KEY` | Session 签名密钥；不设置则每次启动随机生成 |
| `AI_ROUTING_ENABLED` | 是否展示 AI 路由状态和相关统计 |
| `DATAPLANE_SSH_TARGET` | 远端数据面 SSH 目标，例如 `root@node-a` |
| `DATAPLANE_SSH_OPTIONS` | SSH 额外参数，按 shell words 解析 |
| `DATAPLANE_API_SERVER` | 数据面 Xray API 地址，默认 `127.0.0.1:10085` |
| `DATAPLANE_CONFIG_PATH` | 远端或本地数据面使用的 `config.json` 路径 |
| `DATAPLANE_DYNAMIC_ROUTING_PATH` | 远端 `dynamic-routing.json` 路径 |
| `DATAPLANE_AI_REPORT_PATH` | 远端 `reports/hourly-domains/latest.json` 路径 |
| `DATAPLANE_PANEL_DB_PATH` | 远端 `panel.db` 路径，用于回传 AI 域名聚合快照 |
| `DATAPLANE_PANEL_PORTS_PATH` | 远端 `panel-ports.json` 路径 |
| `DATAPLANE_ACCESS_LOG_PATH` | 远端 `access.log` 路径 |
| `DATAPLANE_RESTART_COMMAND` | 远端数据面重启命令 |
| `DATAPLANE_PROBE_HOST` | TCP 探针连接目标；远端模式下应指向远端入口 IP 或域名 |

常见但通常不需要手动覆盖的运行时变量：

- `PANEL_PORT`
- `DEFAULT_UPSTREAM_HOST`
- `DEFAULT_UPSTREAM_PORT`
- `SEED_LISTEN_PORT`
- `PROBE_ENABLED`
- `PROBE_INTERVAL`
- `PROBE_TEST_LISTEN_PORT`
- `PANEL_HEALTH_REQUIRES_XRAY`

## `app/xray/.env` 必填 REALITY 参数

以下参数是渲染 `config.json` 的基础输入：

- `XRAY_PUBLIC_HOST`
- `XRAY_CLIENT_UUID`
- `XRAY_REALITY_PRIVATE_KEY`
- `XRAY_REALITY_PUBLIC_KEY`
- `XRAY_REALITY_SHORT_ID`
- `XRAY_SERVER_NAME`
- `XRAY_DEST`

常用补充项：

- `XRAY_LISTEN_PORT`
- `XRAY_PUBLIC_PORT`
- `XRAY_API_SERVER`
- `XRAY_NODE_TAG`
- `XRAY_LOGLEVEL`

## AI 上游和分类器变量

### AI 上游

- `AI_UPSTREAM_HOST`
- `AI_UPSTREAM_PORT`
- `AI_UPSTREAM_FALLBACK_URL`
- `AI_UPSTREAM_FALLBACKS`
- `AI_UPSTREAMS`
- `AI_UPSTREAM_PROBE_TIMEOUT_SECONDS`
- `PANEL_ROUTE_LISTEN_PORT`

说明：

- `AI_UPSTREAM_HOST` / `AI_UPSTREAM_PORT` 是主上游
- `AI_UPSTREAM_FALLBACKS` 在主上游后追加多个备用上游
- `AI_UPSTREAMS` 直接覆盖完整优先级列表
- `AI_UPSTREAM_FALLBACK_URL` 适合备用上游使用不同 UUID / `pbk` / `sid` / `sni`

### 域名分类器

- `CODEX_CLASSIFIER_ENABLED`
- `CODEX_TIMEOUT_SECONDS`
- `CODEX_MODEL`
- `CODEX_CLI_JS`
- `CODEX_BIN`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `OPENAI_BASE_URL`
- `OPENAI_ALLOW_NO_KEY`

如果本机 `codex` 不可用，AI 管理器会回退到 OpenAI 兼容接口。

## 模式相关注意事项

### 远端模式

- `DATAPLANE_SSH_TARGET` 生效后，数据面模式优先变成 `ssh`
- 控制面会先在本地渲染，再通过 SSH 上传产物
- 如果要在首页展示远端 AI 报表，需要补齐 `DATAPLANE_AI_REPORT_PATH` 和 `DATAPLANE_PANEL_DB_PATH`

### 本地二进制模式

- 设置 `DATAPLANE_LOCAL_BIN` 后，模式变成 `local`
- 面板能做配置校验，但不会自动重启该进程

### Docker 模式

- 默认通过 `DATAPLANE_CONTAINER_NAME=xray-reality-local` 管理本地容器
- 如不使用本地容器，可显式清空并改用 `local` 或 `ssh`

完整变量模板见：

- [../.env.example](../.env.example)
- [../app/xray/.env.example](../app/xray/.env.example)
