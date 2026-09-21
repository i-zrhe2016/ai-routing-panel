# Repository Current State

Last verified: 2026-09-21 @ d2f5086

## Current Focus

- None.

## Implemented

- 台湾 AI 节点是当前唯一配置的 AI 主候选；原不可用主节点已从节点清单、监控目标、路由候选和运行中的控制面容器环境中移除。
- `AI_UPSTREAM_FALLBACK_AS_PRIMARY=1` 可将带独立凭据的 fallback 分享链接提升为候选 0；单候选可作为主节点，过期的 `backup` 状态会归一化，人工固定备用仍要求至少两个候选。
- AI 管理器已拆分到 `app/xray/ai_routing/`，节点控制统一使用 `app/xray/node/` 的 canonical backend；控制面由 `app/bootstrap.py` 组装 Application。
- 远端 AI 节点的指标和访问日志通过受管 SSH 通道读取；本地节点继续使用本地 endpoint/file 路径。远端日志读取具备有界读取、超长记录丢弃和跨轮次续传状态。控制面运行镜像已于 2026-09-21 从当前 `main`（`d2f5086`）重建部署，`xray_panel_ai_node_metrics_available`、`xray_panel_ai_destination_log_available` 和 `xray_panel_ai_node_running` 均为 1。
- 运维日报器支持第三方 OpenAI 兼容 provider：`OPS_CODEX_MODEL_PROVIDER`、`OPS_CODEX_PROVIDER_BASE_URL`、`OPS_CODEX_PROVIDER_WIRE_API`、`OPS_CODEX_MODEL_REASONING_SUMMARY`、`OPS_CODEX_OUTPUT_SCHEMA` 通过 `-c` 显式覆盖 Codex 配置，仍不读取任何用户 `config.toml`。拒绝约束输出的 provider 可设 `OPS_CODEX_OUTPUT_SCHEMA=0`，此时改为把同一份 JSON Schema 文档写进提示词。2026-09-20 实测 2026-09-19 日报以 `generation_mode=codex` 成功生成。
- 控制面 Loki 与 Fluent Bit Agent 组成集中日志链路，控制面和普通数据面 Agent 通过 Tailscale 推送；Grafana 通过 `GRAFANA_LOKI_URL` 查询。
- 灾备 SSH 采集已启用，覆盖普通数据面和 AI 节点；AI 节点采集其 Xray 配置和 `.env`。2026-09-20 手工执行 `run_db_backup_cycle.py`（关闭 R2）验证两个角色均为 `ok`、`recoveryReady=true`。
- Prometheus 目标保留控制面、普通数据面和台湾 AI 节点的当前拓扑，旧 AI 主节点目标已移除。
- `scripts/restore_backup.py` 可校验明文/AES-256-GCM 灾备包，并把面板数据库、可选运维数据库、用户附件、控制面文件和普通/AI 节点文件准备到隔离恢复树；默认不写 SSH、Docker 或线上服务。
- `scripts/configure_backup_secrets.py` 提供中文交互配置和 `--check`，只管理灾备加密密码及可选 R2 字段；输入不回显，生成值不打印，目标 dotenv 文件原子更新并保持 `0600`。使用边界见 [灾备上传](db-backup-uploader.md)。
- 仓库具备首个 CI 门禁：`.github/workflows/ci.yml` 在**指向 `main` 的 PR**和**推送到 `main`**时运行 `backend`（Python 3.12 跑 `python -m pytest`）和 `frontend`（Node 22 跑 `npm test`、`npm run build`，再阻塞比对产物与 `app/static/admin`）两个 job，均只申请 `contents: read`。检查清单见 [开发流程](development.md)。
- 管理后台已重构为控制中心，暴露 Overview、AI Routing、Traffic、Resources、Orders & Plans、Infrastructure、Observability 七个一级工作区，共用 `frontend/src/shared/control-center.css`；AI Routing 与 Traffic 只渲染 `/api/dashboard` 已返回的数据。Admin 源码改动必须与重建的 `app/static/admin` 产物一起提交，构建与比对命令见 [开发流程](development.md)。
- 面板控制台只允许内网和 Tailscale 来源访问：`PANEL_ALLOWED_NETWORKS`（CIDR 列表，默认回环、RFC1918、链路本地、`100.64.0.0/10`、`fc00::/7`、`fe80::/10`）在路由前按来源地址放行，其他来源一律 `403`（`/api/**` 返回 `{"ok":false,"code":"forbidden_source"}`）并记录 `panel.access.denied`；宿主机 `ai_routing_panel_firewall` 表使用同一组网段做 L3/L4 兜底。管理员登录已整体移除（`PANEL_USERNAME`、`PANEL_PASSWORD`、`PANEL_INTERNAL_HOSTS`、`AUTH_ENABLED`、Basic Auth、Cloudflare Access 邮箱旁路、`/logout` 和后台登出按钮），CSRF 仍对每个调用方强制校验；租户与客户登录不变，访问说明见 [面板访问](panel-access.md)。

