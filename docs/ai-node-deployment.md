# AI 节点部署

## 概述

AI 节点是一台远端独立机器上运行的 VLESS + REALITY Xray 实例，专门接收普通数据面转发的 AI 域名流量并 freedom 直出。控制面通过 SSH 纳管 AI 节点的配置生命周期（渲染 → 推送 → 校验 → 重启）和可达性监控。

AI 节点的职责边界：

- **只做**：接收来自普通数据面的 AI 流量、freedom 直出、接受控制面 SSH 管理
- **不做**：域名分类、动态路由生成、panel-ports 管理、access.log 采集、流量统计

AI 节点与普通数据面使用**同一套 REALITY 参数**（私钥、公钥、shortId、UUID、SNI、dest），只是监听端口不同。

## 架构拓扑

```
                        ┌─────────────────────────────┐
                        │        控制面 (Panel)        │
                        │      143.198.234.31         │
                        │  ┌───────────────────────┐  │
                        │  │  PanelState           │  │
                        │  │  ├─ data_plane (SSH)  │  │
                        │  │  ├─ ai_node   (SSH)   │  │
                        │  │  └─ dns_failover      │  │
                        │  └───────────────────────┘  │
                        │  ┌───────────────────────┐  │
                        │  │  控制面备用 Xray        │  │
                        │  │  (relay / 直出 双模式) │  │
                        │  └───────────────────────┘  │
                        └──────┬──────────┬───────────┘
                               │ SSH      │ SSH
                      ┌────────┘          │
                      ▼                   ▼
           ┌──────────────────┐  ┌──────────────────┐
           │   普通数据面      │  │    AI 节点        │
           │  64.186.224.96   │  │  (远端独立机器)   │
           │                  │  │                  │
           │  VLESS+REALITY   │  │  VLESS+REALITY   │
           │  panel-ports.json │  │  监听 AI_UPSTREAM │
           │  dynamic-routing │  │  _PORT           │
           │  ai_domain_mgr   │  │  freedom 直出     │
           │  access.log      │  │                  │
           └──────────────────┘  └──────────────────┘
```

正常流量路径：

```
客户端 ──→ 普通数据面 ──→ 直出（普通流量）
              └─AI域名─→ AI 节点 ──→ 直出（AI 流量）
```

## 前置条件

### 远端机器

- 独立公网 IP 的 Linux 服务器
- 开放 AI 节点监听端口（与 `AI_UPSTREAM_PORT` 一致）的入站 TCP
- 已安装 Xray（二进制或 Docker 容器均可）
- Python 3（控制面 SSH 远端脚本依赖）

### SSH 免密

控制面通过 SSH 管理数据面和 AI 节点。以数据面的现有 SSH 密钥为例：

```bash
# 在控制面生成密钥（如果还没有）
ssh-keygen -t ed25519 -f /root/.ssh/xray-control-plane_ed25519 -N ""

# 把公钥推到 AI 节点
ssh-copy-id -i /root/.ssh/xray-control-plane_ed25519.pub root@<ai-node-ip>

# 验证免密登录
ssh -i /root/.ssh/xray-control-plane_ed25519 root@<ai-node-ip> "echo ok"
```

### REALITY 参数复用

AI 节点复用普通数据面同一套 REALITY 参数，这些参数在 `app/xray/.env` 中定义：

| 参数 | 说明 |
| --- | --- |
| `XRAY_REALITY_PRIVATE_KEY` | REALITY 私钥 |
| `XRAY_REALITY_PUBLIC_KEY` | REALITY 公钥 |
| `XRAY_REALITY_SHORT_ID` | shortId |
| `XRAY_CLIENT_UUID` | 客户端 UUID |
| `XRAY_SERVER_NAME` | SNI |
| `XRAY_DEST` | 回落目标 |
| `XRAY_FLOW` | flow（通常 `xtls-rprx-vision`）|

无需为 AI 节点单独生成或配置这些参数。

## 环境变量配置

AI 节点的配置分为两组环境变量，分别在根 `.env` 和 `app/xray/.env` 中设置。

### 根 `.env` — AI 节点纳管变量

