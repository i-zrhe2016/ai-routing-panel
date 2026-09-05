# 每日日报器

> 权威范围：Prometheus 查询、脱敏归因快照、AI 域名聚合、规则执行和报告发布

日报器每天按 `Asia/Shanghai` 自然日查询 Prometheus HTTP API，并读取本地 SQLite 中已脱敏的 Xray counter 快照。日报器本身不访问 SSH，不读取原始日志，不部署 exporter，也不执行修复。

当前日报 JSON/Markdown 契约版本为 `1.1`；旧版 `1.0` 报告不会被当作当前日期的完整报告，调度器会在下一次运行时重新生成。

截至 2026-08-05，生产日报器以 `OPS_FORCE_RULES_ONLY=1` 影子模式运行。首份 2026-08-04 报告因窗口早于 Prometheus 上线而为 `unknown`，仅用于验证缺口处理、审计和原子发布，不作为业务状态结论。

## 生成流程

1. 验证 Prometheus 可用性、必需 labels 和 target 唯一性。
2. 对前一自然日执行版本化 range queries，计算覆盖率、counter reset 和两个数据面的日流量增量。
3. 从 SQLite 读取 `xray-ops-attribution-sampler` 写入的脱敏 user/inbound counter 快照，计算报告窗口内的增量归因。
4. 读取 AI 管理器的小时域名报表和 `panel.db` 的 `ai_domains` / `ai_domain_observations`，汇总分类、每日新增域名、每个域名的命中次数与流量导向；相同域名在窗口内发生出口变化时保留为 `mixed`。
5. 将标准化指标与有界的 AI 域名摘要交给确定性规则和 Codex；域名逐项的 `source`、`model`、`reason` 来自管理器每小时分类结果，日报不会重新猜测历史分类。Codex 缺失、认证失败、调用失败或输出校验失败时，本次日报运行失败，不生成或发布规则-only 报告。
6. 从同一份已校验结果生成 JSON 和 Markdown，并原子发布。
7. 将 Markdown 和 JSON 复制到仓库内 `ops-daily-reports/<year>/`，只提交该目录并推送到 GitHub。
8. 仅把本次运行元数据和报告归档索引写入 SQLite。

Prometheus 查询失败、标签冲突或覆盖不足时仍应生成明确标注缺口的规则报告；无法校验规则结果或无法原子发布时，本次运行失败且不发布半份报告。
`--rules-only` 或 `OPS_FORCE_RULES_ONLY=1` 仅用于明确的影子/维护运行，不是 Codex 失败时的自动降级路径。

官方登录 token 配套的 Codex 配置使用 `model_provider = "openai"`。日报器执行时仍忽略任意用户配置以保持隔离，但在未配置自定义 provider 时会显式传入这个内置 provider，避免把官方 token 发到错误的默认 provider；运行时 `auth.json` 和 `config.toml` 均应由宿主机以只读方式提供。

## 数据面流量

每个节点段落都会展示普通数据面和 AI 数据面的日总流量、入站流量、出站流量、网络流量覆盖率和计入接口列表。流量来源为 Prometheus 中 `job="data-plane-node"` 的 `node_network_receive_bytes_total` 与 `node_network_transmit_bytes_total`，按 `node_role` 分别汇总。

日报只计入数据面主机上的公网/物理网络接口前缀：`eth`、`ens`、`enp`、`eno`、`enx`、`bond`、`wan`。`lo`、Docker bridge、veth、Tailscale 等虚拟或管理链路不会进入日报总流量，避免把监控隧道和容器内部转发重复计数。

## AI 域名分类与流量导向

AI 域名分析使用 `ai_domain_analysis` 字段写入日报 JSON，并在 Markdown 中固定展示四部分：

- 分类汇总：AI、非 AI、未知域名数量以及各类命中次数。
- 每日新增域名：按保留的小时历史和 `first_seen` 判断；历史数据缺失时不会宣称“首次出现”。
- 每个域名的流量导向：`ai_proxy` 表示普通数据面动态规则已将流量送往选中的 AI 上游；`direct` 表示 DMIT 普通数据面直出；`mixed` 表示报告窗口内发生过两种导向；证据不足时为 `unknown`。
- Codex 分类：展示管理器已记录的 Codex 域名、分类理由、模型和待分类/不可用状态。日报的运维 Codex 也会接收有界的汇总和域名上下文，用于解释总体情况。

