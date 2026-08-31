# AI 节点部署与纳管

## 模块职责

AI 节点运行独立的 VLESS + REALITY Xray，接收主数据面转发的 AI 域名流量并通过 `freedom` 直出。本文件说明本机 Docker 备用节点，以及显式启用远端 SSH 节点时的边界。

凭据匹配规则见 [AI 节点独立凭据](ai-node-credentials.md)，ChatGPT 故障处理见 [ChatGPT 路由排障](chatgpt-routing-troubleshooting.md)。

## 当前拓扑

```text
控制面 `100.87.76.6`
  ├─ Docker: xray-ai-node ──▶ 0.0.0.0:27166
  │                         ├─▶ 127.0.0.1:31097/debug/vars
  │                         └─▶ /var/log/xray/ai-{access,error}.log
  └─ 本机状态检查/重启

主数据面
  └─ VLESS + REALITY
       │
       ▼
  AI 上游选择器
    ├─ 主：nat.qq.pw:27166
    └─ 备：100.87.76.6:27166
             │
             ▼
       freedom 直出
```

两个端点职责不同：

| 端点 | 用途 |
| --- | --- |
| `100.87.76.6:27166` | 控制面本机 Docker AI 备用节点，使用独立 REALITY 凭据 |
| `nat.qq.pw:27166` | AI 业务流量，供主数据面 VLESS outbound 使用 |

禁止使用已下线的旧 AI 上游 `isif.217777.xyz:42994`。

## AI 上游选择

主数据面不会同时把 AI 流量发往两个节点，而是由控制面生成的 `ai_proxy` 动态路由选择一个候选：

- `auto`：按主、备顺序探测，选择第一个可达候选；
- `primary`：人工固定主 AI，主节点不可达时报告 `manual_target_unreachable`，不静默改用备用；
- `backup`：人工固定本机 Docker 备用 AI，备用不可达时同样停用动态 AI 路由；
- `forced_fallback`：人工停用动态 AI 路由，AI 域名回到普通数据面 `freedom` 直出。

控制台会展示两个候选的探测状态、当前选中节点和 `manual_mode`。人工切换写入控制面 `app_state`，并立即触发一轮管理器重算。

## 当前本机部署

| 项目 | 当前值 |
| --- | --- |
| 部署方式 | Docker |
| 容器名 | `xray-ai-node` |
| 配置源 | `app/xray/runtime/config-ai-node.json` |
| 容器内配置路径 | `/etc/xray/config.json` |
| 业务监听端口 | `27166` |

AI 节点使用 `AI_NODE_*` 独立 UUID、REALITY 私钥、公钥和 Short ID，不能复用普通数据面的 `XRAY_*` 凭据。

## AI 节点监控采集

本机 AI 容器与控制面共享主机监控；业务端口 `27166` 不承担监控流量。Xray
额外开启仅回环可访问的 expvar 指标端点，面板只提取有界的入站/直出字节计数，
再由受保护的 `/metrics` 暴露给 Prometheus。`ai-access.log` 保留在本机用于域名/端口
分析，不进入 Loki；`ai-error.log` 可由控制面 Fluent Bit 采集。

| 端点 | 服务 | 指标路径 | 采集目标 | 说明 |
| --- | --- | --- | --- | --- |
| Xray 指标 | AI Xray | `/debug/vars` | `127.0.0.1:31097` | 入站与 `direct` 出站累计字节，仅绑定回环 |
| 控制面监控端点 | Node Exporter/cAdvisor | `/metrics` | `control-plane` | 控制面主机与 `xray-ai-node` 容器网络指标 |
| 面板指标 | Routing Panel | `/metrics` | Prometheus | 汇总 AI Xray 字节指标，需 Bearer token |

当前 AI 节点容器部署为：

```text
xray-ai-node        → host network → :27166（业务端口，不得修改）
                    → 127.0.0.1:31097（Xray metrics，不得公网开放）
                    → app/xray/logs/ai-access.log、ai-error.log
```

部署或变更监控容器时，不得删除或重建 `xray-ai-node` 业务容器。

从控制面验证本机 AI 容器：

```bash
docker inspect xray-ai-node --format '{{.State.Running}}|{{.State.Status}}|{{.State.StartedAt}}'
docker exec xray-ai-node /usr/local/bin/xray run -test -config /etc/xray/config.json
curl -fsS http://127.0.0.1:31097/debug/vars >/dev/null
stat /root/ai-routing-panel/app/xray/logs/ai-access.log /root/ai-routing-panel/app/xray/logs/ai-error.log
curl -fsS http://127.0.0.1:9090/api/v1/targets
```

面板 Prometheus 指标中，`xray_panel_ai_node_metrics_available=1` 表示 Xray
指标端点可读；`xray_panel_ai_node_traffic_bytes_total` 是 AI 入站方向累计字节，
`xray_panel_ai_node_egress_bytes_total` 是 AI 节点 `direct` 出站方向累计字节。
Xray 重启会使其 counter 从零开始，查询应使用 Prometheus 的 `rate()` 或 `increase()`。