## In Progress

- `fix/backup-tailscale-ssh-completeness`（Plan #44）尚未合并：该分支包含 `DB_BACKUP_AI_NODE_SSH_TARGETS` 列表支持和 broker 加固，工作副本位于该分支上。

## Known Issues / Failing Checks

- 全量 `PYTHONPATH=. .venv/bin/pytest -q`：384 passed、1 skipped；跳过项需要 `XRAY_TEST_BINARY` 和 HAProxy 才能执行真实传输测试。
- 手工采集周期使用 `DB_BACKUP_R2_ENABLED=0`，因此本次未验证 R2 上传链路；定时 `03:00 UTC` 任务在本次会话中未被观察。
- 日报归档已启用推送：`OPS_GITHUB_REPORTS_PUSH_ENABLED=1` 且通过 `OPS_GITHUB_REPORTS_TOKEN_HOST_PATH` 只读挂载 token；2026-09-20 实测调度周期把归档提交推到 `origin/main`，日志为 `push_status=pushed`、`ahead_after=0`。
- 归档日期存在缺口：`ops-daily-reports/` 在 `origin/main` 上从 2026-09-08 直接跳到 2026-09-19；2026-09-09 的报告只提交在本地 `main` 分支且未推送，2026-09-10 至 2026-09-18 因日报器故障未生成。本仓库不计划回补。
- AI 节点自建的控制面栈（`prometheus` 重启循环、`xray-routing-panel` `/healthz` 非 200）是遗留部署；本仓库当前只对其做灾备采集，不接管其运行时。
- `frontend/src/portal` 与 `frontend/src/landing` 的源码改动没有构建路径：本仓库不生成 `app/static/{portal,landing}` 产物（见 [开发流程](development.md)），所以 2026-09-21 合并的这两处改版不会进入运行中的服务，已提交的 `app/static/{portal,landing}` 仍是改版前版本。

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
- 归档检出必须是独立仓库：`git worktree` 的 `.git` 是指向主仓库 `.git/worktrees/<name>` 的文件，容器内不可用，会报 `fatal: not a git repository`。
- 归档检出与 upstream 的同步规则（仅适用于配置了 tracking upstream 的检出；没有 upstream 时发布器跳过 fetch 和落后检查，推送时直接 `git push -u`）：启用推送时先 fetch，且只在检出落后时处理同步——工作树干净则以 `--ff-only` 自动快进，工作树有未提交改动则以 `github_reports_branch_behind_upstream` 报错，同时领先（分叉）导致无法快进则以 `fatal: Not possible to fast-forward` 报错，需要先人工理顺该检出；未落后时不做这些检查。关闭推送时不做 fetch，落后直接以 `github_reports_branch_behind_upstream` 拒绝提交。

## Architecture Snapshot

- `app/panel.py` / `app/bootstrap.py` 创建 Application；`app/state/` 管理领域状态，`app/xray/node/` 负责 local、Docker、SSH 和 unmanaged backend，`app/web/` 仅消费已注入的 Application。
- AI 路由数据流和操作约束见 [AI 路由](ai-routing.md)；远端节点配置和 SSH 纳管见 [AI 节点部署](ai-node-deployment.md)；监控拓扑见 `monitoring/prometheus/prometheus.yml`。
- AI 节点候选配置、探测、报告和面板状态必须保持同一有效候选集合；应用配置失败时保留待应用状态并等待下一轮重试。
- 灾备包由 `scripts/build_backup_bundle.py` 生成，`scripts/node_recovery.py` 提供恢复契约，`scripts/restore_backup.py` 负责验证后隔离准备；详细流程见 [节点恢复](node-recovery.md)。

## Next

- 新增需求先记录 GitHub Issue Plan，再创建 Plan 分支。
