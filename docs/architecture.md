# 架构说明

## 总览

当前仓库围绕一个单一 `data_plane` 工作：

- 控制面：`xray-routing-panel`
- 数据面：本地容器、本地二进制，或远端 SSH 目标上的 Xray
- AI 路由子系统：`xray-ai-domain-manager`
- 备份子系统：`xray-routing-panel-db-backup`
- 备份归档上传组件：`db-backup-uploader`

首页只展示数据面状态和 AI 路由状态，不再展示“独立 AI 节点”。

## 组件职责

### 控制面

- 入口代码：`app/web.py`、`app/state.py`
- 保存端口、租户、流量和 AI 聚合数据到 `data/panel.db`
- 根据数据库内容生成 `app/xray/runtime/panel-ports.json`
- 调用 `python -m app.xray.render_config` 生成 `app/xray/runtime/config.json`
- 对数据面做配置校验、同步、重启、统计采集和探针采样

### 数据面

- 实际承载 `VLESS + REALITY` 流量
- 通过 Xray API 暴露 `statsquery`
- 通过 `access.log` 提供连接和域名观测输入

### AI 路由子系统

- 入口代码：`app/xray/ai_domain_manager.py`
- 从 `access.log` 统计小时域名窗口
- 结合内建规则、Codex 或 OpenAI 兼容接口做域名分类
- 输出动态路由片段、小时报表、数据库聚合快照

### 备份上传组件

- 入口代码：`scripts/run_db_backup_cycle.py`、`components/db-backup-uploader/`
- 先由 `scripts/backup_db.py` 生成新的 `panel.db` 备份
- 再按配置调用 `db-backup-uploader` 做加密、切片、上传和记录写入

## 数据面模式判定

`app/xray/node_control.py` 中的数据面控制器按以下优先级决定模式：

1. `ssh`
   - 条件：设置了 `DATAPLANE_SSH_TARGET`
   - 能力：同步配置、读取远端日志和报表、重启远端数据面
2. `local`
   - 条件：设置了可执行的 `DATAPLANE_LOCAL_BIN`
   - 能力：本地校验配置和访问本地 API；进程守护由你自己负责
3. `docker`
   - 条件：存在可管理的 `DATAPLANE_CONTAINER_NAME`
   - 能力：重启本地容器并读取本地 API
4. `unmanaged`
   - 条件：以上都不满足
   - 能力：面板仍可维护元数据和渲染配置，但不能自动重启或同步数据面

AI 域名同步模式在 UI 中会显示为：

- `远端镜像`：`ssh`
- `本地运行`：`local` 或 `docker`
- `本地缓存`：`unmanaged`

## 主要数据流

1. 管理员在 Web UI 或 `POST /api/ports` 修改端口状态。
2. `panel.db` 持久化端口、租户、流量和 AI 聚合数据。
3. `panel-ports.json` 记录当前有效监听端口。
4. `render_config.py` 合并 `app/xray/.env`、`panel-ports.json` 和可选 `dynamic-routing.json`，生成 `config.json`、`client-test.json`、分享链接。
5. 数据面加载 `config.json` 并通过 Xray API 提供 `statsquery`。
6. `xray-ai-domain-manager` 从 `access.log` 读取域名，输出 AI 路由产物。
7. `xray-routing-panel-db-backup` 按 cron 生成 `backups/*.db`，并在启用时调用 `db-backup-uploader` 上传最新备份。
8. 首页读取 `data_plane_status`、`ai_routing_status` 和 AI 域名聚合结果。

## 关键运行产物

- `data/panel.db`：端口、租户、流量和 AI 域名聚合
- `backups/*.db`：最近几天的本地数据库备份
- `data/db-backup-uploader/upload-records.json`：最新上传记录和历史快照
- `data/db-backup-uploader/shards/`：最新一次备份的本地分片产物
- `app/xray/runtime/panel-ports.json`：当前有效监听端口列表
- `app/xray/runtime/config.json`：Xray 服务端配置
- `app/xray/runtime/client-test.json`：本地客户端测试配置
- `app/xray/runtime/dynamic-routing.json`：AI 动态路由片段
- `app/xray/reports/hourly-domains/latest.json`：最近一小时域名报告
- `app/xray/logs/access.log`：连接和域名观测输入
