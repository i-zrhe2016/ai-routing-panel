# 每日日报器

> 权威范围：Prometheus 查询、规则执行和报告发布

日报器每天按 `Asia/Shanghai` 自然日查询 Prometheus HTTP API。它不访问 SSH，不读取原始日志，不部署 exporter，也不执行修复。

截至 2026-08-05，生产日报器以 `OPS_FORCE_RULES_ONLY=1` 影子模式运行。首份 2026-08-04 报告因窗口早于 Prometheus 上线而为 `unknown`，仅用于验证缺口处理、审计和原子发布，不作为业务状态结论。

## 生成流程

1. 验证 Prometheus 可用性、必需 labels 和 target 唯一性。
2. 对前一自然日执行版本化 range queries，计算覆盖率与 counter reset。
3. 将标准化指标交给确定性规则；缺失数据保持 `unknown`。
4. 从同一份已校验结果生成 JSON 和 Markdown，并原子发布。
5. 仅把本次运行元数据和报告归档索引写入 SQLite。

Prometheus 查询失败、标签冲突或覆盖不足时仍应生成明确标注缺口的规则报告；无法校验规则结果或无法原子发布时，本次运行失败且不发布半份报告。

## 职责边界

日报器不能从指标反推出日志内容或请求级根因。模型解释如被启用，只接收脱敏后的规则摘要，超时后降级为 `rules_only`，且不能覆盖规则结论。

相关文档：[Prometheus Targets](prometheus-targets.md)、[规则边界](fault-classification.md)、[SQLite 审计](report-run-audit.md)。