| 变量 | 说明 | 示例 |
| --- | --- | --- |
| `AI_NODE_SSH_TARGET` | AI 节点 SSH 目标 | `root@ai-node.example.com` |
| `AI_NODE_SSH_OPTIONS` | SSH 额外参数，按 shell words 解析 | `-o StrictHostKeyChecking=no -i /root/.ssh/xray-control-plane_ed25519` |
| `AI_NODE_CONTAINER_NAME` | AI 节点上 Xray 容器名（容器部署时填写） | `xray-ai-node` |
| `AI_NODE_RESTART_COMMAND` | 自定义重启命令（优先于容器名） | `systemctl restart xray` |
| `AI_NODE_CONFIG_PATH` | AI 节点上 `config.json` 的路径 | `/etc/xray/config.json` |
| `AI_NODE_API_SERVER` | AI 节点 Xray API 地址（默认同 `XRAY_API_SERVER`） | `127.0.0.1:10085` |
| `AI_NODE_PROBE_HOST` | AI 节点可达性探测目标 IP 或域名 | `ai-node.example.com` |

### `app/xray/.env` — AI 上游变量

| 变量 | 说明 | 示例 |
| --- | --- | --- |
| `AI_UPSTREAM_HOST` | AI 节点公网 IP 或域名 | `ai-node.example.com` |
| `AI_UPSTREAM_PORT` | AI 节点监听端口 | `27166` |

`AI_UPSTREAM_HOST:PORT` 是普通数据面 `dynamic-routing.json` 中 freedom redirect 的目标地址，必须指向 AI 节点的公网入口。

### 变量关系说明

```
AI_UPSTREAM_HOST:PORT  ──→  普通数据面 dynamic-routing.json 中的 redirect 目标
AI_NODE_SSH_TARGET     ──→  控制面 SSH 纳管 AI 节点的连接目标
AI_NODE_CONFIG_PATH    ──→  控制面推送 config-ai-node.json 到远端的路径
AI_NODE_PROBE_HOST     ──→  控制面周期性 TCP 探测 AI 节点的目标
```

通常 `AI_UPSTREAM_HOST` 和 `AI_NODE_SSH_TARGET` / `AI_NODE_PROBE_HOST` 指向同一台机器，但可以分别使用域名和 IP。

## 配置渲染

控制面复用 `render_config.py` 的 `build_server_config()` 渲染 AI 节点配置，参数差异：

| 参数 | 普通数据面 | AI 节点 |
| --- | --- | --- |
| `panel_ports` | 从 `panel-ports.json` 读取 | `[AI_UPSTREAM_PORT]`（单端口）|
| `dynamic_payload` | `dynamic-routing.json`（AI 域名路由）| `None`（无动态路由）|
| `relay_outbound` | `None` | `None`（freedom 直出）|

产出文件：`app/xray/runtime/config-ai-node.json`

### 手动渲染

```bash
python -m app.xray.render_config \
  --env-file app/xray/.env \
  --config-out app/xray/runtime/config.json \
  --client-out app/xray/runtime/client-test.json \
  --share-out app/xray/runtime/client-share.txt \
  --panel-ports-file app/xray/runtime/panel-ports.json \
  --ai-node-config-out app/xray/runtime/config-ai-node.json
```

目标态下，`render_xray_config()`（`app/state/base.py`）会在渲染普通数据面配置时同步渲染 AI 节点配置。

### SSH 推送流程

1. 控制面在本地渲染 `config-ai-node.json`
2. 通过 SSH 上传到 `AI_NODE_CONFIG_PATH`（先写临时文件，校验通过后原子替换）
3. 在远端执行 `xray run -test -config <path>` 校验
4. 校验通过后重启远端 Xray（容器 `docker restart` 或自定义命令）

推送逻辑复用 `DataPlaneController.sync_generated_files()` 和 `restart()`（`app/xray/node_control.py`）。

## 部署步骤

### 步骤 1：远端安装 Xray

在 AI 节点机器上安装 Xray：

```bash
# 方式一：官方安装脚本（二进制）
bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install

# 方式二：Docker
docker run -d --name xray-ai-node \
  --restart unless-stopped \
  --network host \
  -v /etc/xray:/etc/xray:ro \
  -v /var/log/xray:/var/log/xray \
  ghcr.io/xtls/xray-core:26.5.3 \
  run -c /etc/xray/config.json
```

### 步骤 2：配置 SSH 免密

```bash
# 在控制面上执行
ssh-copy-id -i /root/.ssh/xray-control-plane_ed25519.pub root@<ai-node-ip>
ssh root@<ai-node-ip> "echo ok"
```

### 步骤 3：填写环境变量