控制面 cAdvisor 使用 `127.0.0.1:18081`，Prometheus target 为
`control-plane-cadvisor`；它提供 `xray-ai-node` 容器的 CPU、内存和网络总量，
不替代 Xray 按入站方向的业务计数。

本机 Docker 模式只要求控制面共享的 Node Exporter/cAdvisor targets 为 `up`；远端 AI 模式才需要额外的两个 AI targets。Grafana 的 AI 主机面板按 `node_role="ai_data_plane"` 区分 AI 节点，容器面板按 `host="ai-data-plane"` 和 `name` 区分容器。生产环境应在远端 AI 节点防火墙中仅允许控制面访问 `27168/27169`，不要将监控端口用于公网开放服务。

## SSH 认证边界

本机 AI 备用不需要 SSH。专用 Ed25519 私钥只用于普通数据面和其他仍显式配置的远端节点：

```text
宿主机 0600 私钥
  └─ 只读挂载 → /run/secrets/fleet_ssh_key
                    │
                    └─ SSH 纳管普通数据面
```

远端 SSH 纳管时强制：

```text
PreferredAuthentications=publickey
PasswordAuthentication=no
KbdInteractiveAuthentication=no
StrictHostKeyChecking=yes
UserKnownHostsFile=/root/.ssh/known_hosts
```

不要使用密码认证或 `StrictHostKeyChecking=no`。密钥轮换和恢复流程见 [SSH 密钥登录与轮换](ssh-key-access.md)。

## 根 `.env` 配置

示例不包含密码：

```env
AI_NODE_SSH_TARGET=
AI_NODE_CONTAINER_NAME=xray-ai-node
AI_NODE_PROBE_HOST=100.87.76.6
AI_NODE_API_SERVER=127.0.0.1:27166
AI_NODE_METRICS_URL=http://127.0.0.1:31097/debug/vars
AI_NODE_CONFIG_PATH=
```

关键语义：

- `AI_NODE_API_SERVER=127.0.0.1:27166`：本机 Docker 模式使用的 TCP 业务端口，用于节点存活检查。
- `AI_NODE_METRICS_URL`：面板读取 AI Xray expvar 的地址；默认只允许控制面回环地址，不得改成公网监听。
- `AI_NODE_CONFIG_PATH=`：显式留空会使 `supports_sync=false`，禁止控制面上传配置。
- `AI_NODE_CONTAINER_NAME=xray-ai-node` 提供本机容器状态检查和重启能力。

## `app/xray/.env` 上游配置

```env
AI_UPSTREAM_HOST=nat.qq.pw
AI_UPSTREAM_PORT=27166
# 备用候选使用 AI_UPSTREAM_FALLBACK_URL 或 AI_UPSTREAM_FALLBACKS 配置
```

`AI_UPSTREAM_HOST` / `AI_UPSTREAM_PORT` 定义主候选；生产备用候选为 `100.87.76.6:27166`，应通过 `AI_UPSTREAM_FALLBACK_URL` 或 `AI_UPSTREAM_FALLBACKS` 加入候选列表。端点变量不能替代 AI 节点独立的 UUID、REALITY 密钥、Short ID 和 SNI。

## 为什么默认禁用配置上传

AI 节点当前使用独立 REALITY 凭据。控制面生成的 `config-ai-node.json` 若复用主数据面 `XRAY_*` 凭据，会破坏主数据面现有 VLESS outbound 与 AI inbound 的认证匹配。

因此生产默认保持：

```env
AI_NODE_CONFIG_PATH=
```

本机 AI 容器直接挂载控制面生成的 `config-ai-node.json`；`AI_NODE_CONFIG_PATH` 留空可避免面板把配置误当作远程路径上传。

## 受控配置同步流程

启用同步前必须完成以下步骤：

1. 确认 `app/xray/.env` 中存在独立 `AI_NODE_*` 凭据。
2. 使用同版本 Xray 执行 `run -test`。
3. 重启 `xray-ai-node` 容器。
4. 验证容器运行、`27166` 可达以及 ChatGPT 实际请求成功。

任一步失败都应恢复备份并重启容器。

## 日常检查

```bash
# 控制面健康状态
curl -fsS http://127.0.0.1:18080/healthz

# AI 备用容器状态
docker inspect xray-ai-node --format '{{.State.Running}}|{{.State.Status}}|{{.State.StartedAt}}'

# 业务端口
nc -zv 100.87.76.6 27166
```

预期 `/healthz`：

```json
{
  "ok": true,
  "data_plane_running": true,
  "ai_node_running": true
}
```

## 回滚

回滚使用控制面运行时配置，不涉及远端宿主机路径：

```text
备份文件
  → app/xray/runtime/config-ai-node.json
  → docker restart xray-ai-node
  → 比较宿主/容器哈希
  → 验证 27166 和完整 REALITY 握手
```

备份文件包含敏感凭据，权限必须为 `0600`，不得提交到 Git 或复制到日志、工单和聊天记录。
