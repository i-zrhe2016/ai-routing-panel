# Repository Current State

Last verified: 2026-10-03 @ working tree

## Current Focus

- None。

## Implemented

- React 管理后台保留总览、主机、流量、流量拓扑、AI 路由、交付六个工作区；独立故障排查、商业管理与监控入口已移除，后端体检和客户业务保留。Admin 源码与产物同步维护，见 [开发流程](development.md)。
- 主机工作区展示中文 Codex 故障报告与生命周期；默认连续五次失败才排队，成功清空计数，持续失败不重复分析。SQLite 持久化计数与认领，异步分析在隔离容器中只读运行，不更改生产节点，见 [运维](operations.md#codex-自动故障记录)。
- 独立 SSH 执行主机以实际凭据完成普通与 AI 目标的 VLESS + REALITY 认证 HTTPS 请求；执行器错误与节点失败分开，执行器错误保留既有路由和健康状态。
- 域名分类持久化到 SQLite 与原子缓存；默认 OpenRouter GPT-5 Nano 仅分类未知域名，失败保留历史决策与待分类域名。独立健康周期复用分类完成回退和恢复，不等待小时分类，见 [AI 路由](ai-routing.md) 和 [配置](configuration.md#域名分类器)。
- 拓扑并列展示普通/未分类直出与 AI 路径；备用出口以实际渲染的直出或独立中继目标为准。应用证据、业务探测、DNS 目标与实测入口速率分别呈现，未知结果不虚构生效路径或边流量。
- 新租户可自动分配端口；交付提供 Clash/V2Ray 订阅、VLESS 分享与订阅地址重置。流量支持今日、近 7 天、近 30 天，展示与自然日统计使用北京时间。
- 统一入口在 Docker 启动前清理失效 Unix socket，保留活跃 socket、普通文件和符号链接，见 [统一入口部署](unified-entry.md)。
- 面板仅允许内网/Tailscale 来源，无管理员登录；来源白名单、宿主机防火墙与 CSRF 保留，租户/客户登录不变，见 [面板访问](panel-access.md)。
- 灾备通过隔离 broker/只读 SSH 采集节点材料，缺失必需材料阻止上传；恢复脚本验证归档后生成隔离恢复树，见 [节点恢复](node-recovery.md)。中文密钥配置工具仅管理灾备密码与可选 R2 字段，见 [灾备上传](db-backup-uploader.md)。
- CI 对指向 `main` 的 PR 与 `main` 推送执行 backend/frontend 门禁，包含后端测试、前端测试/构建及已提交 Admin 产物一致性检查。

## In Progress

- None。

## Known Issues / Failing Checks

- 最近完整验证跳过两项可选真实传输测试：协议认证测试需要 `PROBE_TEST_XRAY_BIN`（或默认路径的 Xray），统一入口测试需要 `XRAY_TEST_BINARY` 与 HAProxy；当前 CI 未安装这些工具。
- Portal/Landing 的 Vue 源码没有构建入口；Vite 只生成 `app/static/admin`，运行服务使用已提交的 Portal/Landing 静态产物，见 [开发流程](development.md#前端发布资源)。
- 当前生产前端是否运行上述合并版本未核验。

## Constraints

- Python >=3.10；Flask 运行依赖固定版本；SQLite 单副本部署。
- AI 节点使用独立 REALITY 凭据，不能从普通数据面凭据推导；候选配置、探测、报告与面板须使用同一有效集合，应用失败保持待应用并重试。
- 访问日志读取保持有界读取与持久化游标；`AI_NODE_ACCESS_LOG_PATH` 必须是远端宿主机实际路径。
- 灾备恢复只准备隔离树，不替换线上服务；恢复密码通过受保护文件或环境提供，密钥配置工具不管理 SSH/Xray 凭据。

## Architecture Snapshot

- `app/bootstrap.py` 组装 Application；`app/state/` 管理领域状态，`app/xray/ai_routing/` 管理 AI 路由，`app/xray/node/` 统一节点 backend，`app/web/` 消费注入的 Application。
- 路由细节见 [AI 路由](ai-routing.md)，远端节点与 SSH 纳管见 [AI 节点部署](ai-node-deployment.md)；协议执行器与故障分析见 [运维](operations.md#协议探测)。

## Next

- None。
