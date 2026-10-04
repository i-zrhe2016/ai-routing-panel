# 运维与排障

> Type: Runbook
> Status: Active
> Scope: 面板和受管节点的运行检查、指标观测与日常排障

## 健康检查

- 接口：`GET /healthz`
- 返回体：`{"ok": <bool>, "data_plane_running": <bool>, "ai_node_running": <bool>}`
- 默认行为：要求数据面可用时才返回健康

如果你只启动面板而不启动数据面：

- 设置 `PANEL_HEALTH_REQUIRES_XRAY=0`

`ai_node_running` 反映 AI 节点远端 Socket 可达性，不代表 REALITY 凭据匹配或实际 ChatGPT 请求成功。

## AI 路由状态

AI 路由状态至少应同时查看 `ai_candidates`、`manual_mode`、`route_status` 和 `ai_target.selected_index`，核对候选探测结果、人工策略和实际写入的上游。详见 [AI 路由](ai-routing.md)。

## 流量与连接统计

后台流量拓扑的使用与数据边界见[开发流程](development.md)。

当前统计链路拆成两部分：

- 连接数来自 `app/xray/logs/access.log`
  - 只统计 `panel-<listen_port>` inbound tag
- 字节流量来自 Xray API `statsquery`
  - 查询模式为 `inbound>>>panel-`
  - 每次拉取后会执行 `-reset`

因此：

- `total_connections` 依赖访问日志增量同步
- `total_bytes_sent` / `total_bytes_received` 依赖 Xray API 周期采样
- 流量上限判断使用上行加下行累计值

## 自动维护规则

- 非商业端口到期后自动删除；关联客户或商业订阅的端口到期后自动停用，保留记录和订阅凭据供续费
- 达到流量上限的端口会自动停用
- “重置流量并启用”会清零累计流量与当日流量，但不会清零连接数

端口变更只有在配置校验、所需节点重载和数据库提交成功后才完成。重载失败（包括返回失败状态）
或数据库提交失败时，会回滚数据库并恢复原配置文件；已尝试重载的数据面会再次加载原配置。
如果数据面回滚同步或重载也失败，记录 `node.data_plane.rollback_failed` 事件，需要检查节点实际运行状态。

## 灾备归档与上传

- `xray-routing-panel-db-backup` 默认每天 `03:00 UTC` 备份一次 `panel.db`，并生成一个包含配置文件的灾备归档
- 本地 `.db` 和 `tar.gz` 备份文件均落在 `./backups`
- 额外文件由 `DB_BACKUP_EXTRA_PATHS` 指定；Compose 默认包含 `app/xray/.env`、运行时/报告、`data/uploads` 和部署脚本
- 在 `.env` 中启用远端采集后，Compose 通过隔离 Tailscale broker 只读采集普通数据面和已配置的远端 AI 节点；未配置远端 AI 目标时，本机 AI 备用的 `.env` 与 `config-ai-node.json` 随 `config/` 归档
- 每个归档都包含 `node-recovery-manifest.json`，任务还会生成 `node-recovery-status.json`，明确标记 `recoveryReady`
- Compose 默认关闭远端采集和完整性门禁；示例 `.env` 将两项门禁设为必需。门禁开启后，缺少普通数据面目标或已配置节点未提供必需恢复文件会阻止归档上传
- 当 `DB_BACKUP_R2_ENABLED=1` 时，归档成功后会继续调用 `R2 灾备上传`
- R2 对象 key 默认包含日期、归档名称和 SHA-256 前缀；对象保留由 Cloudflare R2 生命周期策略控制，不承担快速恢复

上传链路依赖：

- `DB_BACKUP_R2_ENDPOINT`、`DB_BACKUP_R2_BUCKET`
- `DB_BACKUP_R2_ACCESS_KEY_ID`、`DB_BACKUP_R2_SECRET_ACCESS_KEY`
- 独立保存的 `DB_BACKUP_ENCRYPTION_PASSWORD`

排查建议：

- 查看日志：`docker compose logs -f xray-routing-panel-db-backup`
- 确认最新本地备份已生成到 `./backups`
- 确认对应的 `*-disaster-*.tar.gz` 已生成，并检查其中 `backup-manifest.json` 的 `skippedExtraPaths`
- 检查 `node-recovery-status.json`：普通数据面和 AI 数据面都应为 `ready=true`；必需 `.env` 缺失会列入 `missingRequiredArtifacts`
- 确认 `./backups/r2-upload-record.json` 是否已更新

