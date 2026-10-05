# Repository Current State

Last verified: 2026-10-04 @ working tree

## Current Focus

- [Plan 158](https://github.com/i-zrhe2016/ai-routing-panel/issues/158)：软配额限流与按端口全部 AI 转发已完成部署验收，Git 交付仍待完成。

## Implemented

- React 管理后台保留总览、主机、流量、流量拓扑、AI 路由、交付六个工作区；独立故障排查、商业管理与监控入口已移除，后端体检和客户业务保留。Admin 源码与产物同步维护，见 [开发流程](development.md)。
- 主机工作区展示中文 Codex 故障报告与生命周期；默认连续五次失败才排队，成功清空计数，持续失败不重复分析。SQLite 持久化计数与认领，异步分析在隔离容器中只读运行，不更改生产节点，见 [运维](operations.md#codex-自动故障记录)。
- 控制面已迁到 DO，普通与 AI 数据面保持独立；DO 控制面以显式 `local` 探测模式运行隔离 Xray 客户端，对两个数据面执行真实 VLESS + REALITY 认证 HTTPS 请求。执行器错误保留既有路由和健康状态，见 [控制面迁移](panel-migration.md)。
- 域名分类持久化到 SQLite 与原子缓存；默认 OpenRouter GPT-5 Nano 仅分类未知域名，失败保留历史决策与待分类域名。独立健康周期复用分类完成回退和恢复，不等待小时分类，见 [AI 路由](ai-routing.md) 和 [配置](configuration.md#域名分类器)。
- 拓扑并列展示普通/未分类直出与 AI 路径；备用出口以实际渲染的直出或独立中继目标为准。应用证据、业务探测、DNS 目标与实测入口速率分别呈现，未知结果不虚构生效路径或边流量。
- 新租户可自动分配端口；交付提供 Clash/V2Ray 订阅、VLESS 分享与订阅地址重置，Clash 含常用 AI、GitHub、Discord、WhatsApp 规则。流量展示与自然日统计使用北京时间，见 [接口](api.md)。
- 统一入口在 Docker 启动前清理失效 Unix socket，保留活跃 socket、普通文件和符号链接，见 [统一入口部署](unified-entry.md)。
- 面板仅允许内网/Tailscale 来源，无管理员登录；来源白名单、宿主机防火墙与 CSRF 保留，租户/客户登录不变，见 [面板访问](panel-access.md)。
- 灾备通过隔离 broker/只读 SSH 采集节点材料；新上传保存原始归档，历史加密归档仍需原密码恢复。中文配置工具仅管理 R2 凭据并保留历史解密密码，见 [灾备上传](db-backup-uploader.md) 和 [节点恢复](node-recovery.md)。
- DO 生产 Admin 已通过桌面和移动浏览器检查；页面正常加载，逐端口 AI 转发开关可见，无页面错误或整页横向溢出。
- CI 对指向 `main` 的 PR 与 `main` 推送执行 backend/frontend 门禁，包含后端测试、前端测试/构建及已提交 Admin 产物一致性检查。

## In Progress

- [Plan 158](https://github.com/i-zrhe2016/ai-routing-panel/issues/158) 工作树已部署：达到配置配额后保持连接并按账号分别限制上下行 5 Mbps；每个端口可独立切换全部 AI 转发，统一入口按认证账号隔离。当前端口沿用原分类分流。实现、测试和回滚证据在对应 Ticket，尚未提交或合并。

## Known Issues / Failing Checks

- 最近完整验证跳过两项可选真实传输测试：协议认证测试需要 `PROBE_TEST_XRAY_BIN`（或默认路径的 Xray），统一入口测试需要 `XRAY_TEST_BINARY` 与 HAProxy；当前 CI 未安装这些工具。
- Portal/Landing 的 Vue 源码没有构建入口；Vite 只生成 `app/static/admin`，运行服务使用已提交的 Portal/Landing 静态产物，见 [开发流程](development.md#前端发布资源)。

## Constraints

- Python >=3.10；Flask 运行依赖固定版本；SQLite 单副本部署。
- AI 节点使用独立 REALITY 凭据，不能从普通数据面凭据推导；候选配置、探测、报告与面板须使用同一有效集合，应用失败保持待应用并重试。
- 访问日志读取保持有界读取与持久化游标；`AI_NODE_ACCESS_LOG_PATH` 必须是远端宿主机实际路径。
- 灾备恢复只准备隔离树，不替换线上服务；历史解密密码通过受保护文件或环境提供，配置工具不管理 SSH/Xray 凭据。

## Architecture Snapshot

- `app/bootstrap.py` 组装 Application；`app/state/` 管理领域状态，`app/xray/ai_routing/` 管理 AI 路由，`app/xray/node/` 统一节点 backend，`app/web/` 消费注入的 Application。
- 路由细节见 [AI 路由](ai-routing.md)，远端节点与 SSH 纳管见 [AI 节点部署](ai-node-deployment.md)；协议执行器与故障分析见 [运维](operations.md#协议探测)。

## Next

- [Plan 158](https://github.com/i-zrhe2016/ai-routing-panel/issues/158) 的 Git 交付与合并后状态同步。
