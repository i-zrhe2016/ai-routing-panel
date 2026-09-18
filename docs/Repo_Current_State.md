# Repository Current State

Last verified: 2026-09-18 @ fd4320e

## Current Focus

- None.

## Implemented

- 台湾 AI 节点是当前唯一配置的 AI 主候选；原不可用主节点已从节点清单、监控目标和路由候选中移除。
- `AI_UPSTREAM_FALLBACK_AS_PRIMARY=1` 可将带独立凭据的 fallback 分享链接提升为候选 0；单候选可作为主节点，过期的 `backup` 状态会归一化，人工固定备用仍要求至少两个候选。
- AI 管理器已拆分到 `app/xray/ai_routing/`，节点控制统一使用 `app/xray/node/` 的 canonical backend；控制面由 `app/bootstrap.py` 组装 Application。
- 远端 AI 节点的指标和访问日志通过受管 SSH 通道读取；本地节点继续使用本地 endpoint/file 路径。远端日志读取具备有界读取、超长记录丢弃和跨轮次续传状态。
- Prometheus 目标保留控制面、普通数据面和台湾 AI 节点的当前拓扑，旧 AI 主节点目标已移除。
- `scripts/restore_backup.py` 可校验明文/AES-256-GCM 灾备包，并把面板数据库、可选运维数据库、用户附件、控制面文件和普通/AI 节点文件准备到隔离恢复树；默认不写 SSH、Docker 或线上服务。

## In Progress

- None.

## Known Issues / Failing Checks

- 全量 `PYTHONPATH=. .venv/bin/pytest -q`：328 passed、1 skipped；跳过项需要 `XRAY_TEST_BINARY` 和 HAProxy 才能执行真实传输测试。
- 当前备份容器虽启用 R2 开关，但加密密码和 R2 连接配置为空；已观测到 9 次 `backup.failed`，没有 R2 上传记录。
- 最新本地灾备包完整性和共享数据库校验通过，但 `recoveryReady=false`：已配置的 AI 数据面材料缺少必需的 Xray 配置；完整恢复会拒绝，部分准备必须显式使用 `--allow-incomplete`。
- 尚未执行远端运行时重启或重新部署；远端运行中的容器可能仍使用旧环境，待具备安全的部署目标、锁/隔离和回滚契约后再做 live rollout。

## Constraints

- Python >=3.10；Flask 运行版本保持固定；SQLite 仍按单副本部署。
- AI 节点使用独立 REALITY 凭据，默认不上传控制面生成的 AI 配置；不能从普通数据面凭据推导 AI 节点凭据。
- 远端指标 endpoint 必须绑定远端回环地址并经受管 SSH 读取；访问日志读取必须保持有限单次读取量和可持久化游标状态。
- 灾备恢复密码只能通过受保护文件或环境变量提供；恢复脚本默认只生成隔离树，不执行线上替换，节点材料不完整时不得把部分准备当作完整恢复。
- 远端发布前必须明确不可变目标、并发锁/隔离、健康门禁和恢复路径；当前不把 SSH 可达性视为发布授权。

## Architecture Snapshot

- `app/panel.py` / `app/bootstrap.py` 创建 Application；`app/state/` 管理领域状态，`app/xray/node/` 负责 local、Docker、SSH 和 unmanaged backend，`app/web/` 仅消费已注入的 Application。
- AI 路由数据流和操作约束见 [AI 路由](ai-routing.md)；远端节点配置和 SSH 纳管见 [AI 节点部署](ai-node-deployment.md)；监控拓扑见 `monitoring/prometheus/prometheus.yml`。
- AI 节点候选配置、探测、报告和面板状态必须保持同一有效候选集合；应用配置失败时保留待应用状态并等待下一轮重试。
- 灾备包由 `scripts/build_backup_bundle.py` 生成，`scripts/node_recovery.py` 提供恢复契约，`scripts/restore_backup.py` 负责验证后隔离准备；详细流程见 [节点恢复](node-recovery.md)。

## Next

- 在安全部署契约明确后，执行远端配置发布/重载和 live health check；在此之前保持远端运行时状态为 Unverified。
