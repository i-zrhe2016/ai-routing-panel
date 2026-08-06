# AI 节点部署与 SSH 纳管

## 模块职责

AI 节点运行独立的 VLESS + REALITY Xray，接收主数据面转发的 AI 域名流量并通过 `freedom` 直出。本文件只说明 AI 节点部署、SSH 纳管、状态探测、重启和配置同步边界。

凭据匹配规则见 [AI 节点独立凭据](ai-node-credentials.md)，ChatGPT 故障处理见 [ChatGPT 路由排障](chatgpt-routing-troubleshooting.md)。

## 生产拓扑

```text
控制面
  ├─ SSH 管理 ───────────────▶ nat.qq.pw:27160
  └─ 状态检查/远程重启

主数据面
  └─ VLESS + REALITY ────────▶ nat.qq.pw:27166
                                  │
                                  ▼
                            Docker: xray
                                  │
                                  └─ freedom 直出
```

两个端点职责不同：

| 端点 | 用途 |
| --- | --- |
| `nat.qq.pw:27160` | SSH 管理，只供控制面运维使用 |
| `nat.qq.pw:27166` | AI 业务流量，供主数据面 VLESS outbound 使用 |

禁止使用已下线的旧 AI 上游 `isif.217777.xyz:42994`。

## 当前远端部署

| 项目 | 当前值 |
| --- | --- |
| 部署方式 | Docker |
| 容器名 | `xray` |
| 宿主机真实配置源 | `/root/.codex/xray-main/config.json` |
| 容器内配置路径 | `/etc/xray/config.json` |
| 业务监听端口 | `27166` |

> 不能仅凭容器内路径推断宿主机配置路径。必须通过 `docker inspect xray` 的 `Mounts[].Source` 确认真实 bind source。

## SSH 认证

当前控制面使用专用 Ed25519 私钥访问 AI 节点，私钥只读挂载到容器：

```text
宿主机 0600 私钥
  └─ 只读挂载 → /run/secrets/fleet_ssh_key
                    │
                    └─ /app/scripts/ai-node-ssh
                         └─ ssh -o IdentitiesOnly=yes -i ...
```

同时只读挂载 AI 节点专用 `known_hosts`，并强制：

```text
PreferredAuthentications=publickey
PasswordAuthentication=no
KbdInteractiveAuthentication=no
StrictHostKeyChecking=yes
UserKnownHostsFile=/root/.ssh/known_hosts_ai
```

不要使用密码认证或 `StrictHostKeyChecking=no`。密钥轮换和恢复流程见 [SSH 密钥登录与轮换](ssh-key-access.md)。

## 根 `.env` 配置

示例不包含密码：

```env
AI_NODE_SSH_TARGET=root@nat.qq.pw
AI_NODE_SSH_BIN=/app/scripts/ai-node-ssh
AI_NODE_SSH_OPTIONS=-p 27160 -o PreferredAuthentications=publickey -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/root/.ssh/known_hosts_ai -o ConnectTimeout=8
AI_NODE_SSH_KEY_FILE=/run/secrets/fleet_ssh_key
AI_NODE_CONTAINER_NAME=xray
AI_NODE_RESTART_COMMAND=
AI_NODE_PROBE_HOST=nat.qq.pw
AI_NODE_API_SERVER=127.0.0.1:27166
AI_NODE_CONFIG_PATH=
```

关键语义：

- `AI_NODE_API_SERVER=127.0.0.1:27166`：控制面通过 SSH 在远端执行 TCP Socket 存活检查；当前 AI 配置不启用 Xray Stats API，因此这里不是 Stats API 地址。
- `AI_NODE_CONFIG_PATH=`：显式留空会使 `supports_sync=false`，禁止控制面上传配置。
- SSH 状态检查和 `AI_NODE_CONTAINER_NAME=xray` 提供的容器重启能力不依赖配置同步，因此仍然可用。

## `app/xray/.env` 上游配置

```env
AI_UPSTREAM_HOST=nat.qq.pw
AI_UPSTREAM_PORT=27166
```

这两个变量只定义业务端点，不能替代 AI 节点独立的 UUID、REALITY 密钥、Short ID 和 SNI。

## 为什么默认禁用配置上传

AI 节点当前使用独立 REALITY 凭据。控制面生成的 `config-ai-node.json` 若复用主数据面 `XRAY_*` 凭据，会破坏主数据面现有 VLESS outbound 与 AI inbound 的认证匹配。

因此生产默认保持：

```env
AI_NODE_CONFIG_PATH=
```

只有在控制面已经支持并安全加载 AI 节点独立凭据后，才能填入真实宿主路径并恢复自动上传。

## 受控配置同步流程

启用同步前必须完成以下步骤：

1. 使用 `docker inspect xray` 确认真正的宿主机 bind source。
2. 对真实配置源创建权限为 `0600` 的时间戳备份。
3. 确认控制面生成的 outbound 与候选 inbound 的全部认证字段匹配。
4. 本地使用同版本 Xray 执行 `run -test`。
5. SSH 上传临时文件。
6. 在远端容器中再次执行 `run -test`。
7. 原子替换真实宿主配置源。
8. 重启 `xray` 容器。
9. 比较宿主配置与容器内 `/etc/xray/config.json` 的 SHA-256。
10. 验证容器运行、`27166` 可达以及 ChatGPT 实际请求成功。

任一步失败都应恢复备份并重启容器。

## 日常检查

```bash
# 控制面健康状态
curl -fsS http://127.0.0.1:18080/healthz

# AI 节点容器状态（通过已配置的安全 SSH 通道执行）
docker inspect xray --format '{{.State.Running}}|{{.State.Status}}|{{.State.StartedAt}}'

# 业务端口
nc -zv nat.qq.pw 27166
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

回滚必须恢复真实 bind source，而不是假定的 `/etc/xray/config.json` 宿主路径：

```text
备份文件
  → /root/.codex/xray-main/config.json
  → docker restart xray
  → 比较宿主/容器哈希
  → 验证 27166 和完整 REALITY 握手
```

备份文件包含敏感凭据，权限必须为 `0600`，不得提交到 Git 或复制到日志、工单和聊天记录。
