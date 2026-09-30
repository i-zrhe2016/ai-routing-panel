# 项目概览

> Type: Guide
> Status: Active
> Scope: 项目定位、能力边界、代码入口与详细文档的阅读顺序

`xray-routing-panel` 是面向开发者和运维人员的 Xray REALITY 控制面，统一管理普通数据面、独立 AI 数据面、客户订阅、流量观测、故障切换和灾备归档。

## 能力边界

| 领域 | 能力 | 详细说明 |
| --- | --- | --- |
| 控制面 | 管理端口、租户、客户、套餐、订单和订阅，编排节点配置 | [架构说明](architecture.md)、[API](api.md) |
| 普通数据面 | 承载代理流量，按动态域名规则选择 AI 上游 | [AI 路由](ai-routing.md) |
| AI 数据面 | 使用独立凭据接收 AI 流量并独立出站 | [AI 节点部署](ai-node-deployment.md)、[凭据契约](ai-node-credentials.md) |
| 故障切换 | 选择 AI 候选、回退直出，切换 DNS 到控制面备用入口 | [DNS 故障切换](dns-failover.md)、[三节点容错](fault-tolerance.md) |
| 可观测性 | Prometheus/Grafana 指标、Loki 日志和每日运维报告 | [运维](operations.md)、[日志采集](logging-fluent-bit.md)、[日报器](ops-reporting/daily-reporter.md) |
| 灾备 | SQLite 快照、节点配置采集、完整性校验、加密 R2 上传与隔离恢复 | [灾备归档](disaster-backup.md)、[节点恢复](node-recovery.md) |

控制面负责配置和管理；正常代理流量由普通数据面与 AI 数据面承载。控制面不可用时，已下发的数据面配置仍可工作，管理页面、订阅更新和自动运维的可用性不由此保证。组件拓扑见[架构说明](architecture.md#总览)。

## 开始使用

1. 按根目录 [README](../README.md#快速开始) 准备最小运行环境。
2. 按[配置说明](configuration.md)选择本机或远端数据面及可选组件。
3. 按[开发与启动](development.md)启动对应服务。
4. 按[控制面访问](panel-access.md)限制来源，并使用[运维与排障](operations.md)检查运行状态。

独立 AI 节点、统一 443 入口、DNS 切换、日志和备份均有各自的部署说明，完整入口只在 [README](../README.md#完整文档导航) 维护。

## 代码入口

| 入口 | 职责 |
| --- | --- |
| `app/panel.py`、`app/bootstrap.py` | 进程启动与 Application 组装 |
| `app/web/` | 页面、JSON API 与应用消费层 |
| `app/state/`、`app/storage/` | 领域服务、状态与 SQLite 持久化 |
| `app/xray/node/`、`app/xray/apply.py` | 节点管理与配置应用 |
| `app/xray/ai_routing/` | 域名观测、分类、AI 候选选择和路由产物 |
| `frontend/src/admin/`、`app/static/admin/` | React 管理后台源码与发布资源 |
| `scripts/run_db_backup_cycle.py` | 定时备份与归档任务 |
| `monitoring/` | 指标、Grafana 和日志采集部署配置 |

已核实的实现、限制与当前状态见[仓库当前状态](Repo_Current_State.md)；历史记录不作为当前线上拓扑的依据。
