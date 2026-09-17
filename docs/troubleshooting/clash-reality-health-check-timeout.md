# Clash REALITY 健康检查超时排障记录

本文记录 2026-09-03 `ai.zrhe2016.cc:31098` 在 Clash 中显示 `check timeout` 的故障定位、修复和验收结果。文档只记录故障边界与可复用步骤，不记录订阅令牌、UUID、REALITY 私钥或其他租户凭据。

## 2026-09-17 数据面 Xray 启动失败：残留 Unix Socket

### 结论

普通数据面主机的基础网络路径正常：控制面到数据面的路由、Ping、SSH 以及公网入口 TCP 均可达；但控制面检查到远端 Xray API `127.0.0.1:10085` 被拒绝，导致面板将数据面标记为不可连接。

故障期间 `xray-reality-local` 处于 `Restarting (255)` / `unhealthy`。Xray 日志反复出现：

```text
failed to listen Unix Domain Socket ... bind: address already in use
```

宿主机日志目录中存在 7 个 `entry-*.sock` 残留文件，而 `ss -xlpn` 未发现对应的活动 Unix Socket 监听。因此本次“数据面无法连接”的直接原因是 Xray 重启后无法重新绑定残留 Socket，不是数据面网络路径故障。

### 故障期间证据

| 检查项 | 结果 | 判定 |
| --- | --- | --- |
| 控制面到数据面路由、Ping、SSH | 正常 | 基础管理链路正常 |
| 数据面公网入口 TCP | `443`、`31098`、`31333`、`31339`、`31340` 可达 | 入口网络正常 |
| 面板数据面状态 | `reachable=false`，Xray API 连接被拒绝 | 管理面检查失败 |
| `xray-reality-local` | `Restarting (255)` / `unhealthy` | Xray 进程未稳定运行 |
| Unix Socket | 7 个残留文件，无活动 `entry-*` 监听 | 确认 Socket 文件残留 |
| Xray 日志 | `address already in use` | 确认启动失败根因 |

残留文件名为 `entry-panel-31000.sock`、`entry-panel-31098.sock`、`entry-panel-31333.sock`、`entry-panel-31339.sock`、`entry-panel-31340.sock`、`entry-panel-31341.sock` 和 `entry-unified-443.sock`。这些文件不是订阅凭据或 REALITY 密钥。

### 修复操作

在确认没有活动的 `entry-*` Unix Socket 监听后，仅停止异常容器、删除指定日志目录下的 Socket 类型残留文件，再启动原容器。没有修改 Xray 配置、订阅内容、UUID、REALITY 参数或租户数据。

```bash
docker stop --time 10 xray-reality-local

# 若仍有活动监听，应中止，不要删除文件
if ss -xlpn | grep -q 'entry-'; then
  echo 'active entry socket found; abort'
  exit 1
fi

find /root/xray-routing-panel/app/xray/logs \
  -maxdepth 1 -type s -name 'entry-*.sock' -print -delete

docker start xray-reality-local
```

本次实际清理 7 个残留 Socket，容器随后恢复运行。该操作只匹配明确目录、Socket 类型和 `entry-*.sock` 文件，不能替代活动监听检查。

### 修复后验收

修复后确认：

1. `xray-reality-local` 为 `running / healthy`，并在连续状态轮询中保持稳定。
2. `xray run -test -config /etc/xray/config.json` 返回码为 `0`，输出包含 `Configuration OK`。
3. Xray API `127.0.0.1:10085` 恢复监听，面板数据面状态恢复为 `reachable=true`、`xray_running=true`。
4. `443`、`31098`、`31333`、`31339`、`31340` 入口均恢复 TCP 可达；对应 HAProxy、legacy forwarder 和 Xray 监听均存在。
5. 容器本次启动后的新日志中没有新增 `failed to start`、`address already in use`、`panic` 或 `fatal`。

仓库内协议 smoke 脚本在控制面镜像中未能执行，因为该镜像缺少 `curl`；随后发现镜像内的本地 `client-test.json` 与数据面实际使用的测试配置不一致。因此这两项不能作为协议失败或成功的证据，外部客户端仍应按现有门禁完成 VLESS + REALITY 复测。当前验收结论限定为：数据面容器、管理 API、监听端口和配置解析均已恢复。

### 后续预防

- 在 Xray 启动前增加精确的 Socket 残留预检：只有确认没有活动的同名监听时，才清理指定目录中的 `entry-*.sock`。
- 将健康检查从“配置可解析”扩展为“运行时 API 可访问且入口监听存在”；`xray run -test` 本身不能证明数据面可用。
- 保留容器异常退出、Unix Socket 绑定失败和管理 API 拒绝连接的告警，避免只看到端口层正常就误判服务恢复。

## 结论

`31098` 的 TCP 端口在国内三网基本可达，但当时生产 Xray 进程会重置有效的 VLESS + REALITY 握手，因此 Clash 无法访问健康检查目标并显示超时。

订阅内容和磁盘上的服务端配置一致；使用同一份 inbound 配置启动的临时 Xray 实例能够正常返回 HTTP 204。仅重启生产容器 `xray-reality-local` 后，完整协议检查和国内实际流量均恢复。

已确认的直接故障点是生产 Xray 进程的运行状态，而不是订阅接口、租户参数、DNS 或国内到 `31098` 的普遍网络阻断。进程为何进入该异常状态没有足够证据，不将其归因于某个未验证的 Xray 缺陷。

