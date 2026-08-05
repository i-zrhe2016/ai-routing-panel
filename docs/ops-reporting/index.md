# Prometheus-only 节点运维分析

> 状态：Prometheus-only 采集与影子日报已部署，正式验收待完成
> 权威范围：本专题边界与模块导航
> 最后核验日期：2026-08-05

本专题只使用 Prometheus 时序指标生成普通数据面与 AI 数据面的运维结论和日报。

## 强制边界

- 不通过 SSH 登录节点，不远程执行命令。
- 不读取、复制、解析或保存 Xray、systemd、Docker 等原始日志。
- 节点只部署指标 exporter；控制面通过 Prometheus HTTP API 查询聚合后的指标。
- exporter 端口只允许 Prometheus 抓取源访问，不向公网开放。
- SQLite 不保存遥测、日志或规则证据，只保留报告运行审计与历史报告归档索引。
- 规则只能判断指标能够证明的运行、可达性、流量连续性和资源风险，不能推断日志级根因。

![Prometheus-only 监控与日报架构](diagrams/monitoring-reporting.svg)

[查看 PlantUML 源文件](diagrams/monitoring-reporting.puml)

## 模块导航

| 模块 | 文档 |
| --- | --- |
| exporter 安装、最小权限和防火墙 | [exporter-deployment.md](exporter-deployment.md) |
| Prometheus targets、labels 与查询约束 | [prometheus-targets.md](prometheus-targets.md) |
| 确定性规则能力与边界 | [fault-classification.md](fault-classification.md) |
| 日报生成与报告契约 | [daily-reporter.md](daily-reporter.md)、[report-contract.md](report-contract.md) |
| SQLite 审计与历史归档 | [report-run-audit.md](report-run-audit.md) |
| 灰度、验收和回滚 | [rollout.md](rollout.md)、[acceptance.md](acceptance.md) |
| 日常排障 | [troubleshooting.md](troubleshooting.md) |

旧版 SSH 日志采集器、原始日志入库和日志解析流程不属于本方案，不应部署或作为回退路径保留。

## 当前生产状态

截至 2026-08-05，控制面 Prometheus 的 7 个 targets 均可抓取，日报器以 `rules_only` 影子模式运行。AI 数据面位于 NAT 后，指标通过只绑定控制面回环地址的 SSH 隧道抓取；该隧道只承载 exporter HTTP，不改变 Xray 业务链路。

旧 Collector 容器已经删除，旧采集表的数据已清空；`report_runs` 审计和已发布报告继续保留。首份 2026-08-04 影子报告因统计窗口早于 Prometheus 上线而为 `unknown`，属于历史样本不足，不代表业务故障。完整 30 分钟观察门禁按运维决定跳过，因此当前状态仍是影子运行而非正式验收通过。
