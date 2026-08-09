# 配置说明

- 配置入口
  - 根目录 `.env`：面板地址、管理员认证、AI 路由开关、远端数据面接入参数、AI 节点纳管参数
  - `app/xray/.env`：REALITY 基础参数、AI 上游、分类器、MCP 和 Xray 渲染参数

仓库根目录的 `.env.example` 只覆盖高频项；`docker-compose.yml` 里还会注入一批固定运行时默认值。

## 根 `.env` 常用变量

| 变量 | 说明 |
| --- | --- |
| `PANEL_PUBLIC_URL` | 面板对外地址；影响订阅链接和安全 Cookie |
| `PANEL_USERNAME` / `PANEL_PASSWORD` | 管理员认证；任一设置后首页、探针页和 `/api/*` 都要求登录 |
| `PANEL_SECRET_KEY` | Session 签名密钥；不设置则每次启动随机生成 |
| `METRICS_TOKEN` | Prometheus `/metrics` 抓取令牌；不设置则 `/metrics` 返回 404，设置后需 `Authorization: Bearer <token>` |
| `METRICS_DP_TTL` | `/metrics` 缓存数据面存活检测的秒数，默认 `30`（抓取路径上唯一的 SSH 调用） |
| `GRAFANA_PUBLIC_URL` | 生产统一使用 `https://xray.zrhe2016.cc/grafana/`，由 Cloudflare Access 保护；管理后台「监控」标签使用该同源地址 |
| `GRAFANA_OBSERVABILITY_UID` | 「监控」标签内嵌所用 Grafana dashboard 的 UID，默认 `xray-observability` |
| `AI_ROUTING_ENABLED` | 是否展示 AI 路由状态和相关统计 |
| `DATAPLANE_SSH_TARGET` | 远端数据面 SSH 目标，例如 `root@node-a` |
| `DATAPLANE_SSH_OPTIONS` | SSH 额外参数，按 shell words 解析；远端生产环境建议包含 `-o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=1` |
| `DATAPLANE_REMOTE_COMMAND_TIMEOUT` | 单次远程 SSH/Docker 命令的控制面超时，默认 `8` 秒；避免数据面失联拖住控制面任务 |
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

## AI 节点纳管变量

| 变量 | 说明 |
| --- | --- |
| `AI_NODE_SSH_TARGET` | AI 节点 SSH 目标，例如 `root@nat.qq.pw` |
| `AI_NODE_SSH_BIN` | SSH 可执行文件或密钥包装器，例如 `/app/scripts/ai-node-ssh` |
| `AI_NODE_SSH_OPTIONS` | SSH 额外参数，按 shell words 解析；必须启用严格主机校验和仅公钥认证 |
| `AI_NODE_SSH_KEY_FILE` | 密钥包装器读取的容器内只读私钥路径；不得把私钥写入环境变量或镜像 |
| `AI_NODE_CONTAINER_NAME` | AI 节点上 Xray 容器名；生产当前为 `xray` |
| `AI_NODE_RESTART_COMMAND` | 自定义重启命令（优先于容器名） |
| `AI_NODE_CONFIG_PATH` | AI 节点真实宿主配置路径；显式留空会禁用配置上传 |
| `AI_NODE_API_SERVER` | SSH 模式下远端 Socket 存活检查地址；生产当前为 `127.0.0.1:27166` |
| `AI_NODE_PROBE_HOST` | AI 节点可达性探测目标 IP 或域名 |

说明：

- AI 节点使用独立 REALITY 凭据，不能复用或由普通数据面的 `XRAY_*` 参数覆盖
- `AI_UPSTREAM_HOST` / `AI_UPSTREAM_PORT`（在 `app/xray/.env` 中）定义主数据面 VLESS outbound 的目标，生产为 `nat.qq.pw:27166`
- 当前生产保持 `AI_NODE_CONFIG_PATH=`，关闭配置上传但保留 SSH 状态检查和容器重启
- 详见 [AI 节点部署与 SSH 纳管](ai-node-deployment.md)和 [AI 节点独立凭据](ai-node-credentials.md)

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
| `DNS_FAILOVER_PRIMARY_CONTENT` | 主数据面入口 IP 或 CNAME；远端数据面模式必须显式填写，本地模式可留空自动获取 |
| `CONTROL_PLANE_BACKUP_XRAY_ENABLED` | 是否启用"控制面本机公网 IP + 备用 Xray"自动备用模式（relay / 直出双模式） |
| `CONTROL_PLANE_BACKUP_UPSTREAM_URL` | relay 模式的完整 `vless://` 上游 URL；必须受保护并与 AI 节点独立 inbound 凭据完整匹配，不得从普通数据面 `XRAY_*` 盲目派生 |
| `DNS_FAILOVER_BACKUP_CONTENT` | 控制面备用节点 IP 或 CNAME；留空时自动获取控制面本机公网 IP |
| `DNS_FAILOVER_BACKUP_LABEL` | 首页展示用备用节点名称 |
| `DNS_FAILOVER_PEAK_ENABLED` | 是否启用“高峰窗口优先专用节点” |
| `DNS_FAILOVER_PEAK_START` / `DNS_FAILOVER_PEAK_END` | 高峰窗口起止时间，格式 `HH:MM` |
| `DNS_FAILOVER_PEAK_TIMEZONE` | 高峰窗口时区；支持 `Asia/Shanghai` 或 `+08:00` |

