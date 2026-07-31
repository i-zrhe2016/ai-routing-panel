# SSH 日志采集器停用说明

> 状态：已停用，不得部署
> 权威范围：旧 `xray-ops-log-collector` 的生命周期状态
> 最后核验日期：2026-07-31

`xray-ops-log-collector` 已从运行 Compose 中移除。当前架构不通过 SSH 连接数据面，不读取或保存 Xray、Docker、systemd 原始日志，也不再生成日志游标、raw events、五分钟日志 rollup 或采集缺口记录。

替代链路：

```mermaid
flowchart LR
    E[node_exporter / cAdvisor] --> P[Prometheus]
    P --> R[Daily Reporter]
    R --> J[确定性规则与日报]
```

- Exporter 部署：[exporter-deployment.md](exporter-deployment.md)
- Prometheus 数据源：[prometheus-targets.md](prometheus-targets.md)
- 规则能力边界：[fault-classification.md](fault-classification.md)

旧 SQLite 遥测数据仅保留为历史归档，不参与新报告判定。是否撤销数据面已有 SSH 公钥是独立安全变更，必须核验具体公钥指纹并另行批准；迁移过程不自动修改 `authorized_keys`。