小时域名历史是请求命中次数和路由状态的来源，`panel.db` 以只读方式补充 AI 域名的累计分类、来源和每小时观察。日报不会读取原始访问日志，也不会把当前出口倒推成没有历史记录的过去出口。

数据流图：[AI 域名日报汇总](diagrams/ai-domain-daily-report.svg) · [PlantUML 源文件](diagrams/ai-domain-daily-report.puml)

## 脱敏归因

`xray-ops-attribution-sampler` 可选采集 Xray `/debug/vars` 中的 `stats.user.*` 与 `stats.inbound.*` 聚合 counter。采样器在解析后立即用本地盐做 HMAC，SQLite 和日报只保存 `usr-<16 hex>`、`inb-<16 hex>`、方向和 counter 值；不保存原始用户 email/UUID、inbound tag、客户端 IP、访问域名或 URL。

日报中的 user 与 inbound 是同一批流量的两种视角，不应相加。至少需要同一报告窗口附近的两次快照才能计算增量；启用采样器前的历史流量不能回溯到用户级。

相关环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `OPS_XRAY_STATS_ENABLED` | `0` | 是否启用归因采样器。 |
| `OPS_XRAY_STATS_SAMPLE_INTERVAL_SECONDS` | `300` | 脱敏 counter 快照采样间隔。 |
| `OPS_XRAY_STATS_REDACTION_SALT_FILE` | `/data/xray-ops/xray-stats-redaction-salt` | 本地 HMAC 盐文件；不进入 GitHub 归档。 |
| `OPS_XRAY_STATS_AI_ENABLED` | `0` | 是否启用 AI 数据面 Xray stats 源。 |
| `OPS_XRAY_STATS_AI_SSH_TARGET` | 空 | AI 节点 SSH 管理端点。 |
| `OPS_XRAY_STATS_NORMAL_KNOWN_HOSTS_HOST_PATH` | `/dev/null` | 普通数据面 SSH 的宿主机 known_hosts 挂载源；启用普通 SSH stats 时必须提供已核验文件。 |
| `OPS_XRAY_STATS_AI_KNOWN_HOSTS_HOST_PATH` | `/dev/null` | 宿主机 known_hosts 挂载源。 |
| `OPS_XRAY_STATS_AI_CONTAINER` | `xray` | 远端 Xray 容器名。 |
| `OPS_XRAY_STATS_AI_METRICS_PORT` | `31097` | 容器内 `/debug/vars` 端口。 |
| `OPS_XRAY_STATS_WINDOW_PADDING_SECONDS` | `900` | 日报计算归因时读取窗口前后的采样缓冲。 |
| `OPS_PANEL_DB_HOST_DIR` | 部署时必填；本地默认为 `./data` | 宿主机 `panel.db` 所在目录，以只读方式挂载到日报容器。 |
| `OPS_AI_PANEL_DB_PATH` | `/panel-data/panel.db` | 只读的 `panel.db` 路径，用于补充 AI 域名历史和分类来源。 |

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
| `OPS_GITHUB_REPORTS_AUTHOR_EMAIL` | `redacted-email-001 [at] example.invalid` | 自动提交邮箱。 |
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

日报器不能从指标反推出原始日志内容或请求级根因。AI 域名区块只保存域名级聚合命中次数、分类结果和已记录的路由状态，不保存 URL、请求内容或客户端身份；它不能证明单个请求在每一秒的真实出口。归因表只说明哪个脱敏 user/inbound counter 增长最多，不说明具体访问内容。模型解释不能覆盖规则结论；正常模式下 Codex 失败会使整次日报失败，不会自动降级为 `rules_only`。

相关文档：[Prometheus Targets](prometheus-targets.md)、[规则边界](fault-classification.md)、[SQLite 审计](report-run-audit.md)。