节点替换的演练和应急命令见[节点备份完整性与快速恢复](node-recovery.md)。应急时先执行 `python3 scripts/node_recovery.py validate --bundle <bundle> --require-ready`，再执行 `prepare`，不要直接解压覆盖运行目录。

SSH 采集的认证、known_hosts、实测路径和只读排障命令见[远端节点配置采集](remote-node-backup.md)。采集器不会在远端写入、重启或执行配置同步。

## 协议探测

AI 候选选择、启用租户端口的周期探测、DNS 故障切换和按需诊断均通过 `PROBE_SSH_TARGET` 的独立执行主机运行 Xray VLESS + REALITY 客户端。健康要求经认证隧道请求 `https://www.gstatic.com/generate_204` 返回 HTTP 204；TCP 开放或 TLS/SNI 握手不能建立业务健康。该固定目标无需 HTTPS 外的其他健康请求。

配置键的默认值见[配置参考](configuration.md)。在独立主机安装 Python 3.10+、curl 和与节点一致的 Xray 客户端版本；将仓库 `scripts/xray_protocol_probe.py` 与 Xray 二进制放在 `/opt/xray-probe/releases/<version>/` 的 root 只读文件中。先校验二进制 SHA-256 和版本，再将 `PROBE_REMOTE_SCRIPT`、`PROBE_XRAY_BIN` 固定到 release 路径；使用 `current` 符号链接时应原子切换。先从控制面容器验证严格 SSH 主机密钥与认证，随后测试正确凭据成功、错误 UUID 拒绝、执行主机故障保留状态。回滚只需恢复此前脚本/二进制路径并重启 panel 和 manager；不得回退到普通/AI 节点执行。

客户端 UUID、REALITY 公钥/Short ID 等仅经 SSH stdin 传输，写入每次调用独立的 0700 临时目录和 0600 配置；不进入 argv、输出或日志。每次启动独立 loopback SOCKS 监听，curl 强制使用 SOCKS5h 并禁用 NO_PROXY/用户 curl 配置，进程超时后终止并清理临时文件。请求超时接受 0.1–30 秒；SSH 总时限为请求时限加启动时限（最多 3 秒）和 4 秒清理余量。

缺失凭据、客户端依赖、无效返回、SSH/执行主机不可用均标记 `management_error`，不计为目标故障。AI 保留此前选择和动态片段，DNS 保留失败/成功计数及目标，租户周期探测保留此前业务健康。结果携带 `error_code`、`stage`、`checked_at`、`probe_origin` 与请求状态供控制面诊断使用；异常报告回调不改变健康决策。

普通租户探测使用 `client-test.json` 的实际账户凭据；统一 443 入口使用 `panelSubscription.users[listen_port]`，缺失/禁用账户不会借用其他 UUID。AI 候选使用实际渲染的 `ai_proxy` outbound，分享链接保留独立凭据。DNS 使用生成的主诊断客户端，并覆盖主探测 host/port，避免 DNS 别名已经指向备用时探测错误目标。

节点 Xray admin API socket、配置/日志/流量读取仍使用各节点管理 transport；这些是管理状态，不能替代业务协议健康。`/probe-dashboard` 展示周期业务探测，`PROBE_INTERVAL`、`PROBE_TIMEOUT`、`PROBE_TEST_LISTEN_PORT` 控制采样。

## DNS 故障切换

当 `DNS_FAILOVER_ENABLED=1` 且配置完整时，面板会后台周期性执行以下规则：

- 通过独立执行主机探测主入口 `DNS_FAILOVER_PROBE_HOST:DNS_FAILOVER_PROBE_PORT`
- DNS 故障切换探测运行在独立 worker 中，不会被数据面 SSH、日志同步或流量统计阻塞
- 连续失败达到 `DNS_FAILOVER_FAILURE_THRESHOLD` 时，把单条 Cloudflare DNS 记录切到备用目标
- 连续成功达到 `DNS_FAILOVER_RECOVERY_THRESHOLD` 时，自动回切到主数据面
- 如果启用了高峰窗口，窗口内会把备用/专用节点视为首选目标，窗口外恢复主节点优先
- AI 候选故障不触发 DNS 切换：`auto` 模式优先切换到另一候选，全部候选不可达时由 `ai_domain_manager` 回退；数据面故障时 DNS 切到控制面备用，AI 节点健康度决定备用是 relay 还是直出模式

