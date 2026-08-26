# 每日日报器

> 权威范围：Prometheus 查询、规则执行和报告发布

日报器每天按 `Asia/Shanghai` 自然日查询 Prometheus HTTP API。它不访问 SSH，不读取原始日志，不部署 exporter，也不执行修复。

截至 2026-08-05，生产日报器以 `OPS_FORCE_RULES_ONLY=1` 影子模式运行。首份 2026-08-04 报告因窗口早于 Prometheus 上线而为 `unknown`，仅用于验证缺口处理、审计和原子发布，不作为业务状态结论。

## 生成流程

1. 验证 Prometheus 可用性、必需 labels 和 target 唯一性。
2. 对前一自然日执行版本化 range queries，计算覆盖率与 counter reset。
3. 将标准化指标交给确定性规则；缺失数据保持 `unknown`。
4. 从同一份已校验结果生成 JSON 和 Markdown，并原子发布。
5. 将 Markdown 和 JSON 复制到仓库内 `ops-daily-reports/<year>/`，只提交该目录并推送到 GitHub。
6. 仅把本次运行元数据和报告归档索引写入 SQLite。

Prometheus 查询失败、标签冲突或覆盖不足时仍应生成明确标注缺口的规则报告；无法校验规则结果或无法原子发布时，本次运行失败且不发布半份报告。

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

日报器不能从指标反推出日志内容或请求级根因。模型解释如被启用，只接收脱敏后的规则摘要，超时后降级为 `rules_only`，且不能覆盖规则结论。

相关文档：[Prometheus Targets](prometheus-targets.md)、[规则边界](fault-classification.md)、[SQLite 审计](report-run-audit.md)。
