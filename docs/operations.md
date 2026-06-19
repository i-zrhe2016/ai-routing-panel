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

## TCP 探针

当 `PROBE_ENABLED=1` 时，面板会周期性对 `DATAPLANE_PROBE_HOST:<listen_port>` 做 TCP 连通性探测。

相关页面与配置：

- 页面：`/probe-dashboard`
- 常用变量：`PROBE_INTERVAL`、`PROBE_TIMEOUT`、`PROBE_TEST_LISTEN_PORT`
- compose 默认把 `PROBE_INTERVAL` 设为 `180` 秒

远端模式注意：

- 探针目标不能继续是 `127.0.0.1`
- 把 `DATAPLANE_PROBE_HOST` 设置成远端入口 IP 或域名

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
