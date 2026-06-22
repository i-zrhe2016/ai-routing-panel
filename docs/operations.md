# 运维与排障

## 健康检查

- 接口：`GET /healthz`
- 返回体：`{"ok": <bool>, "data_plane_running": <bool>}`
- 默认行为：要求数据面可用时才返回健康

如果你只启动面板而不启动数据面：

- 设置 `PANEL_HEALTH_REQUIRES_XRAY=0`

## 流量与连接统计

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

- 到期端口会自动删除
- 达到流量上限的端口会自动停用
- “重置流量并启用”会清零累计流量与当日流量，但不会清零连接数

## 数据库备份与上传

- `xray-routing-panel-db-backup` 默认每天 `03:00 UTC` 备份一次 `panel.db`
- 本地备份文件落在 `./backups`
- 当 `DB_BACKUP_UPLOADER_ENABLED=1` 时，备份成功后会继续调用 `db-backup-uploader`
- 上传成功后会尝试删除上一份备份的 npm 包版本，只保留最新一份上传记录

上传链路依赖：

- `DB_BACKUP_UPLOADER_PASSWORD`
- 有效的 npm 认证配置，默认读取 `./data/db-backup-uploader/.npmrc`
- 如需先验证流程，可设置 `DB_BACKUP_UPLOADER_DRY_RUN=1`

排查建议：

- 查看日志：`docker compose logs -f xray-routing-panel-db-backup`
- 确认最新本地备份已生成到 `./backups`
- 确认 `./data/db-backup-uploader/upload-records.json` 是否已更新

## TCP 探针

当 `PROBE_ENABLED=1` 时，面板会周期性对 `DATAPLANE_PROBE_HOST:<listen_port>` 做 TCP 连通性探测。

相关页面与配置：

- 页面：`/probe-dashboard`
- 常用变量：`PROBE_INTERVAL`、`PROBE_TIMEOUT`、`PROBE_TEST_LISTEN_PORT`
- compose 默认把 `PROBE_INTERVAL` 设为 `180` 秒

远端模式注意：

- 探针目标不能继续是 `127.0.0.1`
- 把 `DATAPLANE_PROBE_HOST` 设置成远端入口 IP 或域名

## DNS 故障切换

当 `DNS_FAILOVER_ENABLED=1` 且配置完整时，面板会后台周期性执行以下规则：

- 只探测 `DNS_FAILOVER_PROBE_HOST:DNS_FAILOVER_PROBE_PORT`
- 连续失败达到 `DNS_FAILOVER_FAILURE_THRESHOLD` 时，把单条 Cloudflare DNS 记录切到备用目标
- 连续成功达到 `DNS_FAILOVER_RECOVERY_THRESHOLD` 时，自动回切到主数据面
- 如果启用了高峰窗口，窗口内会把备用/专用节点视为首选目标，窗口外恢复主节点优先
- AI 路由或 AI 节点状态不会触发任何 DNS 切换

手动入口：

- 首页 “DNS 故障切换” 卡片
- `POST /api/dns-failover/check`
- `POST /api/dns-failover/switch`

生效速度建议：

- 非代理记录把 `CF_DNS_RECORD_TTL` 设为 `60`
- `DNS_FAILOVER_INTERVAL` 设小一些可以更快触发切换，但会增加探测频率和 Cloudflare API 调用概率
- 当前不支持 Cloudflare Load Balancer / Pool，也不做多记录原子切换

控制面备用 Xray：

- 已新增 `docker compose` 服务 `xray-reality-backup`
- 它复用同一份 `app/xray/runtime/config.json`，也就是除了 IP 以外，REALITY 参数与主线路保持一致
- 先把 `CONTROL_PLANE_BACKUP_XRAY_ENABLED=1` 写入根 `.env`
- 控制面作为备用时，启动方式为：`docker compose --profile backup-xray up -d xray-reality-backup`
- 如果控制面本机要接管流量，可把 `DNS_FAILOVER_BACKUP_CONTENT` 留空，让面板自动获取控制面本机公网 IP
- 如果不想启用这套本机备用模式，保持 `CONTROL_PLANE_BACKUP_XRAY_ENABLED=0`，并手动填写 `DNS_FAILOVER_BACKUP_CONTENT`

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

### 数据面无法重启

常见原因：

- 当前模式为 `unmanaged`
- 未设置 `DATAPLANE_RESTART_COMMAND`
- Docker 模式下 `DATAPLANE_CONTAINER_NAME` 错误

### AI 路由状态一直没有报告

检查：

- `docker compose --profile xray logs -f xray-ai-domain-manager`
- `app/xray/reports/hourly-domains/latest.json` 是否生成
- `AI_ROUTING_ENABLED` 是否为 `1`
