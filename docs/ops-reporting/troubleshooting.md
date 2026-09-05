# Prometheus-only 故障排查

> 权威范围：target、查询和报告故障；不包含 SSH 或原始日志排查

## 2026-08-31 目标抓取恢复记录

普通数据面 target 曾配置为公网地址 `redacted-ip-011:19100/18081`。从控制面访问这两个端口失败，而通过 Tailscale 地址 `redacted-ip-003:19100/18081` 均返回 HTTP 200，故障根因是 Prometheus 抓取路径与数据面防火墙边界不一致。

已执行：

- 将 `data-plane-node` 和 `data-plane-cadvisor` 的普通数据面 target 固定为 `redacted-ip-003`；
- 恢复控制面面板的 `METRICS_TOKEN`，并让 Prometheus 以仅自身 UID 可读的文件读取同一 token；
- 启动控制面 node-exporter 和 Prometheus，校验配置及 active targets。

核验结果：Prometheus 的 7 个 active targets 均为 `up`，普通数据面两个 target 的 `up` 值均为 `1`，面板 `/metrics` 带认证返回 HTTP 200。修复前报告窗口的缺失样本不能回填，历史 `unknown` 仍应保留；应等待完整采集窗口后再生成新的报告。

## Codex 返回 401

如果 `codex login status` 能识别认证方式，但日报日志仍出现 `401 Unauthorized`，先确认运行时 `auth.json` 是官方登录 token，并且对应配置包含 `model_provider = "openai"`。文件存在或 CLI 能读取 profile 不代表服务端验签成功；应在同一个 `CODEX_HOME` 下做一次真实 Codex 请求验证。不要把 `auth.json` 提交到仓库或粘贴到工单。

## Target 为 down

普通数据面 exporter 应使用其 Tailscale 地址（当前为 `redacted-ip-003`），不要从控制面通过公网地址抓取。依次检查 Prometheus `/targets` 的错误、Tailscale 路由、exporter 进程、监听地址、TLS/认证和防火墙来源限制。不得临时向公网放开指标端口；使用与 Prometheus 相同的授权源验证。

## AI Targets 为 down

AI node-exporter 和 cAdvisor 通过控制面回环 SSH 隧道抓取。先检查 `xray-ai-exporter-tunnel.service` 是否 active，以及 `redacted-ip-007:19101`、`redacted-ip-007:18082` 是否监听；再检查 SSH 管理端口、严格主机密钥校验和远端 exporter 容器。不得把本地转发地址改成 `redacted-ip-001`，也不得为排障开放公网 exporter 端口。隧道异常只影响 AI 遥测，不应修改或重启 AI Xray 业务配置。

## 报告为 unknown

检查必需 labels、重复序列、抓取间隔、时钟同步、Prometheus 保留窗口和查询步长。缺失样本不能用零填充。修复数据源后重跑同一窗口，并保留失败运行审计。

如果报告窗口早于 Prometheus 上线时间，覆盖率为 0% 和状态 `unknown` 是预期结果，不能据此判定业务故障，也不能通过重跑补出不存在的历史样本。应等待一个完整采集窗口后生成新的影子报告。

## Codex 不可用或日报未生成

正常模式要求 Codex 成功完成 schema 校验；Codex 缺失、认证失败、进程失败、超时或输出不合法时，`report_runs` 应记录失败，且不应生成或发布 `rules_only` 报告。先在日报容器中执行 `codex --version`，再检查认证种子和 API 返回状态；不要把旧的 `rules_only` 报告当作当前窗口已完成。`--rules-only` 和 `OPS_FORCE_RULES_ONLY=1` 只用于明确的影子/维护运行。

## 指标与业务感知冲突

先确认 exporter 与 blackbox 是否测量同一对象，再检查 counter reset 和 label 漂移。Prometheus-only 无法给出日志级根因；需要人工在节点本地按独立安全流程排查，但不得把原始日志导入本子系统。

## SQLite 或报告发布失败

检查审计目录权限、磁盘空间、数据库完整性和原子重命名所在文件系统。SQLite 只含运行审计与历史索引；不要向其中补写遥测或日志。
