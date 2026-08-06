# Prometheus-only 生产部署状态

> 状态：影子模式运行中
> 权威范围：当前部署入口、运行边界和旧方案迁移
> 最后核验日期：2026-08-05

旧版部署流程依赖 SSH Collector 和原始日志采集，已经停用，禁止继续按旧流程部署。旧 `xray-ops-log-collector` 容器已删除，不能作为回退方案恢复。

当前部署入口：

- [Exporter 部署与安全边界](exporter-deployment.md)
- [Prometheus targets 与标签](prometheus-targets.md)
- [分阶段上线与回滚](rollout.md)
- [生产验收](acceptance.md)

迁移后的 Reporter 不挂载 SSH 密钥、不执行远程命令、不读取原始日志。生产容器 `xray-ops-daily-reporter` 当前以 `rules_only` 运行，Prometheus API 使用控制面回环地址。

2026-08-05 已完成旧数据清理：旧采集表数据归零，`report_runs` 审计和已发布 JSON/Markdown 报告保留。清理前 SQLite 备份位于：

```text
/srv/xray-ops/ops.db.backup-before-legacy-purge-20260805T085249Z
```

备份只用于审计恢复，不得恢复旧 Collector 或重新启用原始日志采集。
