# Prometheus 与脱敏归因生产部署状态

> 状态：影子模式运行中
> 权威范围：当前部署入口、运行边界和旧方案迁移
> 最后核验日期：2026-08-31

旧版部署流程依赖 SSH Collector 和原始日志采集，已经停用，禁止继续按旧流程部署。旧 `xray-ops-log-collector` 容器已删除，不能作为回退方案恢复。

当前部署入口：

- [Exporter 部署与安全边界](exporter-deployment.md)
- [Prometheus targets 与标签](prometheus-targets.md)
- [分阶段上线与回滚](rollout.md)
- [生产验收](acceptance.md)

迁移后的 Reporter 不挂载 SSH 密钥、不执行远程命令、不读取原始日志。生产容器 `xray-ops-daily-reporter` 查询 Prometheus API，并读取本地 SQLite 中已经脱敏的归因快照。

可选生产容器 `xray-ops-attribution-sampler` 只用于读取 Xray `/debug/vars` 聚合 counter。它可挂载 fleet SSH key 访问 AI 节点，但执行的远端命令仅限定位 Xray 容器 IP 并读取 stats HTTP 端点；解析后立即丢弃原始 user/inbound 字符串，只把 HMAC 后的 `usr-*`、`inb-*` 和 counter 写入 SQLite。该容器不得读取 Xray access/error log，也不得把客户端 IP、访问域名、URL、UUID 或 email 写入数据库或 GitHub 归档。

2026-08-05 已完成旧数据清理：旧采集表数据归零，`report_runs` 审计和已发布 JSON/Markdown 报告保留。清理前 SQLite 备份位于：

```text
/srv/xray-ops/ops.db.backup-before-legacy-purge-20260805T085249Z
```

备份只用于审计恢复，不得恢复旧 Collector 或重新启用原始日志采集。

2026-08-31 已恢复控制面 Prometheus 采集：普通数据面使用 Tailscale `redacted-ip-003` 抓取 node-exporter/cAdvisor，控制面面板指标认证已补齐，7 个 active targets 均已核验为 `up`。本次修复不改变历史报告结论；修复前时间区间的缺失样本不存在，需从修复后的完整窗口开始重新评估。