在控制面根 `.env` 中添加：

```env
AI_NODE_SSH_TARGET=root@<ai-node-ip>
AI_NODE_SSH_OPTIONS=-o StrictHostKeyChecking=no -i /root/.ssh/xray-control-plane_ed25519
AI_NODE_CONTAINER_NAME=xray-ai-node
AI_NODE_CONFIG_PATH=/etc/xray/config.json
AI_NODE_PROBE_HOST=<ai-node-ip>
```

> 如果使用 Docker 部署，填写 `AI_NODE_CONTAINER_NAME`；如果使用 systemd 二进制，填写 `AI_NODE_RESTART_COMMAND=systemctl restart xray`。

在 `app/xray/.env` 中确认：

```env
AI_UPSTREAM_HOST=<ai-node-ip>
AI_UPSTREAM_PORT=27166
```

### 步骤 4：控制面渲染并推送配置

```bash
# 渲染 AI 节点配置
python -m app.xray.render_config \
  --env-file app/xray/.env \
  --config-out app/xray/runtime/config.json \
  --client-out app/xray/runtime/client-test.json \
  --share-out app/xray/runtime/client-share.txt \
  --panel-ports-file app/xray/runtime/panel-ports.json \
  --ai-node-config-out app/xray/runtime/config-ai-node.json

# 目标态下，重启控制面后 maintenance_loop 会自动推送
# 手动触发（目标态 API）：
curl -u admin:secret -X POST http://127.0.0.1:18080/api/ai-node/restart
```

### 步骤 5：验证可达性

```bash
# 在控制面上验证 AI 节点端口可达
curl -s http://127.0.0.1:18080/api/ai-node/status | python3 -m json.tool

# 直接 TCP 探测
nc -zv <ai-node-ip> 27166
```

## 控制面纳管能力

### 配置生命周期

| 操作 | 触发方式 | 说明 |
| --- | --- | --- |
| 渲染 | 端口变更 / maintenance loop | 复用 `build_server_config()`，产出 `config-ai-node.json` |
| 推送 | 渲染后自动 | SSH 上传到 `AI_NODE_CONFIG_PATH`，校验后原子替换 |
| 校验 | 推送时 | 远端 `xray run -test -config` |
| 重启 | 校验通过后 | `docker restart` 或 `AI_NODE_RESTART_COMMAND` |

### 可达性探测

控制面在 `maintenance_loop` 中周期性 TCP 探测 `AI_NODE_PROBE_HOST:AI_UPSTREAM_PORT`，结果用于：

- 面板节点状态卡片展示
- DNS 故障切换时判断控制面备用应使用 relay 模式还是直出模式

### API 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/ai-node/status` | 获取 AI 节点状态（role、label、configured、reachable、xray_running、management_target、last_error）|
| `POST` | `/api/ai-node/restart` | 重启 AI 节点 Xray |

## 本地测试

仓库内置 `docker-compose.yml` 的 `ai-node` profile，用于在控制面本机测试 AI 节点配置渲染：

```bash
# 渲染 AI 节点配置
python -m app.xray.render_config \
  --env-file app/xray/.env \
  --config-out app/xray/runtime/config.json \
  --client-out app/xray/runtime/client-test.json \
  --share-out app/xray/runtime/client-share.txt \
  --panel-ports-file app/xray/runtime/panel-ports.json \
  --ai-node-config-out app/xray/runtime/config-ai-node.json

# 启动本地 AI 节点容器
docker compose --profile ai-node up -d xray-ai-node

# 查看日志
docker compose --profile ai-node logs -f xray-ai-node
```

> 本地测试模式下，`AI_NODE_SSH_TARGET` 留空，AI 节点模式为 `docker`。生产环境使用 SSH 模式。

## 故障行为

### 四种故障场景下 AI 节点的角色

| 场景 | 普通数据面 | AI 节点 | 控制面备用 | DNS 指向 | AI 节点行为 |
| --- | --- | --- | --- | --- | --- |
 | ① 正常 | 运行中 | 运行中 | 待命 | primary | 承接 AI 流量 |
 | ② AI 节点故障 | 运行中 | 故障 | 待命 | primary（不变）| 不可达，流量回退 |
 | ③ 数据面故障 | 故障 | 运行中 | 接管（relay）| backup | 承接 relay 流量 |
 | ④ 双节点故障 | 故障 | 故障 | 接管（直出）| backup | 不可达，备用直出 |

