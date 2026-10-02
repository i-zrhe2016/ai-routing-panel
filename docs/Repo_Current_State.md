# Repository Current State

Last verified: 2026-10-02 @ 65f33d6

## Current Focus

- [管理后台改版](https://github.com/i-zrhe2016/ai-routing-panel/issues/118)。

## Implemented

- 本机及可达 AI 节点的监控、集中日志采集、运维日报服务与专用数据已删除；旧 Loki 接收节点离线，其数据未核验。备份恢复跳过旧运维数据库及部署配置。
- 台湾 AI 节点是当前唯一配置的 AI 主候选；原不可用主节点已从节点清单、路由候选和运行中的控制面容器环境中移除。
- `AI_UPSTREAM_FALLBACK_AS_PRIMARY=1` 可将带独立凭据的 fallback 分享链接提升为候选 0；单候选可作为主节点，过期的 `backup` 状态会归一化，人工固定备用仍要求至少两个候选。
- AI 管理器已拆分到 `app/xray/ai_routing/`，节点控制统一使用 `app/xray/node/` 的 canonical backend；控制面由 `app/bootstrap.py` 组装 Application。
- AI 节点的访问日志保留受管 SSH 有界读取和持久化游标；Prometheus 指标接口及 Xray expvar 监听已退役。
- 灾备 Compose 由隔离 broker 通过只读宿主机挂载采集同机普通数据面文件，通过 Tailscale SSH 采集远端 AI 节点配置；必需恢复材料不完整时会阻止 R2 上传。
- `scripts/restore_backup.py` 可校验明文/AES-256-GCM 灾备包，并把面板数据库、用户附件、控制面文件和普通/AI 节点文件准备到隔离恢复树；默认不写 SSH、Docker 或线上服务。
- `scripts/configure_backup_secrets.py` 提供中文交互配置和 `--check`，只管理灾备加密密码及可选 R2 字段；输入不回显，生成值不打印，目标 dotenv 文件原子更新并保持 `0600`。使用边界见 [灾备上传](db-backup-uploader.md)。
- 仓库具备首个 CI 门禁：`.github/workflows/ci.yml` 在**指向 `main` 的 PR**和**推送到 `main`**时运行 `backend`（Python 3.12 跑 `python -m pytest`）和 `frontend`（Node 22 跑 `npm test`、`npm run build`，再阻塞比对产物与 `app/static/admin`）两个 job，均只申请 `contents: read`。检查清单见 [开发流程](development.md)。
- 管理后台使用 React 控制中心，主机、流量和诊断图表消费面板业务接口；可观测性工作区及 Grafana 嵌入已移除。Admin 源码与构建产物一起维护，见 [开发流程](development.md)。
- 面板控制台只允许内网和 Tailscale 来源访问：`PANEL_ALLOWED_NETWORKS`（CIDR 列表，默认回环、RFC1918、链路本地、Tailscale IPv4 range、`fc00::/7`、`fe80::/10`）在路由前按来源地址放行，其他来源一律 `403`（`/api/**` 返回 `{"ok":false,"code":"forbidden_source"}`）并记录 `panel.access.denied`；宿主机 `ai_routing_panel_firewall` 表使用同一组网段做 L3/L4 兜底。管理员登录已整体移除（`PANEL_USERNAME`、`PANEL_PASSWORD`、`PANEL_INTERNAL_HOSTS`、`AUTH_ENABLED`、Basic Auth、Cloudflare Access 邮箱旁路、`/logout` 和后台登出按钮），CSRF 仍对每个调用方强制校验；租户与客户登录不变，访问说明见 [面板访问](panel-access.md)。

## In Progress

- 管理后台改版及流量拓扑的工作区尚未合并，见 [Plan](https://github.com/i-zrhe2016/ai-routing-panel/issues/118)。
- OpenRouter 分类器工作区尚未合并；原日报需求已被可观测性退役替代，见 [Plan](https://github.com/i-zrhe2016/ai-routing-panel/issues/122)。

## Known Issues / Failing Checks

- 真实远端传输集成测试需要 `XRAY_TEST_BINARY` 和 HAProxy；当前 CI 未安装这些依赖，因此该测试仍跳过。
- AI 节点的旧面板已有 unhealthy 状态；其可观测性容器和专用数据卷已删除。
- `frontend/src/portal` 与 `frontend/src/landing` 的源码改动没有构建路径：`frontend/vite.config.js` 只把 `src/admin/main.jsx` 作为输入、输出到 `app/static/admin`，本仓库不生成 `app/static/{portal,landing}` 产物（见 [开发流程](development.md)），所以这两处的 Vue 源码改版不会进入运行中的服务，已提交的 `app/static/{portal,landing}` 是改版前版本。

## Constraints

- Python >=3.10；Flask 运行版本保持固定；SQLite 仍按单副本部署。
- AI 节点使用独立 REALITY 凭据，默认不上传控制面生成的 AI 配置；不能从普通数据面凭据推导 AI 节点凭据。
- 访问日志读取必须保持有限单次读取量和可持久化游标状态。
- 灾备恢复密码只能通过受保护文件或环境变量提供；恢复脚本默认只生成隔离树，不执行线上替换，节点材料不完整时不得把部分准备当作完整恢复。
- 灾备密钥配置脚本只负责 `DB_BACKUP_*` 材料，不管理 SSH、Xray/REALITY 或节点业务凭据；密钥不能通过命令行参数、日志或聊天传递。
- 远端发布前必须明确不可变目标、并发锁/隔离、健康门禁和恢复路径；当前不把 SSH 可达性视为发布授权。
- AI 节点的访问日志位于远端宿主机路径，不是容器内路径；`AI_NODE_ACCESS_LOG_PATH` 必须指向宿主机上的实际文件。

## Architecture Snapshot

- `app/panel.py` / `app/bootstrap.py` 创建 Application；`app/state/` 管理领域状态，`app/xray/node/` 负责 local、Docker、SSH 和 unmanaged backend，`app/web/` 仅消费已注入的 Application。
- AI 路由数据流和操作约束见 [AI 路由](ai-routing.md)；远端节点配置和 SSH 纳管见 [AI 节点部署](ai-node-deployment.md)。
- AI 节点候选配置、探测、报告和面板状态必须保持同一有效候选集合；应用配置失败时保留待应用状态并等待下一轮重试。
- 灾备包由 `scripts/build_backup_bundle.py` 生成，`scripts/node_recovery.py` 提供恢复契约，`scripts/restore_backup.py` 负责验证后隔离准备；详细流程见 [节点恢复](node-recovery.md)。

## Next

- 继续核对 [管理后台改版](https://github.com/i-zrhe2016/ai-routing-panel/issues/118) 与 [OpenRouter 分类](https://github.com/i-zrhe2016/ai-routing-panel/issues/122) 的剩余交付范围。
