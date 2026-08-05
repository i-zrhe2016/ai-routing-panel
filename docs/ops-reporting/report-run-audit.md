# SQLite 报告运行审计与历史归档

> 权威范围：SQLite 在 Prometheus-only 方案中的唯一用途

SQLite 仅用于报告运行审计和历史报告归档索引，不是遥测数据库。

## 允许保存

- 运行 ID、报告日期、开始/结束时间、状态和规则版本；
- Prometheus 查询窗口、查询模板版本、样本覆盖率和错误摘要；
- JSON/Markdown 报告路径、内容摘要、生成模式和发布时间；
- 重试次数、程序版本与人工确认记录。

## 禁止保存

不得保存 Prometheus 全量样本、Xray 原始日志、Docker/systemd 输出、请求内容、客户端标识、订阅 token、SSH 凭据或未经筛选的模型输入。规则计算所需样本只在单次运行内存或受控临时目录存在，运行结束后删除。

## 生产清理记录

2026-08-05 已停止并删除旧 Collector，清空 `collection_cursors`、`raw_log_events`、`node_samples`、`collection_runs`、`telemetry_gaps`、`rollups_5m` 和 `service_heartbeats` 的历史数据。共享存储模块仍可能创建这些空表，因此验收应检查其行数保持为零，而不是只检查表名不存在。

`report_runs` 和现有报告文件予以保留。清理前备份为 `/srv/xray-ops/ops.db.backup-before-legacy-purge-20260805T085249Z`，数据库清理后已通过 SQLite 完整性检查。

## 生命周期

报告文件和审计记录采用相同的保留策略，默认 90 天；删除报告时同步删除对应索引。数据库使用最小文件权限，备份只为审计恢复，不替代 Prometheus 的时序保留。回滚时保留 SQLite 和已发布报告，以便确认旧版本运行历史；恢复服务前执行完整性检查。
