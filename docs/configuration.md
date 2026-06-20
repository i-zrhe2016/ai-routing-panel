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

## DNS 故障切换变量

| 变量 | 说明 |
| --- | --- |
| `DNS_FAILOVER_ENABLED` | 是否启用 Cloudflare DNS 故障切换 |
| `DNS_FAILOVER_INTERVAL` | 后台检测周期，默认 `15` 秒 |
| `DNS_FAILOVER_TIMEOUT` | 单次 TCP 探测超时 |
| `DNS_FAILOVER_FAILURE_THRESHOLD` | 连续失败多少次切到备用 |
| `DNS_FAILOVER_RECOVERY_THRESHOLD` | 连续成功多少次回切主数据面 |
| `DNS_FAILOVER_PROBE_HOST` / `DNS_FAILOVER_PROBE_PORT` | 只用于自动切换判定的数据面公网 TCP 探测目标 |
| `CF_API_TOKEN` | Cloudflare API Token，至少需要目标 Zone 的 DNS 编辑权限 |
| `CF_ZONE_ID` | Cloudflare Zone ID |
| `CF_DNS_RECORD_ID` | 要切换的单条 DNS Record ID |
| `CF_DNS_RECORD_TYPE` | 当前支持 `A` / `AAAA` / `CNAME` |
| `CF_DNS_RECORD_NAME` | 记录名，例如 `edge.example.com` |
| `CF_DNS_RECORD_PROXIED` | 是否保持 Cloudflare 代理 |
| `CF_DNS_RECORD_TTL` | 记录 TTL；非代理记录建议 `60` 以尽快生效 |
| `DNS_FAILOVER_PRIMARY_CONTENT` | 主数据面入口 IP 或 CNAME；留空时自动获取数据面公网 IP |
| `CONTROL_PLANE_BACKUP_XRAY_ENABLED` | 是否启用“控制面本机公网 IP + 备用 Xray”自动备用模式 |
| `DNS_FAILOVER_BACKUP_CONTENT` | 控制面备用节点 IP 或 CNAME；留空时自动获取控制面本机公网 IP |
| `DNS_FAILOVER_BACKUP_LABEL` | 首页展示用备用节点名称 |

说明：

- 当前只支持通过 `CF_DNS_RECORD_ID` 更新单条记录
- 自动切换只看 `DNS_FAILOVER_PROBE_HOST:DNS_FAILOVER_PROBE_PORT`
- AI 节点状态只展示，不参与任何 DNS 切换决策
- 如果 `DNS_FAILOVER_PRIMARY_CONTENT` 留空，控制面会自动获取当前数据面的公网 IP
- 如果 `CONTROL_PLANE_BACKUP_XRAY_ENABLED=1` 且 `DNS_FAILOVER_BACKUP_CONTENT` 留空，控制面会自动获取本机公网 IP，适合作为控制面备用 Xray 的 DNS 指向
- 如果 `CONTROL_PLANE_BACKUP_XRAY_ENABLED=0`，则必须显式填写 `DNS_FAILOVER_BACKUP_CONTENT`
- 对 REALITY 这类直连流量，想让 IP 更快生效，优先把 `CF_DNS_RECORD_TTL` 设为 `60`

## 数据库备份上传变量

| 变量 | 说明 |
| --- | --- |
| `DB_BACKUP_UPLOADER_ENABLED` | 是否在每日本地备份成功后自动上传 |
| `DB_BACKUP_UPLOADER_PASSWORD` | 备份加密密码；必须覆盖上游占位值 |
| `DB_BACKUP_UPLOADER_SCOPE` | npm 包 scope，例如 `@example` |
| `DB_BACKUP_UPLOADER_DRY_RUN` | 设为 `1` 时只做加密切片和记录写入，不真实 publish |
| `DB_BACKUP_UPLOADER_NPMRC_PATH` | npm 认证配置文件路径，默认 `/db-backup-uploader-data/.npmrc` |
| `DB_BACKUP_UPLOADER_ARTIFACT_NAME` | 逻辑 artifact 名；不设时默认为 `<DB_BACKUP_PREFIX>-db-backup` |
| `DB_BACKUP_UPLOADER_PACKAGE_VERSION` | 强制覆盖自动生成的包版本；留空时按备份时间戳生成 |
| `DB_BACKUP_UPLOADER_SHARD_SIZE_BYTES` | 单分片大小，默认 `5242880` |
| `DB_BACKUP_UPLOADER_PUBLISH_CONCURRENCY` | 并发发布数，默认 `2` |
| `DB_BACKUP_UPLOADER_NPM_PUBLISH_TIMEOUT_MS` | 单个 `npm publish` 超时时间 |

目录约定：

- 本地 `.db` 备份：`./backups`
- 上传工作目录：`./data/db-backup-uploader`
- 默认认证文件：`./data/db-backup-uploader/.npmrc`

当前上传器默认采用 latest-only 模式：

- 上传成功后会尝试删除上一份备份对应的 npm 包版本
- `upload-records.json` 只保留当前 `latest`

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