说明：

- 当前只支持通过 `CF_DNS_RECORD_ID` 更新单条记录
- 自动切换只看 `DNS_FAILOVER_PROBE_HOST:DNS_FAILOVER_PROBE_PORT`
- DNS 故障切换运行在独立 worker 中，不依赖数据面日志、Xray API、流量统计或配置同步
- 数据面远程命令受 `DATAPLANE_REMOTE_COMMAND_TIMEOUT` 限制；SSH 连接参数仍建议通过 `DATAPLANE_SSH_OPTIONS` 配置连接超时和 keepalive
- AI 节点故障不触发 DNS 切换，由 `ai_domain_manager` 自动回退；数据面故障时 DNS 切到控制面备用，AI 节点健康度决定备用是 relay 还是直出模式
- 若启用高峰窗口，窗口内会把备用/专用节点视为首选目标；窗口外恢复主节点优先
- 如果是本地数据面且 `DNS_FAILOVER_PRIMARY_CONTENT` 留空，控制面会自动获取当前数据面的公网 IP；远端数据面必须显式填写，避免数据面失联时 DNS worker 依赖数据面 SSH
- 如果 `CONTROL_PLANE_BACKUP_XRAY_ENABLED=1` 且 `DNS_FAILOVER_BACKUP_CONTENT` 留空，控制面会自动获取本机公网 IP，适合作为控制面备用 Xray 的 DNS 指向
- 如果 `CONTROL_PLANE_BACKUP_XRAY_ENABLED=0`，则必须显式填写 `DNS_FAILOVER_BACKUP_CONTENT`
- 对 REALITY 这类直连流量，想让 IP 更快生效，优先把 `CF_DNS_RECORD_TTL` 设为 `60`
- 完整 DNS 故障切换机制详见 [dns-failover.md](dns-failover.md)

## 灾备归档与 npm 上传变量

