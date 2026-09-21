# 控制面访问与来源白名单

## 当前入口

控制面**没有登录界面**：能否访问完全由客户端来源地址决定。

- 内网直连：`http://<控制面内网地址>:18080`
- Tailscale：`http://<控制面-Tailscale-IP>:18080`（用 `tailscale status` 查控制面节点地址）

只有落在 `PANEL_ALLOWED_NETWORKS` 内的来源能建立连接；其他来源在进入任何路由之前就返回 403（`/api/**` 返回 JSON `{"ok":false,"code":"forbidden_source"}`，其他路径返回纯文本）。

默认白名单：`127.0.0.0/8`、`::1/128`、`10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16`、`169.254.0.0/16`、`100.64.0.0/10`（Tailscale）、`fc00::/7`、`fe80::/10`。

## 为什么没有登录

- 面板只承担运维操作，公网域名 + Cloudflare Access Email OTP 入口已不再使用。
- 旧的管理员账号密码、Basic Auth 和 `PANEL_INTERNAL_HOSTS` 内网 Host 旁路都以“客户端能到达端口”为前提；其中 Host 旁路还可以被任何能连到端口的客户端伪造。现在改为直接校验来源地址，Host 头不再参与鉴权。
- 写操作仍然要求 CSRF 令牌：操作者浏览器通常也在 tailnet 内，来源白名单挡不住被诱导的跨站请求。

## 配置

| 变量 | 说明 |
| --- | --- |
| `PANEL_ALLOWED_NETWORKS` | 逗号分隔的 CIDR 列表；留空使用上面的默认值。需要收紧时显式列出，例如 `100.64.0.0/10,127.0.0.1/32` |

宿主机 nftables 表 `ai_routing_panel_firewall` 只放行内网网段、回环地址和 Xray 端口，`18080` 不在公网放行列表；应用层白名单是第二道门。

## 云侧待办（仓库外）

域名入口的清理不在本仓库内：需要在 Cloudflare 控制台删除 `xray.zrhe2016.cc` 对应的 Access 应用，并清理不再使用的 DNS 记录。同一域名下的 Grafana 入口（`/grafana/`）目前仍由 Access 保护，删除前先确认是否保留。

## 租户与客户登录

租户登录（`/login`、`/tenant/<token>/login`）和客户门户登录（`/customer/login`、`/portal`）保留各自的凭据，不受本次变更影响。
