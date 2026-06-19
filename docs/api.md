# API 与页面路径

## 认证规则

- 未设置 `PANEL_USERNAME` / `PANEL_PASSWORD` 时，首页和 `/api/*` 默认无需登录
- 只要任一管理员凭据被设置，首页、探针页和 `/api/*` 都要求认证
- Web 页面支持表单登录
- API 支持 `Authorization: Basic ...`
- `GET /healthz` 永远不要求登录

## 页面与订阅路径

- `/`：管理员首页
- `/login`：管理员登录页
- `/probe-dashboard`：TCP 探针监控页
- `/ai-domain-dashboard`：AI 域名统计页
- `/tenant/<tenant_token>/login`：租户登录页
- `/tenant/<tenant_token>`：租户面板
- `/tenant-subscriptions/<subscription_token>`：默认订阅
- `/tenant-subscriptions/<subscription_token>/clash`：Clash 订阅
- `/tenant-subscriptions/<subscription_token>/v2ray`：V2Ray 订阅

历史兼容订阅路径仍保留：

- `/<token>/<listen_port>`
- `/<token>/<listen_port>/clash`
- `/<token>/<listen_port>/v2ray`

## JSON API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/dashboard` | 获取首页完整状态 |
| `POST` | `/api/ports` | 新建监听端口 |
| `PUT` | `/api/ports/<port_id>` | 更新端口配置 |
| `POST` | `/api/ports/<port_id>/toggle` | 启用或停用端口 |
| `DELETE` | `/api/ports/<port_id>` | 删除端口 |
| `POST` | `/api/ports/<port_id>/reset-traffic` | 重置端口流量并重新启用 |
| `POST` | `/api/ports/<port_id>/rotate-tenant-token` | 重置租户面板地址 |
| `POST` | `/api/ports/<port_id>/rotate-tenant-credentials` | 重置租户用户名和密码 |
| `POST` | `/api/ports/<port_id>/rotate-subscription-token` | 重置租户订阅地址 |
| `POST` | `/api/subscriptions/rotate` | 重置历史兼容的全局订阅 token |
| `POST` | `/api/data-plane/restart` | 重启唯一数据面 |

## 创建 / 更新端口字段

- `listen_port`
  - 必填，范围 `1-65535`
- `expires_at`
  - 可选，格式示例：`2026-06-30T20:00`
- `traffic_limit`
  - 可选，支持 `10G`、`500MB`、`1048576`
- `note`
  - 可选，最多 `200` 字符

示例：

```bash
curl -u admin:secret http://127.0.0.1:18080/api/dashboard

curl -u admin:secret \
  -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:18080/api/ports \
  -d '{
    "listen_port": 32001,
    "expires_at": "2026-06-30T20:00",
    "traffic_limit": "20G",
    "note": "demo-tenant"
  }'
```

## 常见返回体

写操作成功后通常返回：

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
  "data_plane_running": true
}
```

其中：

- `ok` 受 `PANEL_HEALTH_REQUIRES_XRAY` 影响
- `data_plane_running` 反映当前数据面是否可用