| 变量 | 说明 |
| --- | --- |
| `DB_BACKUP_UPLOADER_ENABLED` | 是否在每日本地备份成功后自动上传 |
| `DB_BACKUP_BUNDLE_ENABLED` | 是否生成包含数据库和配置文件的灾备归档；默认 `1` |
| `DB_BACKUP_EXTRA_PATHS` | 逗号/换行分隔的额外文件、目录或 glob；Compose 默认收集 `/app/xray/.env,/app/xray/runtime` |
| `DB_BACKUP_BUNDLE_DIR` | 灾备归档本地目录，默认跟随 `DB_BACKUP_DIR` |
| `DB_BACKUP_BUNDLE_KEEP_DAYS` | 灾备归档本地保留天数，默认跟随 `DB_BACKUP_KEEP_DAYS` |
| `DB_BACKUP_BUNDLE_PREFIX` | 灾备归档名前缀，默认跟随 `DB_BACKUP_PREFIX` |
| `DB_BACKUP_SSH_COLLECTION_ENABLED` | 是否在归档前通过只读 SSH 采集两个数据面；Compose 默认 `1`，脚本默认 `0` |
| `DB_BACKUP_SSH_COLLECTION_REQUIRED` | `1` 时两个节点都必须连通且第一个主配置路径成功；`0` 时远端失败只记入 manifest |
| `DB_BACKUP_SSH_KEY_PATH` | SSH 私钥路径，默认 `/run/secrets/fleet_ssh_key`；不得把内容放入环境变量 |
| `DB_BACKUP_SSH_OPTIONS` | 仅允许 `-4`/`-6`、日志级别和连接超时/keepalive 等安全选项；身份、known_hosts、代理和远端命令覆盖会被拒绝 |
| `DB_BACKUP_SSH_TIMEOUT_SECONDS` / `DB_BACKUP_SSH_MAX_FILE_BYTES` | 远端连接超时（默认 20 秒）和单文件上限（默认 5 MiB） |
| `DB_BACKUP_DATAPLANE_SSH_TARGET` / `DB_BACKUP_DATAPLANE_SSH_PORT` | 普通数据面 SSH 目标和端口；生产为 `root@64.186.224.96:22` |
| `DB_BACKUP_DATAPLANE_KNOWN_HOSTS` | 普通数据面专用 known_hosts 文件，默认 `/root/.ssh/known_hosts` |
| `DB_BACKUP_DATAPLANE_REMOTE_PATHS` | 普通数据面主机路径；实测主配置为 `/root/xray-routing-panel/app/xray/runtime/config.json` |
| `DB_BACKUP_AI_NODE_SSH_TARGET` / `DB_BACKUP_AI_NODE_SSH_PORT` | AI 数据面 SSH 目标和端口；生产为 `root@nat.qq.pw:27160` |
| `DB_BACKUP_AI_NODE_KNOWN_HOSTS` | AI 节点专用 known_hosts，默认 `/root/.ssh/known_hosts_ai` |
| `DB_BACKUP_AI_NODE_REMOTE_PATHS` | AI 节点路径，默认 `/etc/xray/config.json,/etc/xray/.env` |
| `DB_BACKUP_UPLOADER_PASSWORD` | 备份加密密码；必须覆盖上游占位值 |
| `DB_BACKUP_UPLOADER_SCOPE` | npm 包 scope，例如 `@example` |
| `DB_BACKUP_UPLOADER_DRY_RUN` | 设为 `1` 时只做加密切片和记录写入，不真实 publish |
| `DB_BACKUP_UPLOADER_NPMRC_PATH` | npm 认证配置文件路径，默认 `/db-backup-uploader-data/.npmrc` |
| `DB_BACKUP_UPLOADER_ARTIFACT_NAME` | 逻辑 artifact 名；不设时灾备归档默认为 `<DB_BACKUP_PREFIX>-disaster-backup`，关闭归档时为 `<DB_BACKUP_PREFIX>-db-backup` |
| `DB_BACKUP_UPLOADER_PACKAGE_VERSION` | 强制覆盖自动生成的包版本；留空时按备份时间戳生成 |
| `DB_BACKUP_UPLOADER_SHARD_SIZE_BYTES` | 单分片大小，默认 `5242880` |
| `DB_BACKUP_UPLOADER_PUBLISH_CONCURRENCY` | 并发发布数，默认 `2` |
| `DB_BACKUP_UPLOADER_NPM_PUBLISH_TIMEOUT_MS` | 单个 `npm publish` 超时时间 |
| `DB_BACKUP_UPLOADER_PRUNE_REMOTE` | 是否删除上一轮 npm 版本；灾备默认 `0`，保留不可变历史 |
| `DB_BACKUP_UPLOADER_RECORD_HISTORY_LIMIT` | 本地记录保留的历史归档数量，默认 `20`；只影响索引，不影响 npm 版本 |

目录约定：

- 本地 `.db` 备份：`./backups`
- 上传工作目录：`./data/db-backup-uploader`
- 默认认证文件：`./data/db-backup-uploader/.npmrc`

自动灾备任务使用历史保留模式：npm 版本不会被删除，`upload-records.json` 保存最近一次和有限历史索引。npm 只用于低频灾难阶段下载，不作为快速恢复或故障切换依赖。

SSH 采集的详细安全边界、`remote-node-collection.json` 字段和只读验证命令见[远端节点配置采集](remote-node-backup.md)。

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
- 主 AI 上游同样可能使用独立凭据；动态 VLESS outbound 必须与 AI inbound 完整匹配，不能从普通数据面 `XRAY_*` 盲目派生
- 如果全部 AI 上游 TCP 探测都失败，AI 动态路由会撤销，流量回退到主链路
- `AI_NODE_SSH_TARGET` 只启用 SSH 纳管；它不证明凭据匹配，也不应自动派生独立 AI 节点的 relay URL

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

### AI 节点模式

- `AI_NODE_SSH_TARGET` 生效后，AI 节点模式为 `ssh`（远端 SSH 纳管）
- `AI_NODE_CONFIG_PATH` 非空时控制面才具备上传 `config-ai-node.json` 的能力；生产当前显式留空以禁止上传
- `AI_NODE_API_SERVER` 用于远端 Socket 状态检查；当前最小 AI 配置不启用 Stats API
- AI 节点使用独立 REALITY 凭据，字段契约见 [AI 节点独立凭据](ai-node-credentials.md)
- 详见 [AI 节点部署与 SSH 纳管](ai-node-deployment.md)

完整变量模板见：

- [../.env.example](../.env.example)
- [../app/xray/.env.example](../app/xray/.env.example)
