# Repository Current State

Last verified: 2026-09-20 @ 63935d3

## Current Focus

- None.

## Implemented

- 台湾 AI 节点是当前唯一配置的 AI 主候选；原不可用主节点已从节点清单、监控目标、路由候选和运行中的控制面容器环境中移除。
- `AI_UPSTREAM_FALLBACK_AS_PRIMARY=1` 可将带独立凭据的 fallback 分享链接提升为候选 0；单候选可作为主节点，过期的 `backup` 状态会归一化，人工固定备用仍要求至少两个候选。
- AI 管理器已拆分到 `app/xray/ai_routing/`，节点控制统一使用 `app/xray/node/` 的 canonical backend；控制面由 `app/bootstrap.py` 组装 Application。
- 远端 AI 节点的指标和访问日志通过受管 SSH 通道读取；本地节点继续使用本地 endpoint/file 路径。远端日志读取具备有界读取、超长记录丢弃和跨轮次续传状态。控制面运行镜像已于 2026-09-20 从当前 `main` 重建部署，`xray_panel_ai_node_metrics_available`、`xray_panel_ai_destination_log_available` 和 `xray_panel_ai_node_running` 均为 1。
- 运维日报器支持第三方 OpenAI 兼容 provider：`OPS_CODEX_MODEL_PROVIDER`、`OPS_CODEX_PROVIDER_BASE_URL`、`OPS_CODEX_PROVIDER_WIRE_API`、`OPS_CODEX_MODEL_REASONING_SUMMARY`、`OPS_CODEX_OUTPUT_SCHEMA` 通过 `-c` 显式覆盖 Codex 配置，仍不读取任何用户 `config.toml`。拒绝约束输出的 provider 可设 `OPS_CODEX_OUTPUT_SCHEMA=0`，此时改为把同一份 JSON Schema 文档写进提示词。2026-09-20 实测 2026-09-19 日报以 `generation_mode=codex` 成功生成。
- 控制面 Loki 与 Fluent Bit Agent 组成集中日志链路，控制面和普通数据面 Agent 通过 Tailscale 推送；Grafana 通过 `GRAFANA_LOKI_URL` 查询。
- 灾备 SSH 采集已启用，覆盖普通数据面和 AI 节点；AI 节点采集其 Xray 配置和 `.env`。2026-09-20 手工执行 `run_db_backup_cycle.py`（关闭 R2）验证两个角色均为 `ok`、`recoveryReady=true`。
- Prometheus 目标保留控制面、普通数据面和台湾 AI 节点的当前拓扑，旧 AI 主节点目标已移除。
- `scripts/restore_backup.py` 可校验明文/AES-256-GCM 灾备包，并把面板数据库、可选运维数据库、用户附件、控制面文件和普通/AI 节点文件准备到隔离恢复树；默认不写 SSH、Docker 或线上服务。
- `scripts/configure_backup_secrets.py` 提供中文交互配置和 `--check`，只管理灾备加密密码及可选 R2 字段；输入不回显，生成值不打印，目标 dotenv 文件原子更新并保持 `0600`。使用边界见 [灾备上传](db-backup-uploader.md)。

## In Progress

- `fix/backup-tailscale-ssh-completeness`（Plan #44）尚未合并：该分支包含 `DB_BACKUP_AI_NODE_SSH_TARGETS` 列表支持和 broker 加固，工作副本位于该分支上。

## Known Issues / Failing Checks

- 全量 `PYTHONPATH=. .venv/bin/pytest -q`：375 passed、1 skipped；跳过项需要 `XRAY_TEST_BINARY` 和 HAProxy 才能执行真实传输测试。
- 手工采集周期使用 `DB_BACKUP_R2_ENABLED=0`，因此本次未验证 R2 上传链路；定时 `03:00 UTC` 任务在本次会话中未被观察。
- 日报归档已启用推送：`OPS_GITHUB_REPORTS_PUSH_ENABLED=1` 且通过 `OPS_GITHUB_REPORTS_TOKEN_HOST_PATH` 只读挂载 token；2026-09-20 实测调度周期把归档提交推到 `origin/main`，日志为 `push_status=pushed`、`ahead_after=0`。
- 归档日期存在缺口：2026-09-10 至 2026-09-18 因日报器故障未生成，本仓库不计划回补；`ops-daily-reports/` 已含 2026-09-19。
- AI 节点自建的控制面栈（`prometheus` 重启循环、`xray-routing-panel` `/healthz` 非 200）是遗留部署；本仓库当前只对其做灾备采集，不接管其运行时。

## Constraints

- Python >=3.10；Flask 运行版本保持固定；SQLite 仍按单副本部署。
- AI 节点使用独立 REALITY 凭据，默认不上传控制面生成的 AI 配置；不能从普通数据面凭据推导 AI 节点凭据。
- 远端指标 endpoint 必须绑定远端回环地址并经受管 SSH 读取；访问日志读取必须保持有限单次读取量和可持久化游标状态。
- 灾备恢复密码只能通过受保护文件或环境变量提供；恢复脚本默认只生成隔离树，不执行线上替换，节点材料不完整时不得把部分准备当作完整恢复。
- 灾备密钥配置脚本只负责 `DB_BACKUP_*` 材料，不管理 SSH、Xray/REALITY 或节点业务凭据；密钥不能通过命令行参数、日志或聊天传递。
- 远端发布前必须明确不可变目标、并发锁/隔离、健康门禁和恢复路径；当前不把 SSH 可达性视为发布授权。
- AI 节点的访问日志位于远端宿主机路径，不是容器内路径；`AI_NODE_ACCESS_LOG_PATH` 必须指向宿主机上的实际文件。
- 第三方 provider 不支持 `wire_api=chat`，Codex CLI 会拒绝启动；`model_reasoning_summary` 只接受 `auto`、`concise`、`detailed`、`none`。
- 固定版本的 Codex CLI 总会发送 `update_plan`、`view_image`、`request_user_input` 等工具且无法全部关闭，因此拒绝“约束输出 + 工具”组合的 provider 必须关闭 `OPS_CODEX_OUTPUT_SCHEMA`；此时契约只能靠提示词中的 schema 文档加本地校验保证。
- 归档检出必须是独立仓库：`git worktree` 的 `.git` 是指向主仓库 `.git/worktrees/<name>` 的文件，容器内不可用，会报 `fatal: not a git repository`。归档检出所在分支不能落后于其 upstream，否则发布器以 `github_reports_branch_behind_upstream` 拒绝提交；与 upstream 分叉时上游同步使用 `--ff-only`，会以 `fatal: Not possible to fast-forward` 失败，需要先人工把该检出理顺到 upstream。

## Architecture Snapshot

- `app/panel.py` / `app/bootstrap.py` 创建 Application；`app/state/` 管理领域状态，`app/xray/node/` 负责 local、Docker、SSH 和 unmanaged backend，`app/web/` 仅消费已注入的 Application。
- AI 路由数据流和操作约束见 [AI 路由](ai-routing.md)；远端节点配置和 SSH 纳管见 [AI 节点部署](ai-node-deployment.md)；监控拓扑见 `monitoring/prometheus/prometheus.yml`。
- AI 节点候选配置、探测、报告和面板状态必须保持同一有效候选集合；应用配置失败时保留待应用状态并等待下一轮重试。
- 灾备包由 `scripts/build_backup_bundle.py` 生成，`scripts/node_recovery.py` 提供恢复契约，`scripts/restore_backup.py` 负责验证后隔离准备；详细流程见 [节点恢复](node-recovery.md)。

## Next

- 新增需求先记录 GitHub Issue Plan，再创建 Plan 分支。