故障场景矩阵（详见 [dns-failover.md](dns-failover.md)）：

| 场景 | DNS 切换 | 流量路径 |
| --- | --- | --- |
| 正常 | — | 客户端→数据面→直出；AI→数据面→AI节点→直出 |
| 单个 AI 候选故障 | 不切换 | 客户端→数据面→另一 AI 候选（auto） |
| 主、备 AI 候选同时故障 | 不切换 | 客户端→数据面→直出（AI 流量回退） |
| 数据面故障 | → backup | 客户端→控制面备用→relay→AI节点→直出 |
| 双节点故障 | → backup | 客户端→控制面备用→直出 |

手动入口：

- 首页 "DNS 故障切换" 卡片
- `POST /api/dns-failover/check`

### 数据面故障时的应急切换

自动切换异常或需要立即恢复业务时，按以下顺序操作：

1. 从外部网络确认控制面备用入口 `DNS_FAILOVER_BACKUP_CONTENT:DNS_FAILOVER_PROBE_PORT` 可达。
2. 在首页「DNS 故障切换」卡片中将目标手动切到 `backup`，或在 Cloudflare DNS 中把 `CF_DNS_RECORD_NAME` 指向 `DNS_FAILOVER_BACKUP_CONTENT`。
3. 确认 Cloudflare 记录已更新，并等待记录 TTL 生效。
4. 检查控制面备用 Xray 的 relay / direct 模式和客户端连接。
5. 数据面恢复后，不要立即手动回切；先确认 `DNS_FAILOVER_PROBE_HOST:DNS_FAILOVER_PROBE_PORT` 连续成功达到 `DNS_FAILOVER_RECOVERY_THRESHOLD`，再让系统自动回切。

如果数据库中的 `last_probe_checked_at` 长时间不更新，而控制面 HTTP 服务仍然可用，优先检查 DNS failover worker、控制面日志和远程 SSH 超时配置。此时不要只重启控制面或单纯调低失败阈值。
- `POST /api/dns-failover/switch`

生效速度建议：

- 非代理记录把 `CF_DNS_RECORD_TTL` 设为 `60`
- `DNS_FAILOVER_INTERVAL` 设小一些可以更快触发切换，但会增加探测频率和 Cloudflare API 调用概率
- 当前不支持 Cloudflare Load Balancer / Pool，也不做多记录原子切换

控制面备用 Xray（双模式）：

- 已新增 `docker compose` 服务 `xray-reality-backup`
- 双模式运行：AI 节点正常时 relay 到 AI 节点，AI 节点也故障时 freedom 直出
- 先把 `CONTROL_PLANE_BACKUP_XRAY_ENABLED=1` 写入根 `.env`
- 控制面作为备用时，启动方式为：`docker compose --profile backup-xray up -d xray-reality-backup`
- 如果控制面本机要接管流量，可把 `DNS_FAILOVER_BACKUP_CONTENT` 留空，让面板自动获取控制面本机公网 IP
- relay 模式只可使用与 AI 节点独立 inbound 完整匹配、受保护的 `CONTROL_PLANE_BACKUP_UPSTREAM_URL`
- 不得从普通数据面 `XRAY_*` 自动派生 relay URL；没有独立 AI 凭据时保持 relay 能力关闭
- 如果不想启用这套本机备用模式，保持 `CONTROL_PLANE_BACKUP_XRAY_ENABLED=0`，并手动填写 `DNS_FAILOVER_BACKUP_CONTENT`
- 完整机制详见 [dns-failover.md](dns-failover.md)

高峰专用节点示例：

- `DNS_FAILOVER_PEAK_ENABLED=1`
- `DNS_FAILOVER_PEAK_START=19:00`
- `DNS_FAILOVER_PEAK_END=23:00`
- `DNS_FAILOVER_PEAK_TIMEZONE=America/Los_Angeles`
- 启用后，面板会在该时区的 19:00-23:00 把备用目标当作首选线路

排查建议：

- 首页先确认“最近探测”与“当前 DNS 指向”是否一致
- `CF_API_TOKEN` 至少需要目标 Zone 的 DNS 编辑权限
- 如果自动切换没有发生，检查探测目标是否确实是数据面公网入口，而不是控制面地址