## 排障流程

![Clash REALITY 健康检查超时排障流程](../diagrams/clash-reality-health-check.svg)

[PlantUML 源文件](../diagrams/clash-reality-health-check.puml)

## 影响范围

- 客户端现象：Clash 节点测速或健康检查显示 `timeout`。
- 故障入口：普通数据面上的 VLESS + REALITY 入口 `ai.zrhe2016.cc:31098`。
- 未受影响：租户订阅接口仍返回 HTTP 200，生成的 Clash YAML 可正常解析。
- 端口层表现：TCP 三次握手大多成功，但完整 REALITY 握手失败。
- 协议层表现：健康检查请求未到达目标站点，客户端收到连接重置。

## 关键证据

| 检查层级 | 故障期间结果 | 判定 |
| --- | --- | --- |
| 订阅接口 | HTTP 200，Clash YAML 有效 | 订阅服务正常 |
| 配置一致性 | UUID、公钥、short ID、SNI、flow 全部匹配 | 排除订阅与磁盘配置失配 |
| 容器健康检查 | `running / healthy`，`xray run -test` 返回 `Configuration OK` | 只证明配置可解析，不能证明数据面可用 |
| 国内 TCP 拨测 | 128 个电信、移动、联通节点中 127 个成功，中位延迟约 162 ms | 排除端口在国内普遍不可达 |
| 域名与直连 IP 对照 | 两者均为 127/128 成功，失败点相同 | DNS 不是本次主因 |
| 完整协议检查 | 独立美国、台湾 Xray 客户端均收到连接重置 | Clash timeout 可在服务端外部复现 |
| 生产进程内外网对照 | 通过公网地址和 Tailscale 地址访问同一生产进程均失败 | 排除公网入口单一路径问题 |
| 同配置临时实例 | 外部客户端经 VLESS + REALITY 访问健康目标返回 HTTP 204 | inbound 配置内容有效，故障收敛到生产进程状态 |

端口探测只能验证 TCP 握手，不能代替 VLESS + REALITY 的完整代理检查。因此“端口在线”和“Clash 健康检查超时”可以同时发生。

## 修复操作

故障边界确认后，只重启异常的生产 Xray 容器，没有修改订阅或 REALITY 参数：

```bash
docker restart xray-reality-local
```

重启时间为 2026-09-03 02:44 UTC。容器恢复监听后，健康状态从 `starting` 转为 `healthy`。

## 修复后验收

重启后执行以下验证：

1. `xray-reality-local` 状态为 `running / healthy`。
2. `31098` 重新处于监听状态。
3. 美国外部客户端通过公网完成 VLESS + REALITY 请求，健康目标返回 HTTP 204。
4. 台湾外部客户端执行相同请求，健康目标返回 HTTP 204。
5. Xray access log 再次出现来自国内公网地址的真实代理流量。

以上条件同时满足后，本次故障判定为已恢复。

## 可复用排障顺序

### 1. 区分端口可达与协议可用

先从客户端网络以外的位置测试入口端口：

```bash
timeout 5 bash -c 'exec 3<>/dev/tcp/<node-host>/<node-port>'
```

TCP 成功只说明监听、防火墙和基础路径可用。仍需使用配置正确的外部 Xray 或 Mihomo 客户端，通过该节点访问一个 `generate_204` 目标。

### 2. 检查生产容器与监听

```bash
docker inspect -f \
  'status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
  xray-reality-local

ss -ltnp '( sport = :<node-port> )'
```

不要把 `Configuration OK` 当作代理可用性的充分条件；当前健康检查主要验证配置语法。

### 3. 比较配置但不输出凭据

至少核对以下字段是否一致：

- 客户端 UUID；
- `flow`，本入口为 `xtls-rprx-vision`；
- REALITY 公钥与服务端私钥派生结果；
- short ID；
- 客户端 `servername` 与服务端 `serverNames`；
- 节点地址和端口。

检查脚本只应输出 `match/no-match`，不得打印原始值。

### 4. 使用同配置临时实例缩小边界

如果生产进程完整握手失败而字段全部匹配，可在未占用的临时端口启动同版本、同 inbound 配置的 Xray，并通过 Tailscale 或受控防火墙从外部复测：

- 临时实例也失败：继续检查生成配置、伪装目标和 Xray 版本兼容性。
- 临时实例成功：故障收敛到当前生产进程或其加载状态，再考虑重启生产容器。

测试结束后必须停止并删除临时实例，不开放长期公网入口。

### 5. 重启后的验收门禁

重启不能单独作为完成标准。必须同时满足：

- 容器健康；
- 端口监听；
- 至少一个独立外部客户端完成 REALITY 握手；
- 通过代理访问健康目标返回 HTTP 204；
- access log 出现新的有效代理请求。

## 后续改进

当前容器健康检查使用 `xray run -test`，无法发现“配置合法但完整握手失败”。建议后续单独实现外部合成探测：

1. 从数据面以外的受控探针发起真实 VLESS + REALITY 请求。
2. 将 HTTP 204、握手耗时和失败原因写入监控指标。
3. 对连续失败和 Xray 异常重启次数告警。
4. 保留 TCP 探测作为网络层指标，但不以其替代协议层健康状态。

合成探针应从密钥管理系统读取最小权限测试凭据，不得把生产租户订阅链接或私钥写入代码、日志和监控标签。
