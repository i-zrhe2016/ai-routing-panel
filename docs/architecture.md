# 架构说明

## 总览

控制面通过 SSH 纳管两个远端节点：

- **普通数据面**：承载 `VLESS + REALITY` 流量，运行 `ai_domain_manager`
- **AI 节点**：接收数据面转发的 AI 域名流量，freedom 直出

- 控制面：`xray-routing-panel`
- 普通数据面：本地容器、本地二进制，或远端 SSH 目标上的 Xray
- AI 节点：远端 SSH 目标上的独立 Xray
- AI 路由子系统：`xray-ai-domain-manager`（运行在普通数据面上）
- 备份子系统：`xray-routing-panel-db-backup`
- 备份归档上传组件：`db-backup-uploader`

首页展示三节点状态（普通数据面、AI 节点、控制面备用）和当前流量导向路径，以及 AI 路由状态和 DNS 故障切换状态。
如果启用了 DNS 故障切换，首页还会额外展示当前 DNS 指向、最近探测结果和最近一次切换状态。

## 组件职责

### 控制面

- 入口代码：`app/panel.py`（进程入口）→ `app/web/`（`create_app` 工厂 + 按域视图模块）、`app/state/`（`PanelState` facade 组合域 service）
- `PanelState` 持有两个受管节点控制器：`data_plane`（普通数据面）和 `ai_node`（AI 节点），均复用 `DataPlaneController` / `ManagedNodeController`（`app/xray/node_control.py`）
- Flask 同时托管：管理后台 SPA（`/`）、订阅者门户 SPA（`/portal`）、公共/认证页（`/plans`、`/customer/*`）、租户订阅直达（`/tenant/<token>`）和探针/AI 仪表盘；前端是独立的 Vite 工程（`frontend/`），构建出 `app/static/{admin,portal}`
- 保存端口、租户、流量、AI 聚合，以及商业化数据（客户、套餐、订单、服务订阅、支付凭证）到 `data/panel.db`
- 保存 DNS 故障切换状态和事件历史到 `data/panel.db`
- 根据数据库内容生成 `app/xray/runtime/panel-ports.json`
- 调用 `python -m app.xray.render_config` 生成 `app/xray/runtime/config.json`（普通数据面）、`config-ai-node.json`（AI 节点）和 `config-backup.json`（控制面备用）
- 对普通数据面做配置校验、同步、重启、统计采集、探针采样和 Cloudflare DNS 切换
- 对 AI 节点做配置渲染、推送、校验、重启和可达性探测
- 以 Prometheus 文本格式暴露 `/metrics`（token 鉴权）；管理后台「监控」标签把这些指标经 Grafana（`monitoring/` 栈）以 `d-solo` iframe 内嵌出图，观测数据走 Prometheus，配置/事务数据仍走 `data/panel.db`

### 普通数据面

- 实际承载 `VLESS + REALITY` 流量
- 通过 Xray API 暴露 `statsquery`
- 通过 `access.log` 提供连接和域名观测输入
- 运行 `ai_domain_manager`，生成 `dynamic-routing.json` 将 AI 域名流量转发到 AI 节点
- AI 节点不可达时，`ai_domain_manager` 自动删除 `dynamic-routing.json`，AI 流量回退到数据面 freedom 直出

### AI 节点

- 远端独立机器上的 VLESS + REALITY Xray
- 监听 `AI_UPSTREAM_PORT`，接收来自普通数据面 `dynamic-routing.json` freedom redirect 转发的 AI 域名流量
- freedom 直出，不做域名分类、不运行 `ai_domain_manager`、无 panel-ports、无 access.log 采集
- 复用普通数据面同一套 REALITY 参数（私钥、公钥、shortId、UUID、SNI、dest）
- 控制面通过 SSH 纳管配置生命周期（渲染 → 推送 → 校验 → 重启）和可达性监控
- 详见 [ai-node-deployment.md](ai-node-deployment.md)

### 控制面备用 Xray

- 可选在控制面本机启动备用 `xray-reality-backup`，在 DNS 切换后接管入口流量
- 双模式运行：
  - **relay 模式**（AI 节点正常时）：将所有流量转发到 AI 节点
  - **直出模式**（AI 节点也故障时）：freedom 直出
- 控制面自动探测 AI 节点可达性并切换模式
- 详见 [dns-failover.md](dns-failover.md)

### AI 路由子系统