## 数据面重启与同步能力

### `docker`

- 可通过 `DATAPLANE_CONTAINER_NAME` 管理本地容器
- 默认重启目标是 `xray-reality-local`

### `local`

- 可做配置校验和本地 API 采样
- 进程重启和守护由你自己负责

### `ssh`

- 控制面先在本地渲染，再通过 SSH 上传配置
- 可读取远端 `access.log`、`dynamic-routing.json`、AI 报表和数据库快照

### `unmanaged`

- 面板仍可维护端口和租户数据
- 但不能自动重启、同步或读取数据面状态

## AI 节点重启与同步能力

AI 节点的模式判定与普通数据面相同（`ssh` / `local` / `docker` / `unmanaged`）。当前生产使用本机 `docker` 模式；设置 `AI_NODE_SSH_TARGET` 后才使用远端 `ssh` 模式。

### `ssh`（远端 SSH 纳管）

- 使用远端 SSH 时直接走内网连接和密码/键盘交互认证；应用不保存密码或私钥
- `AI_NODE_SSH_OPTIONS` 必须启用严格主机校验并使用专用 `known_hosts`
- `AI_NODE_API_SERVER` 用于远端 Socket 状态检查；当前生产检查 `redacted-ip-007:27166`
- `AI_NODE_CONFIG_PATH` 非空时才支持上传；生产当前显式留空，因此配置上传关闭
- 即使上传关闭，`GET /api/ai-node/status` 和 `POST /api/ai-node/restart` 仍可用
- AI 节点使用独立 REALITY 凭据，主数据面 outbound 必须与其 inbound 完整匹配
- 部署见 [AI 节点部署与 SSH 纳管](ai-node-deployment.md)，凭据见 [AI 节点独立凭据](ai-node-credentials.md)

### `docker`（本地测试）

- 通过 `AI_NODE_CONTAINER_NAME` 管理本地容器
- 适用于 `docker compose --profile ai-node` 本地测试场景

### `unmanaged`

- 面板仍可渲染 `config-ai-node.json`，但不能自动推送或重启

## 常见问题

### 端口显示不可达

优先检查：

- 数据面监听端口是否真的暴露在目标入口
- `DATAPLANE_PROBE_HOST` 是否仍错误地指向本地回环
- 防火墙或上游转发是否允许面板探测目标端口

### `/healthz` 一直失败

检查：

- 数据面是否在运行
- `DATAPLANE_API_SERVER` 是否可访问
- 如果当前只需要管理 UI，是否已经把 `PANEL_HEALTH_REQUIRES_XRAY=0`

当 DNS 已切到启用的控制面备用 Xray 时，`/healthz` 会把控制面接管状态视为健康；健康检查不会执行流量日志同步，避免主数据面失联时阻塞健康接口。

### 数据面无法重启

常见原因：

- 当前模式为 `unmanaged`
- 未设置 `DATAPLANE_RESTART_COMMAND`
- Docker 模式下 `DATAPLANE_CONTAINER_NAME` 错误

### AI 节点不可达

检查：

- `AI_NODE_SSH_TARGET`、内网 SSH 端口 `22` 和专用 `known_hosts` 是否正确
- 目标 SSH 服务是否允许密码/键盘交互认证，且人工连接可以完成登录
- `AI_NODE_API_SERVER` 指向的本机或远端 Socket 是否监听
- `AI_NODE_PROBE_HOST` 是否指向当前 AI 节点入口
- `AI_UPSTREAM_HOST:AI_UPSTREAM_PORT` 是否是当前 AI 业务端点
- `GET /api/ai-node/status` 返回的 `last_error` 字段
- 部署问题见 [AI 节点部署与 SSH 纳管](ai-node-deployment.md)

如果端口可达但 ChatGPT/OpenAI 仍不能连接，不要重复上传配置；按 [ChatGPT 路由排障](chatgpt-routing-troubleshooting.md) 比较主数据面 outbound 与 AI inbound 的凭据摘要，并核对 Docker 真实 bind source。

### AI 路由状态一直没有报告

检查：

- `docker compose --profile xray logs -f xray-ai-domain-manager`
- `app/xray/reports/hourly-domains/latest.json` 是否生成
- `AI_ROUTING_ENABLED` 是否为 `1`
- AI 候选是否可达，以及 `manual_mode` 是否意外固定在故障节点
- 自动模式下全部候选不可达时，`route_status` 应为 `fallback_to_primary`
- 人工固定目标不可达时，`route_status` 应为 `manual_target_unreachable`

