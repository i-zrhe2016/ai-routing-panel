# 旧部署文档迁移说明

> 状态：已停用
> 权威范围：旧 SSH 部署入口的兼容跳转
> 最后核验日期：2026-07-31

旧版部署流程依赖 SSH Collector 和原始日志采集，已经停用，禁止继续按旧流程部署。

当前部署入口：

- [Exporter 部署与安全边界](exporter-deployment.md)
- [Prometheus targets 与标签](prometheus-targets.md)
- [分阶段上线与回滚](rollout.md)
- [生产验收](acceptance.md)

迁移后的 Reporter 不挂载 SSH 密钥、不执行远程命令、不读取原始日志。历史 SQLite 数据仅作为归档保留，不再参与节点状态判定。