- 入口代码：`app/xray/ai_domain_manager.py`
- 从 `access.log` 统计小时域名窗口
- 结合内建规则、Codex 或 OpenAI 兼容接口做域名分类
- 输出动态路由片段、小时报表、数据库聚合快照

### 备份上传组件

- 入口代码：`scripts/run_db_backup_cycle.py`、`components/db-backup-uploader/`
- 先由 `scripts/backup_db.py` 生成新的 `panel.db` 备份
- 再按配置调用 `db-backup-uploader` 做加密、切片、上传和记录写入

## 节点模式判定

`app/xray/node_control.py` 中的 `DataPlaneController`（别名 `ManagedNodeController`）按以下优先级决定模式，对普通数据面和 AI 节点均适用：

1. `ssh`
   - 条件：设置了 `DATAPLANE_SSH_TARGET`（普通数据面）或 `AI_NODE_SSH_TARGET`（AI 节点）
   - 能力：同步配置、读取远端日志和报表、重启远端节点
2. `local`
   - 条件：设置了可执行的 `DATAPLANE_LOCAL_BIN`
   - 能力：本地校验配置和访问本地 API；进程守护由你自己负责
3. `docker`
   - 条件：存在可管理的 `DATAPLANE_CONTAINER_NAME`（普通数据面）或 `AI_NODE_CONTAINER_NAME`（AI 节点）
   - 能力：重启本地容器并读取本地 API
4. `unmanaged`
   - 条件：以上都不满足
   - 能力：面板仍可维护元数据和渲染配置，但不能自动重启或同步节点

AI 节点通常使用 `ssh` 模式。AI 域名同步模式在 UI 中会显示为：

- `远端镜像`：`ssh`
- `本地运行`：`local` 或 `docker`
- `本地缓存`：`unmanaged`

## 主要数据流

1. 管理员在 Web UI 或 `POST /api/ports` 修改端口状态。
2. `panel.db` 持久化端口、租户、流量和 AI 聚合数据。
3. `panel-ports.json` 记录当前有效监听端口。
4. `render_config.py` 合并 `app/xray/.env`、`panel-ports.json` 和可选 `dynamic-routing.json`，生成 `config.json`（普通数据面）、`config-ai-node.json`（AI 节点）和 `client-test.json`。
5. 控制面通过 SSH 将 `config.json` 推送到普通数据面，将 `config-ai-node.json` 推送到 AI 节点。
6. 普通数据面加载 `config.json` 并通过 Xray API 提供 `statsquery`。
7. `xray-ai-domain-manager` 从 `access.log` 读取域名，输出 AI 路由产物。AI 域名流量通过 `dynamic-routing.json` 转发到 AI 节点。
8. AI 节点不可达时，`ai_domain_manager` 删除 `dynamic-routing.json`，AI 流量回退数据面 freedom 直出。
9. DNS 故障切换后台任务对数据面公网入口做 TCP 探测，并在达到阈值时调用 Cloudflare API 更新单条记录。
10. 数据面故障时 DNS 切到控制面备用。控制面探测 AI 节点可达性：AI 节点正常 → relay 模式转发到 AI 节点；AI 节点也故障 → 自动切换为直出模式。
11. `xray-routing-panel-db-backup` 按 cron 生成 `backups/*.db`，并在启用时调用 `db-backup-uploader` 上传最新备份。
12. 首页读取三节点状态、流量导向路径、`ai_routing_status`、`dns_failover_status` 和 AI 域名聚合结果。

## 关键运行产物

- `data/panel.db`：端口、租户、流量和 AI 域名聚合
- `data/panel.db` 内 `dns_failover_state` / `dns_failover_history`：DNS 切换当前态和最近事件
- `backups/*.db`：最近几天的本地数据库备份
- `data/db-backup-uploader/upload-records.json`：最新上传记录和历史快照
- `data/db-backup-uploader/shards/`：最新一次备份的本地分片产物
- `app/xray/runtime/panel-ports.json`：当前有效监听端口列表
- `app/xray/runtime/config.json`：普通数据面 Xray 服务端配置
- `app/xray/runtime/config-ai-node.json`：AI 节点 Xray 服务端配置
- `app/xray/runtime/config-backup.json`：控制面备用 Xray 配置（relay 或直出模式）
- `app/xray/runtime/client-test.json`：本地客户端测试配置
- `app/xray/runtime/dynamic-routing.json`：AI 动态路由片段（AI 节点不可达时被删除）
- `app/xray/reports/hourly-domains/latest.json`：最近一小时域名报告
- `app/xray/logs/access.log`：连接和域名观测输入