## Codex 自动故障记录

独立协议探测的普通上游、AI 上游、DNS 主入口和按需体检结果继续记录既有探测结果；自动分析另由控制面 `IncidentStore` 使用共享面板 SQLite 数据库确认连续失败。节点认证请求失败与探测执行器错误分别标记为 `node_failure`、`executor_error`，后者不构成节点故障结论。连续失败默认达到五次才生成并排队故障记录，前四次仅持久化连续次数、首末时间和最新脱敏证据，不调用模型。阈值配置见[配置说明](configuration.md#故障分析隔离运行配置)。同一来源、目标、故障类别与探测主机的计数在面板和独立 AI 管理器进程间原子共享，进程重启不会丢失。故障类别或探测主机改变会中断未确认的连续计数；成功清空计数。既有健康判断与故障切换时序不变。

达到阈值时记录首个失败时间和实际观测次数；持续失败在尚未恢复的同类记录中累加，只排队一次分析，不为前几次失败伪造重复事件。执行器恢复后即使目标请求失败，也关闭旧执行器故障并重新确认节点故障；执行器再次失败不会表示节点恢复，尚未恢复的节点故障仍保留。成功目标请求关闭两个类别的故障并保留恢复时间，后续复发重新达到阈值才生成新记录。迁移保留旧记录、事件、报告和状态；旧排队记录的累计次数不作为连续失败证据，只有新观测达到阈值才允许认领。已经运行、完成或失败的旧分析保留原有生命周期，超时运行任务仍可恢复认领。

后台独立工作线程处理 `queued → running → completed/failed`，探测与故障切换不等待模型。超时的旧运行认领重新进入队列；模型不可用、超时、非零退出或格式错误保留 `failed`，不生成假诊断。故障后排查工作区显示状态、来源、目标、探测主机、时间、次数和恢复信息。文档按纯文本打开，模型内容不会执行 HTML。

文档保存在 `DATA_DIR/probe-incidents/reports/<固定32位事件ID>.md`，目录权限 `0700`、文件 `0600`，包含观测事实、Codex 分析、不确定性和建议检查。模型提示要求分析正文使用简体中文，JSON 字段名和主机名、协议名、错误码等技术标识保留原样；文档标题、章节和固定失败说明使用中文。已知历史英文分析错误在接口读取时显示为中文，不改写原始错误、失败状态或事件历史；分析失败文档明确说明未获得模型诊断。每份文档最多 128 KiB，接口拒绝无效 ID、符号链接和超限内容；访问仍受面板内网/Tailscale 来源限制。文档是私有运行数据，不追加到 Git 文档，也不自动发布到 GitHub。

每次分析通过已有 Docker CLI 启动固定镜像的临时容器：只读根文件系统、全部 capabilities 禁用、禁止权限提升、限制进程数/内存/CPU、独立 bridge 网络和私有 `/tmp`。只绑定经过筛选的认证输入目录（只读）和本次已脱敏的工作目录；不绑定生产配置、数据库、日志、SSH 密钥或 Docker socket。容器内仅复制 `auth.json` 与最小 `config.toml` 到私有 Codex home，使用只读 sandbox、临时模式、忽略规则并禁用 shell tool。模型只收到白名单探测快照，输出再次脱敏；不会自动修复或更改生产节点。

部署前必须由运维准备只含这两个文件的独立认证目录，文件权限 `0600`。配置仅保留当前认证所需的模型/provider 字段，不包含 hooks、plugins、projects、skills 或任意用户配置。Docker daemon 使用宿主路径，因此 `INCIDENT_CODEX_AUTH_HOME` 是宿主认证目录，`INCIDENT_CODEX_HOST_WORK_ROOT` 必须映射面板容器内 `DATA_DIR/probe-incidents/work` 的宿主路径。未提供镜像或认证路径时分析明确失败。环境配置见[配置说明](configuration.md)。

模型超时默认 180 秒（最大 600 秒）。容器内也有独立进程时限；父工作线程超时后显式 `docker rm -f` 本次容器，避免 Docker 客户端终止后模型继续运行。恢复观测与模型分析相互独立，恢复并不会伪造模型分析成功。
