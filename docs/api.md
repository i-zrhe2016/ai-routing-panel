# API 与页面路径

> Type: Reference
> Status: Active
> Scope: 页面与 API 路径、认证边界、请求字段和返回体

## 认证规则

管理后台**没有登录**，由来源地址白名单放行（见 [控制面访问与来源白名单](panel-access.md)）；客户和租户各有自己的会话。

- 管理员：仅 `PANEL_ALLOWED_NETWORKS` 内的来源可访问（默认回环、私网、link-local 和 Tailscale CGNAT 网段），其他来源在路由前返回 403；写操作仍需 `X-CSRF-Token`。
- 客户：`/api/customer/*`（除 `plans`、`auth/*`）需客户会话，未登录返回 JSON 401（`{"ok":false,"code":"auth_required"}`）；变更请求需 `X-CSRF-Token`
- 租户：`/api/tenant/<token>/*` 需该端口的租户会话
- `GET /healthz` 永远不要求登录

所有响应都会带 `X-Request-ID`。客户端可以发送由字母、数字、`.`、`_`、`:`、`-` 组成且不超过 128 字符的值；缺失或不合法时由控制面生成新值。该 ID 只用于跨请求排障，不应包含 token、邮箱或其他敏感信息。

## 页面与订阅路径

- `/`：管理后台 SPA（React，构建产物 `app/static/admin/`）
- `/login`：租户登录页（控制台本身没有登录）
- `/probe-dashboard`：TCP 探针监控页
- `/ai-domain-dashboard`：AI 域名统计页
- `/portal`、`/portal/<path>`：订阅者门户 SPA（vue-router history）
- `/plans`：公共套餐信息页（只读；新购套餐不提供结账/预订单页）
- `/customer/login`、`/customer/register`：客户认证页
- `/tenant/<tenant_token>`：门户的单订阅只读模式壳（原“租户面板”，未认证时显示内联租户登录卡）
- `/tenant-subscriptions/<subscription_token>`：默认订阅
- `/tenant-subscriptions/<subscription_token>/clash`：Clash 订阅
- `/tenant-subscriptions/<subscription_token>/v2ray`：V2Ray 订阅

订阅 token 只对运行中的端口下发配置；端口停用、过期或达到流量上限时返回 `404`，避免客户端继续拿到无法连接的旧配置。

历史兼容订阅路径仍保留：

- `/<token>/<listen_port>`
- `/<token>/<listen_port>/clash`
- `/<token>/<listen_port>/v2ray`

### Clash 客户端分流

默认与 `/clash` 路径使用同一套规则，历史路径与租户路径均生效。客户端按从上到下的首个匹配规则处理：局域网与解禁直连 → 广告拒绝 → 下载直连 → ChatGPT/OpenAI、Claude/Anthropic、Gemini/AI Studio、GitHub（含资源与 Copilot）、Discord、WhatsApp 的显式域名走 `PROXY` → 原有 Google、Telegram、媒体等服务规则 → 广义代理/中国域名与 IP 规则 → `GEOIP,CN,DIRECT` → `MATCH,PROXY`。常用服务规则包含 API、静态资源、认证与语音相关域名，保留原有 25 个 ACL4SSR 规则提供者，不新增远程列表。

部署更新后，在 Clash 客户端刷新原订阅即可获取规则；只刷新规则提供者不会更新这些内嵌域名规则。`PROXY` 是客户端可选策略组，用户手动选 `DIRECT` 时这些服务也会直连。V2Ray 订阅仍是 VLESS 分享链接，不包含上述 Clash 分流规则；订阅地址、端口、每用户 UUID、REALITY 参数和 DNS 默认值不受规则更新影响。

