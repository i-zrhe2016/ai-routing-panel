# AI 节点独立凭据

## 模块职责

本文件定义主数据面 `ai_proxy` outbound 与 AI 节点 VLESS + REALITY inbound 之间的认证契约。部署步骤见 [AI 节点部署与 SSH 纳管](ai-node-deployment.md)。

## 凭据边界

AI 节点拥有独立于主数据面的 REALITY 身份：

```text
主数据面服务端凭据          AI 隧道凭据
app/xray/.env              AI 节点权威配置
      │                           │
      ├─ 服务客户端连接           ├─ AI inbound 私钥
      │                           └─ 主数据面 ai_proxy outbound 参数
      └─ 不得覆盖 AI 凭据
```

禁止把主数据面的 `XRAY_CLIENT_UUID`、`XRAY_REALITY_*`、`XRAY_SERVER_NAME` 直接写入 AI 节点配置。

## 必须匹配的字段

| 主数据面 `ai_proxy` outbound | AI 节点 inbound | 要求 |
| --- | --- | --- |
| `vnext[0].address` | 公网入口 | `nat.qq.pw` |
| `vnext[0].port` | `inbound.port` | `27166` |
| `users[0].id` | `clients[0].id` | UUID 完全一致 |
| `users[0].flow` | `clients[0].flow` | Flow 完全一致 |
| `realitySettings.publicKey` | 由 `privateKey` 派生 | 公私钥属于同一密钥对 |
| `realitySettings.shortId` | `shortIds[0]` | 完全一致 |
| `realitySettings.serverName` | `serverNames[0]` | 完全一致 |
| `realitySettings.fingerprint` | 客户端指纹策略 | 与约定一致 |

TCP 可达只能证明地址和端口开放，不能证明上述认证字段匹配。

## 私钥和公钥

- REALITY 私钥只保存在 AI 节点权威配置和受保护备份中。
- 主数据面只需要对应公钥。
- 公钥应使用与运行环境一致的 Xray 版本，从 AI 私钥确定性派生。
- 不要运行无参数的 `xray x25519` 作为恢复操作；那会生成新密钥对并使现有 outbound 失效。

## 安全比对

排障时不得输出原始 UUID、私钥、公钥、Short ID 或 SNI。两端分别计算每个字段的 SHA-256，只比较摘要：

```text
主数据面：field → SHA-256 ─┐
                           ├─ 只输出 match=true/false
AI 节点：field → SHA-256 ──┘
```

至少比较：

```text
address, port, uuid, flow, public, short, sni, fingerprint
```

若任一认证字段不匹配，应停止配置下发并恢复上一份已知可用配置。

## 权威来源

当前生产权威关系：

- AI inbound：AI 节点真实 bind source `/root/.codex/xray-main/config.json`。
- 主数据面 outbound：主数据面运行配置中的 `tag=ai_proxy` outbound。
- 控制面 `config-ai-node.json`：当前不是 AI 凭据权威来源，不能自动覆盖生产 AI 节点。

## 配置同步保护

当前根 `.env` 应保持：

```env
AI_NODE_CONFIG_PATH=
```

这会关闭 AI 配置上传，但保留：

- SSH 纳管；
- AI 节点在线检查；
- `xray` 容器重启。

只有在系统增加独立、受保护的 AI 凭据输入，并且渲染器不再复用主数据面 `XRAY_*` 后，才可恢复自动同步。

## 变更检查清单

任何 AI 凭据变更都必须：

1. 备份 AI 节点真实配置源。
2. 生成候选 inbound 与 outbound。
3. 在两端分别计算字段摘要。
4. 确认全部字段匹配。
5. 分别执行 Xray 配置校验。
6. 先部署 inbound，再部署 outbound 时安排原子切换或维护窗口。
7. 验证完整 ChatGPT 请求，而不只测试 TCP 端口。
8. 保留可立即执行的双端回滚点。
