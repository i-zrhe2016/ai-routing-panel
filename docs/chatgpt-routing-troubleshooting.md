# ChatGPT 路由排障

## 模块职责

本文件只描述 ChatGPT/OpenAI 流量从客户端经主数据面转发到 AI 节点的排障流程。

部署说明见 [AI 节点部署与 SSH 纳管](ai-node-deployment.md)，认证字段定义见 [AI 节点独立凭据](ai-node-credentials.md)。

## 流量链路

```text
ChatGPT 客户端
      │
      ▼
主数据面 VLESS + REALITY inbound
      │
      ├─ 域名规则命中 ChatGPT/OpenAI
      ▼
ai_proxy VLESS + REALITY outbound
      │
      ▼
AI 上游选择器
      ├─ 主：nat.qq.pw:27166
      └─ 备：redacted-ip-004:27166
      │
      ▼
AI 节点 VLESS + REALITY inbound
      │
      ▼
freedom → OpenAI HTTPS
```

禁止把 AI 流量发往旧上游 `isif.217777.xyz:42994`。当前生产候选为主 `nat.qq.pw:27166` 和备 `redacted-ip-004:27166`，实际目标以 `ai_target` 和 `manual_mode` 为准。

## 排障顺序

遵循由外到内、由只读到变更的顺序：

```text
1. 控制面健康
2. 主数据面容器
3. 动态域名规则
4. AI 候选选择状态
5. 主数据面 → 当前 AI 端口
6. VLESS/REALITY 参数
7. AI 容器真实配置
8. AI 节点互联网出口
9. 客户端重新建连
```

## 1. 检查控制面和节点状态

`GET /healthz` 应返回：

```json
{
  "ok": true,
  "data_plane_running": true,
  "ai_node_running": true
}
```

注意：`ai_node_running=true` 只证明控制面能检测到当前 AI 节点的 `redacted-ip-007:27166`（本机 Docker 模式）或远端 SSH 模式 Socket，不证明 REALITY 凭据匹配，也不证明 ChatGPT 请求成功。

## 2. 检查主数据面

确认：

- `xray-reality-local` 容器为 `running/healthy`；
- 主数据面运行配置包含 `tag=ai_proxy` outbound；
- 该 outbound 协议为 `vless`，`streamSettings.security=reality`；
- 目标是当前 `ai_target` 选中的主或备用候选；
- 不包含旧 ISIF 地址。

## 3. 检查域名覆盖

动态路由应覆盖至少以下域名族：

- `chatgpt.com`
- `openai.com`
- `oaistatic.com`
- `oaiusercontent.com`

同时确认规则的 `outboundTag` 为 `ai_proxy`。仅检查域名列表存在还不够，还要确认动态片段已经合并进主数据面的实际运行配置。

## 4. 检查 AI 候选选择状态

优先查看最新报表或控制台 AI 路由状态，确认：

- `manual_mode` 是 `auto`、`primary`、`backup` 或 `forced_fallback`；
- `ai_candidates` 同时列出主 `nat.qq.pw:27166` 与备 `redacted-ip-004:27166`；
- `ai_target.selected_index` 与实际 `ai_proxy` 目标一致；
- 固定模式下若状态为 `manual_target_unreachable`，不要期待系统自动切换另一节点。

## 5. 检查网络连通

从主数据面分别测试当前候选的 TCP 连接；若使用 REALITY SNI，应进一步执行 REALITY 探测。

```text
reachable  → 仅证明网络层正常，继续检查 REALITY
refused    → 检查对应 AI 容器、监听端口和防火墙
超时       → 检查 DNS、路由、安全组和中间网络
```

不要把“端口可达”当作“代理可用”。

## 6. 检查 VLESS/REALITY 参数

主数据面 outbound 和 AI inbound 必须匹配：

```text
address, port, uuid, flow, public key, shortId, SNI, fingerprint
```

为避免泄露凭据，两端分别计算字段 SHA-256，只输出 `match=true/false`。详细契约见 [AI 节点独立凭据](ai-node-credentials.md)。

典型故障表现：

```text
TCP 27166 可达
AI 节点互联网出口正常
但 UUID / 公钥 / shortId / SNI 不匹配
→ REALITY 握手失败
→ ChatGPT 无法连接
```

## 7. 核对 Docker 真实配置源

单文件 bind mount 与原子替换组合容易产生误判：宿主机修改了错误路径，或者替换了一个未被容器实际挂载的 inode，容器仍运行旧配置。

必须执行：

1. 对普通数据面执行 `docker inspect xray-reality-local`，对本机备用执行 `docker inspect xray-ai-node`，分别查看 `/etc/xray/config.json` 对应的 `Mounts[].Source`。
2. 分别计算实际 bind source 与容器内 `/etc/xray/config.json` 的 SHA-256。
3. 重启后两者摘要必须一致；不要只检查控制面生成文件而跳过实际挂载源。

如果摘要不同，先修正管理路径，不要继续修改凭据。

## 8. 检查 AI 节点出口

在 AI 节点上检查 DNS 和 HTTPS：

- `chatgpt.com`
- `api.openai.com`
- `auth.openai.com`
- `cdn.oaistatic.com`

`403`、`404` 或 `421` 通常说明 DNS、TCP 和 TLS 已经到达对端，只是请求缺少浏览器状态、正确 Host 路径或 API 身份；它们不同于连接超时、DNS 失败和 TLS 握手失败。

## 9. 恢复已知可用配置

如果凭据在配置下发后不匹配：

1. 立即停止再次自动同步。
2. 恢复 AI 节点真实 bind source 的部署前备份。
3. 重启实际承载 AI inbound 的容器（本机备用为 `xray-ai-node`）。
4. 比较主数据面 outbound 与恢复后 inbound 的字段摘要。
5. 确认全部匹配。
6. 检查 `27166`。
7. 让客户端断开旧连接并重新连接。

当前保护设置：

```env
AI_NODE_CONFIG_PATH=
```

这会阻止控制面再次上传错误配置，同时保留状态检查和远程重启。

## 验收清单

- [ ] `/healthz` 返回 HTTP 200。
- [ ] `data_plane_running=true`。
- [ ] `ai_node_running=true`。
- [ ] 主数据面动态路由命中 ChatGPT/OpenAI 域名。
- [ ] `manual_mode` 和 `ai_target.selected_index` 与预期一致。
- [ ] 当前 AI 目标是主 `nat.qq.pw:27166` 或备 `redacted-ip-004:27166`。
- [ ] 不存在旧 ISIF 上游。
- [ ] 主数据面到 AI 节点 TCP 可达。
- [ ] 八个隧道字段全部匹配。
- [ ] AI 宿主配置与容器内配置哈希一致。
- [ ] AI 节点能连接 OpenAI HTTPS。
- [ ] 客户端重新建连后 ChatGPT 实际可用。