### AI 节点故障的回退机制（场景②）

AI 节点故障**不涉及 DNS 切换**，完全由 `ai_domain_manager` 已有的探测逻辑处理：

1. `ai_domain_manager` 每轮通过 `select_ai_target()`（`app/xray/ai_domain_manager.py:1189`）对 `AI_UPSTREAM_HOST:PORT` 做 TCP 探测
2. 当所有 AI 上游都不可达时，`should_fallback_to_primary_route()`（`ai_domain_manager.py:1183`）返回 `True`
3. 删除 `dynamic-routing.json`（`ai_domain_manager.py:1675`）
4. `route_status` 标记为 `fallback_to_primary`（`ai_domain_manager.py:1676`）
5. 重新渲染数据面配置（不含 AI 路由），重启数据面
6. AI 域名流量回退到数据面的 freedom outbound 直出

AI 节点恢复后，下一轮 `ai_domain_manager` 探测到可达，重新生成 `dynamic-routing.json`，AI 流量恢复转发到 AI 节点。

### 数据面故障时的 AI 节点角色（场景③）

数据面故障触发 DNS 切换到控制面备用。此时控制面备用以 relay 模式运行，将所有流量转发到 AI 节点。详见 [dns-failover.md](dns-failover.md)。

### 双节点故障时的处理（场景④）

当数据面和 AI 节点同时故障时：

1. DNS 切到控制面备用（与场景③相同）
2. 控制面探测到 AI 节点不可达
3. 自动重新渲染 `config-backup.json` 为 freedom 直出模式
4. 重启控制面备用 Xray
5. 所有流量从控制面备用直接出去

AI 节点恢复后，控制面自动切回 relay 模式。详见 [dns-failover.md](dns-failover.md)。

## 验证清单

部署完成后逐项验证：

```bash
# 1. SSH 免密
ssh root@<ai-node-ip> "echo ok"

# 2. AI 节点配置已推送
ssh root@<ai-node-ip> "cat /etc/xray/config.json | python3 -m json.tool"

# 3. Xray 进程运行
ssh root@<ai-node-ip> "docker ps | grep xray-ai-node"
# 或
ssh root@<ai-node-ip> "systemctl is-active xray"

# 4. 端口监听
nc -zv <ai-node-ip> 27166

# 5. 控制面纳管状态
curl -s -u admin:secret http://127.0.0.1:18080/api/ai-node/status | python3 -m json.tool

# 6. 面板首页节点状态卡片
# 浏览器访问 http://控制面IP:18080/ 查看「节点状态」卡片
```

## 排障

### SSH 不通

```bash
# 检查 SSH 连接
ssh -v root@<ai-node-ip>

# 常见原因：
# - 公钥未推送到远端
# - AI_NODE_SSH_OPTIONS 中密钥路径错误
# - 远端防火墙未开放 22 端口
```

### 配置推送失败

```bash
# 查看控制面日志
docker compose logs -f xray-routing-panel | grep -i "ai.*node"

# 常见原因：
# - AI_NODE_CONFIG_PATH 所在目录不存在
# - 远端磁盘空间不足
# - 远端 Python3 不可用（推送脚本依赖 Python3）
```

### 端口未监听

```bash
# 在 AI 节点上检查
ss -tlnp | grep 27166

# 常见原因：
# - Xray 进程未运行
# - config-ai-node.json 中监听端口与 AI_UPSTREAM_PORT 不一致
# - 防火墙未开放端口
```

### 面板显示 AI 节点不可达

```bash
# 检查 AI_NODE_PROBE_HOST 是否正确
# 控制面 TCP 探测 AI_NODE_PROBE_HOST:AI_UPSTREAM_PORT
nc -zv <ai-node-probe-host> 27166

# 如果 AI 节点在 NAT 后面，确保 AI_NODE_PROBE_HOST 是公网入口地址
```

### AI 流量未转发到 AI 节点

```bash
# 检查数据面的 dynamic-routing.json 是否存在
ssh root@<数据面IP> "cat /app/xray/runtime/dynamic-routing.json"

# 如果不存在，说明 AI 域名管理器探测到 AI 节点不可达
# 检查 AI_UPSTREAM_HOST:PORT 是否指向正确的 AI 节点入口
# 查看 AI 域名管理器日志
docker compose --profile xray logs -f xray-ai-domain-manager | grep "fallback_to_primary"
```
