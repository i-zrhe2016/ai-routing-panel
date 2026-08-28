# 每日日报器

> 权威范围：Prometheus 查询、脱敏归因快照、规则执行和报告发布

日报器每天按 `Asia/Shanghai` 自然日查询 Prometheus HTTP API，并读取本地 SQLite 中已脱敏的 Xray counter 快照。日报器本身不访问 SSH，不读取原始日志，不部署 exporter，也不执行修复。

截至 2026-08-05，生产日报器以 `OPS_FORCE_RULES_ONLY=1` 影子模式运行。首份 2026-08-04 报告因窗口早于 Prometheus 上线而为 `unknown`，仅用于验证缺口处理、审计和原子发布，不作为业务状态结论。

## 生成流程

1. 验证 Prometheus 可用性、必需 labels 和 target 唯一性。
2. 对前一自然日执行版本化 range queries，计算覆盖率、counter reset 和两个数据面的日流量增量。
3. 从 SQLite 读取 `xray-ops-attribution-sampler` 写入的脱敏 user/inbound counter 快照，计算报告窗口内的增量归因。
4. 将标准化指标交给确定性规则；缺失数据保持 `unknown`。
5. 从同一份已校验结果生成 JSON 和 Markdown，并原子发布。
6. 将 Markdown 和 JSON 复制到仓库内 `ops-daily-reports/<year>/`，只提交该目录并推送到 GitHub。
7. 仅把本次运行元数据和报告归档索引写入 SQLite。

Prometheus 查询失败、标签冲突或覆盖不足时仍应生成明确标注缺口的规则报告；无法校验规则结果或无法原子发布时，本次运行失败且不发布半份报告。

## 数据面流量

每个节点段落都会展示普通数据面和 AI 数据面的日总流量、入站流量、出站流量、网络流量覆盖率和计入接口列表。流量来源为 Prometheus 中 `job="data-plane-node"` 的 `node_network_receive_bytes_total` 与 `node_network_transmit_bytes_total`，按 `node_role` 分别汇总。

日报只计入数据面主机上的公网/物理网络接口前缀：`eth`、`ens`、`enp`、`eno`、`enx`、`bond`、`wan`。`lo`、Docker bridge、veth、Tailscale 等虚拟或管理链路不会进入日报总流量，避免把监控隧道和容器内部转发重复计数。

## 脱敏归因

`xray-ops-attribution-sampler` 可选采集 Xray `/debug/vars` 中的 `stats.user.*` 与 `stats.inbound.*` 聚合 counter。采样器在解析后立即用本地盐做 HMAC，SQLite 和日报只保存 `usr-<16 hex>`、`inb-<16 hex>`、方向和 counter 值；不保存原始用户 email/UUID、inbound tag、客户端 IP、访问域名或 URL。

日报中的 user 与 inbound 是同一批流量的两种视角，不应相加。至少需要同一报告窗口附近的两次快照才能计算增量；启用采样器前的历史流量不能回溯到用户级。

相关环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `OPS_XRAY_STATS_ENABLED` | `0` | 是否启用归因采样器。 |
| `OPS_XRAY_STATS_SAMPLE_INTERVAL_SECONDS` | `300` | 脱敏 counter 快照采样间隔。 |
| `OPS_XRAY_STATS_REDACTION_SALT_FILE` | `/data/xray-ops/xray-stats-redaction-salt` | 本地 HMAC 盐文件；不进入 GitHub 归档。 |
| `OPS_XRAY_STATS_SSH_KEY_HOST_PATH` | `/dev/null` | 宿主机 SSH 私钥挂载源，仅采样器使用。 |
| `OPS_XRAY_STATS_AI_ENABLED` | `0` | 是否启用 AI 数据面 Xray stats 源。 |
| `OPS_XRAY_STATS_AI_SSH_TARGET` | 空 | AI 节点 SSH 管理端点。 |
| `OPS_XRAY_STATS_AI_KNOWN_HOSTS_HOST_PATH` | `/dev/null` | 宿主机 known_hosts 挂载源。 |
| `OPS_XRAY_STATS_AI_CONTAINER` | `xray` | 远端 Xray 容器名。 |
| `OPS_XRAY_STATS_AI_METRICS_PORT` | `31097` | 容器内 `/debug/vars` 端口。 |
| `OPS_XRAY_STATS_WINDOW_PADDING_SECONDS` | `900` | 日报计算归因时读取窗口前后的采样缓冲。 |

## GitHub 归档

日报器支持在每次报告成功发布后，把结果归档到 Git 仓库并推送：

```text
ops-daily-reports/
  README.md
  2026/
    2026-08-26.md
    2026-08-26.json
```

相关环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `OPS_GITHUB_REPORTS_ENABLED` | `1` | 是否启用 GitHub 归档发布。 |
| `OPS_GITHUB_REPORTS_REPO_HOST_DIR` | 本地 compose 为 `.` | 宿主机上的 Git 仓库路径，挂载到容器 `/reports-repo`。 |
| `OPS_GITHUB_REPORTS_REPO_DIR` | `/reports-repo` | 容器内 Git 仓库路径。 |
| `OPS_GITHUB_REPORTS_OUTPUT_SUBDIR` | `ops-daily-reports` | 仓库内日报归档目录。 |
| `OPS_GITHUB_REPORTS_REMOTE` | `origin` | 推送目标 remote。 |
| `OPS_GITHUB_REPORTS_BRANCH` | 当前分支 | 推送目标分支；留空使用当前分支。 |
| `OPS_GITHUB_REPORTS_PUSH_ENABLED` | `1` | 是否执行 `git push`；设为 `0` 时只提交不推送。 |
| `OPS_GITHUB_REPORTS_AUTHOR_NAME` | `i-zrhe2016` | 自动提交作者。 |
| `OPS_GITHUB_REPORTS_AUTHOR_EMAIL` | `zrhe2016@gmail.com` | 自动提交邮箱。 |
| `OPS_GITHUB_REPORTS_TOKEN_HOST_PATH` | `/dev/null` | 宿主机上的 GitHub token 文件路径；通过只读挂载进入容器。 |
| `OPS_GITHUB_REPORTS_TOKEN_FILE` | `/run/secrets/github_reports_token` | 容器内 GitHub token 文件路径；HTTPS remote 推送时使用。 |

发布器只执行这些路径的 `git add` 和 `git commit`：

```text
ops-daily-reports/README.md
ops-daily-reports/<year>/<date>.md
ops-daily-reports/<year>/<date>.json
```

如果仓库落后于 upstream、GitHub 凭据不可用或推送失败，日报文件仍保留在 `/data/xray-ops/reports`，调度器记录 `report_github_publish_failed` 日志并在下一轮继续尝试发布已完成日报。

## 职责边界

日报器不能从指标反推出日志内容或请求级根因。归因表只说明哪个脱敏 user/inbound counter 增长最多，不说明具体访问了什么域名或内容。模型解释如被启用，只接收脱敏后的规则摘要，超时后降级为 `rules_only`，且不能覆盖规则结论。

相关文档：[Prometheus Targets](prometheus-targets.md)、[规则边界](fault-classification.md)、[SQLite 审计](report-run-audit.md)。