## JSON API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/dashboard` | 获取首页完整状态 |
| `GET` | `/api/insights` | 只读历史快照：主机清单、按天流量序列、探针可用性、DNS 切换事件；可选 `days`（1–30，默认 1）。不触发同步、配置下发或节点操作 |
| `POST` | `/api/ports` | 新建监听端口 |
| `PUT` | `/api/ports/<port_id>` | 更新端口配置 |
| `POST` | `/api/ports/<port_id>/toggle` | 启用或停用端口 |
| `DELETE` | `/api/ports/<port_id>` | 删除端口 |
| `POST` | `/api/ports/<port_id>/reset-traffic` | 重置端口流量并重新启用 |
| `POST` | `/api/ports/<port_id>/rotate-tenant-token` | 重置租户访问地址（`/tenant/<token>`）|
| `POST` | `/api/ports/<port_id>/rotate-tenant-credentials` | 重置租户用户名和密码 |
| `POST` | `/api/ports/<port_id>/rotate-subscription-token` | 重置租户订阅地址 |
| `POST` | `/api/subscriptions/rotate` | 重置历史兼容的全局订阅 token |
| `POST` | `/api/plans` / `PUT /api/plans/<id>` | 套餐增改 |
| `GET` | `/api/orders` | 列出商业化订单 |
| `POST` | `/api/orders/<id>/{fulfill,reject,cancel}` | 订单开通 / 驳回 / 取消 |
| `GET`/`PUT` | `/api/commerce-settings` | 商业设置（收款说明、二维码、订单有效期）|
| `POST` | `/api/data-plane/restart` | 重启数据面 |
| `POST` | `/api/data-plane/diagnose` | 数据面体检（TCP 探测 + Reality 握手 + 配置一致性校验）|
| `GET` | `/api/ai-node/status` | 获取 AI 节点状态 |
| `POST` | `/api/ai-node/restart` | 重启 AI 节点 |
| `GET` | `/api/dns-failover` | 获取 DNS 故障切换状态 |
| `POST` | `/api/dns-failover/check` | 立即执行一次 DNS 检测 |
| `POST` | `/api/dns-failover/switch` | 手动切主备（`{"target": "primary\|backup"}`）|
| `POST` | `/api/ai-routing/ports/<port_id>/scope` | 按稳定账号 ID 设置独立范围（`{"traffic_scope": "all\|classified\|inherit"}`）；`inherit` 恢复默认；保留候选选择，错误不提交偏好 |
| `POST` | `/api/ai-routing/scope` | 设置独立流量范围（`{"traffic_scope": "classified\|all"}`）；应用语义见 [AI 转发范围](ai-routing.md#转发范围) |
| `POST` | `/api/ai-routing/switch` | 手动切换 AI 路由（`{"mode": "primary\|backup\|auto\|forced_fallback"}`）|
| `POST` | `/api/client-errors` | 前端上报网络、HTTP、解析和未捕获运行时错误（需 `X-CSRF-Token`；不接收请求体或敏感 Header）|

### 订阅者门户 API（客户会话 + CSRF）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/customer/me` / `/api/customer/overview` | 当前客户与门户首页数据 |
| `GET` | `/api/customer/subscriptions[/<id>]` | 订阅列表 / 详情（含 Clash/V2Ray/VLESS 链接、用量）|
| `POST` | `/api/customer/subscriptions/<id>/renew` | 续费下单 |
| `GET` | `/api/customer/orders[/<order_no>]` | 订单列表 / 详情 |
| `POST` | `/api/customer/orders/<order_no>/payment-proof` | 上传支付凭证（multipart）|
| `GET` | `/api/customer/plans` | 公开套餐列表（无需登录）|
| `POST` | `/api/customer/auth/{login,register,logout}` | 客户认证 |

### 租户直达 API（token / 每端口凭据）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/tenant/<tenant_token>/subscription` | 单订阅只读详情（管理员会话或该端口租户会话）|
| `POST` | `/api/tenant/<tenant_token>/login` | 每端口租户登录 |

## 创建 / 更新端口字段

- `listen_port`
  - 创建时可省略、设为 `null` 或留空，服务端在创建事务内自动分配端口；区间配置见[配置说明](configuration.md#租户端口自动分配)。显式指定时范围为 `1-65535`。
  - 更新时必填，不会自动重新分配。
- `expires_at`
  - 可选，格式示例：`2026-06-30T20:00`
- `traffic_limit`
  - 可选，支持 `10G`、`500MB`、`1048576`
- `note`
  - 可选，最多 `200` 字符

创建成功返回 `201`，包含 `created_port_id` 和最新 `dashboard`，后台据此选中新租户。自动分配区间未配置或已耗尽时返回 `400`，不创建租户记录。

流量工作区提供今日、近 7 天、近 30 天三种范围。今日从北京时间的 00:00 起算；近 7/30 天包含今日及之前 6/29 个自然日。区间卡片、端口表和曲线均使用该窗口的统计，累计配额使用量单独标注。旧版本已按其他时区日期汇总的历史日数据无法无损重新划分，新写入统一按北京时间日期归档。时区约定见[配置说明](configuration.md)。

示例：

```bash
curl -u admin:secret http://redacted-ip-007:18080/api/dashboard

curl -u admin:secret \
  -H 'Content-Type: application/json' \
  -X POST http://redacted-ip-007:18080/api/ports \
  -d '{
    "listen_port": 32001,
    "expires_at": "2026-06-30T20:00",
    "traffic_limit": "20G",
    "note": "demo-tenant"
  }'
```

## 常见返回体

管理后台写操作成功后通常返回（携带重建后的完整首页状态）：

```json
{
  "ok": true,
  "message": "...",
  "level": "success",
  "dashboard": {
    "...": "最新首页状态"
  }
}
```

订阅者门户 / 租户接口统一用 `data` 携带受影响资源，不返回管理员 `dashboard`：

```json
{ "ok": true, "message": "...", "level": "success", "data": { "...": "受影响资源" } }
```

失败时通常返回：

```json
{
  "ok": false,
  "message": "错误信息"
}
```

健康检查返回：

```json
{
  "ok": true,
  "data_plane_running": true,
  "ai_node_running": true
}
```

其中：

- `ok` 受 `PANEL_HEALTH_REQUIRES_XRAY` 影响
- `data_plane_running` 反映当前普通数据面是否可用
- `ai_node_running` 反映 AI 节点是否可达（目标态）

## 自动探测故障文档

- `GET /api/probe-incidents`：返回 `{ok, incidents}`，最近最多 100 条。每条保留来源、目标、探测来源、`node_failure/executor_error` 分类、首次/最近失败时间、次数、恢复时间和 `queued/running/completed/failed` 分析状态。
- `GET /api/probe-incidents/<incident_id>/report`：返回 `{ok, report}`，`report` 是私有 Markdown 的纯文本。ID 必须为固定 32 位十六进制值；不存在、无效路径、符号链接或超限报告返回 `404`。响应禁止缓存。

两个接口沿用面板内网/Tailscale 来源访问限制，不发布生产认证资料。生命周期与报告边界见[运维与排障](operations.md#codex-自动故障记录)。
