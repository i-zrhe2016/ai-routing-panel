# Xray VLESS + XTLS + REALITY

这个目录是直接纳入 `app/` 的 Xray REALITY 子系统，和当前 `xray-routing-panel` 共享仓库与 compose，但保持独立运行边界。

说明：

- 服务定义已经合并进仓库根目录 `docker-compose.yml` 的 `xray` profile
- 当前目录是 `app.xray` 子系统，包含模块入口、默认资源和运行目录
- 正式入口统一使用 `python -m app.xray.render_config` 和 `python -m app.xray.ai_domain_manager`
- 推荐在仓库根目录执行 `docker compose --profile xray ...`
- `app/xray/.env` 主要用于渲染 Reality 配置和给 AI 管理器提供运行参数；`xray-reality` 服务镜像版本当前直接写在根目录 `docker-compose.yml`

它默认使用：

- 协议：`VLESS`
- 传输：`TCP`
- flow：`xtls-rprx-vision`
- 安全：`REALITY`
- 启动方式：`docker compose`
- 网络模式：`host`
- 容器用户：`root`，用于绑定低位端口并写入挂载日志目录
- AI 域名管理：每小时分析最近一小时访问域名，生成动态路由规则，并把 AI 域名结果写入共享 `panel.db`

## 前提

- 宿主机已安装 Docker 和 Docker Compose
- 服务端监听端口默认是 `443`，启动前确认没有被其他服务占用
- `XRAY_DEST` 和 `XRAY_SERVER_NAME` 应指向一个正常可访问的 HTTPS 站点

检查 `443` 是否空闲：

```bash
ss -ltn 'sport = :443'
```

## 快速开始

1. 进入目录：

```bash
cd /path/to/xray-routing-panel
```

2. 生成一组新的 UUID、REALITY 密钥和 short id：

```bash
./app/xray/generate-secrets.sh
```

3. 复制环境变量模板并填入参数：

```bash
cp app/xray/.env.example app/xray/.env
```

至少需要修改这些值：

- `XRAY_PUBLIC_HOST`
- `XRAY_CLIENT_UUID`
- `XRAY_REALITY_PRIVATE_KEY`
- `XRAY_REALITY_PUBLIC_KEY`
- `XRAY_REALITY_SHORT_ID`
- `XRAY_SERVER_NAME`
- `XRAY_DEST`

如果前面还有一层入口转发，例如通过根目录 `xray-routing-panel` 暴露 `31098` 再转到本机 `443`：

- 保持 `XRAY_LISTEN_PORT=443`
- 额外设置 `XRAY_PUBLIC_PORT=31098`

4. 渲染服务端配置和客户端连接信息：

```bash
python -m app.xray.render_config
```

5. 启动服务：

```bash
docker compose --profile xray up -d --build xray-reality xray-ai-domain-manager
```

启动后会同时拉起：

- `xray-reality`：代理服务本体
- `xray-ai-domain-manager`：每小时读取 `access.log`，优先调用本机 `codex` 识别域名，必要时再回退到 OpenAI API，维护动态规则，并在配置变化后重启 `xray-reality`
- 如果你把数据面独立部署到远端，可以改为配置 `DATAPLANE_SSH_TARGET` / `DATAPLANE_RESTART_COMMAND`，让控制面统一下发和重启

## 生成产物

执行渲染脚本后会生成：

- `runtime/config.json`：Xray 服务端配置
- `runtime/client-share.txt`：可直接导入的 `vless://` 链接
- `runtime/client-test.json`：本地 `xray` 客户端测试配置

运行期会生成：

- `logs/access.log`：Xray 访问日志
- `runtime/ai-domain-decisions.json`：域名分类结果缓存
- `runtime/dynamic-routing.json`：根据 AI 域名生成的动态路由片段
- `reports/hourly-domains/latest.json`：最近一小时域名报告
- `reports/hourly-domains/latest.txt`：文本版最近一小时域名报告
- `reports/hourly-domains/history/*`：按小时归档的历史报告
- `runtime/codex-home/`：容器运行时复制的本机 Codex 配置和认证文件
- `data/panel.db`：共享面板数据库，管理器会在其中维护 `ai_domains` 和 `ai_domain_observations`

本机 `codex` 识别默认依赖：

- 宿主机已安装 `codex` CLI
- 宿主机 `codex login status` 可用
- 根目录 `docker-compose.yml` 里默认挂载了宿主机 `/root/.codex` 和 `/root/.nvm/versions/node`

如果你的 `codex` 不在这些默认路径：

- 改根目录 `docker-compose.yml` 里的挂载路径
- 或者在 `.env` 里设置 `CODEX_CLI_JS` / `CODEX_BIN`

仓库当前已经带有一份可直接使用的 `ai-proxy-outbound.json`：

- 命中的 AI 域名会通过 `VLESS + Reality` 出站转发到 `AI_UPSTREAM_HOST:AI_UPSTREAM_PORT`
- 当前默认上游仍是 `upstream.example.com:27166`
- 如果配置了多个 AI 上游，管理器会在每轮执行时按顺序做 TCP 探测，首个不可达时自动切换到下一个可达上游
- 模板里的 UUID、`flow`、`publicKey`、`shortId` 代表共享的 AI 后端参数；多上游模式默认只切换地址和端口，如果不同上游需要不同凭据，你也需要同步调整模板

如果你删除这个文件，或者把 `AI_PROXY_OUTBOUND_TEMPLATE_PATH` 指向不存在的路径，管理器才会回退到内建的 Xray `freedom redirect`。

如果你想切换成别的上游代理协议，或者只想替换当前 AI 后端参数，直接编辑：

- `ai-proxy-outbound.json`

如果你想从通用样板重新开始，仓库还提供了：

```bash
cp app/xray/assets/ai-proxy-outbound.example.json app/xray/ai-proxy-outbound.json
```

管理器会自动把：

- `__AI_UPSTREAM_HOST__`
- `__AI_UPSTREAM_PORT__`
- `__PANEL_UPSTREAM_HOST__`
- `__PANEL_UPSTREAM_PORT__`
- `__PANEL_LISTEN_PORT__`

替换成当前 AI 上游和当前面板规则对应的值。

如果要配置多个 AI 上游，推荐两种方式二选一：

- 保留 `AI_UPSTREAM_HOST` / `AI_UPSTREAM_PORT` 作为主上游，再用 `AI_UPSTREAM_FALLBACKS` 追加备用上游
- 直接使用 `AI_UPSTREAMS` 写完整优先级列表；设置后会覆盖 `AI_UPSTREAM_HOST` / `AI_UPSTREAM_PORT`

如果第二上游不是同一套 Reality 参数，而是另一条完整的分享链接：

- 使用 `AI_UPSTREAM_FALLBACK_URL`
- 当前支持直接粘贴 `vless://...` 的 TCP + Reality 链接
- 切换到这个备用上游时，管理器会直接使用链接里的 UUID、`pbk`、`sid`、`sni`、`spx`、`pqv`

列表格式支持逗号或换行分隔，例如：

```env
AI_UPSTREAM_HOST=upstream.example.com
AI_UPSTREAM_PORT=27166
AI_UPSTREAM_FALLBACKS=backup-1.example.com:27166,backup-2.example.com:27166
AI_UPSTREAM_PROBE_TIMEOUT_SECONDS=3
```

或者：

```env
AI_UPSTREAMS=upstream.example.com:27166,backup-1.example.com:27166,backup-2.example.com:27166
AI_UPSTREAM_PROBE_TIMEOUT_SECONDS=3
```

如果备用上游参数不同，可以这样写：

```env
AI_UPSTREAM_HOST=upstream.example.com
AI_UPSTREAM_PORT=27166
AI_UPSTREAM_FALLBACK_URL=vless://uuid@backup.example.com:42994?encryption=none&security=reality&sni=www.amazon.com&fp=chrome&pbk=replace_me&sid=2f8deb&spx=%2Fpath&type=tcp
AI_UPSTREAM_PROBE_TIMEOUT_SECONDS=3
```

注意：多上游切换是在 `xray-ai-domain-manager` 每轮执行时完成的；如果你希望切换更快，把 `AI_DOMAIN_INTERVAL_SECONDS` 调小即可。

如果你把完整数据面独立部署到远端，而不是在本机 compose 里直接重启本地容器，可以额外配置：

- `DATAPLANE_SSH_TARGET`
- `DATAPLANE_SSH_OPTIONS`
- `DATAPLANE_CONTAINER_NAME`
- `DATAPLANE_RESTART_COMMAND`
- `DATAPLANE_CONFIG_PATH`

这样当动态路由重渲染后，管理器会只对唯一数据面执行重启。

## 常用命令

启动或重建：

```bash
docker compose --profile xray up -d --build xray-reality xray-ai-domain-manager
```

停止：

```bash
docker compose --profile xray stop xray-reality xray-ai-domain-manager
```

查看状态：

```bash
docker compose --profile xray ps xray-reality xray-ai-domain-manager
```

查看日志：

```bash
docker compose --profile xray logs -f xray-reality
docker compose --profile xray logs -f xray-ai-domain-manager
tail -f app/xray/logs/access.log app/xray/logs/error.log
```

校验配置：

```bash
docker compose --profile xray run --rm xray-reality run -test -config /etc/xray/config.json
```

查看最近一小时域名报告：

```bash
cat app/xray/reports/hourly-domains/latest.txt
sed -n '1,220p' app/xray/reports/hourly-domains/latest.json
```

检查 AI 域名是否已经写入共享数据库：

```bash
python3 - <<'PY'
import sqlite3
conn = sqlite3.connect('./data/panel.db')
for row in conn.execute('select domain, classification, total_hits from ai_domains order by domain'):
    print(row)
PY
```

手动跑一轮 AI 域名分析：

```bash
docker compose --profile xray run --rm xray-ai-domain-manager python -m app.xray.ai_domain_manager --once
```

如果你想把“未分类域名”先做 Google 搜索，再交给 OpenRouter 的 `openai/gpt-5-nano` 按搜索结果判断，可以单独启动仓库自带的 MCP server：

```bash
python -m app.xray.google_search_mcp
```

它默认读取：

- `reports/hourly-domains/latest.json`
- `runtime/ai-domain-decisions.json`
- 启动时还会自动读取仓库根目录 `.env` 和当前目录 `.env`

并提供：

- `collect_uncategorized_domains`
- `search_domains_with_google`
- `classify_domains_with_google`

Google 搜索层直接抓取 Google 搜索结果页，不需要 Google Search API key；只需要配置 OpenRouter：

```bash
export OPENROUTER_API_KEY=...
export OPENROUTER_MODEL=openai/gpt-5-nano
```

手动验证 `chatgpt.com` 是否命中 `ai_proxy`：

```bash
docker run --rm --network host \
  -v "$(pwd)/app/xray/runtime/client-test.json:/etc/xray/config.json:ro" \
  ghcr.io/xtls/xray-core:26.5.3 run -c /etc/xray/config.json
```

另开一个终端执行：

```bash
curl --socks5-hostname 127.0.0.1:10808 -I https://chatgpt.com
tail -n 20 app/xray/logs/access.log
```

命中时，`logs/access.log` 里应看到类似：

```text
accepted tcp:chatgpt.com:443 [ai_proxy]
```

## 参数说明

| 变量名 | 说明 |
| --- | --- |
| `XRAY_IMAGE` | Xray 镜像，默认 `ghcr.io/xtls/xray-core:26.5.3` |
| `XRAY_LISTEN_HOST` | 服务端监听地址，通常保持 `0.0.0.0` |
| `XRAY_LISTEN_PORT` | 服务端监听端口，默认 `443` |
| `XRAY_PUBLIC_HOST` | 客户端连接时使用的公网 IP 或域名 |
| `XRAY_PUBLIC_PORT` | 客户端连接时使用的对外端口；留空时默认等于 `XRAY_LISTEN_PORT`，走 `xray-routing-panel` 时可设为 `31098` |
| `XRAY_CLIENT_UUID` | VLESS 用户 UUID |
| `XRAY_FLOW` | 建议保持 `xtls-rprx-vision` |
| `XRAY_REALITY_PRIVATE_KEY` | REALITY 私钥 |
| `XRAY_REALITY_PUBLIC_KEY` | REALITY 公钥，用于客户端连接信息 |
| `XRAY_REALITY_SHORT_ID` | REALITY short id，建议 16 位十六进制 |
| `XRAY_SERVER_NAME` | 客户端 SNI |
| `XRAY_DEST` | REALITY `dest`，形如 `www.cloudflare.com:443` |
| `XRAY_FINGERPRINT` | 客户端指纹，默认 `chrome` |
| `XRAY_LOGLEVEL` | 日志级别 |
| `XRAY_NODE_TAG` | 分享链接备注名称 |
| `AI_UPSTREAM_HOST` | AI 主上游主机，默认 `upstream.example.com` |
| `AI_UPSTREAM_PORT` | AI 主上游端口，默认 `27166` |
| `AI_UPSTREAM_FALLBACK_URL` | 可选；第二上游的完整 `vless://` Reality 链接，适用于备用上游有独立 UUID / `pbk` / `sid` / `sni` |
| `AI_UPSTREAM_FALLBACKS` | 可选；在主上游后追加备用上游列表，格式为逗号或换行分隔的 `host[:port]` |
| `AI_UPSTREAMS` | 可选；完整覆盖 AI 上游优先级列表，格式为逗号或换行分隔的 `host:port` |
| `AI_UPSTREAM_PROBE_TIMEOUT_SECONDS` | AI 上游 TCP 探测超时，默认 `3` 秒 |
| `PANEL_DB_PATH` | 共享面板数据库路径，默认 `/panel-data/panel.db` |
| `AI_PROXY_OUTBOUND_TEMPLATE_PATH` | AI 专用出站模板路径，默认 `/workspace/ai-proxy-outbound.json` |
| `AI_DOMAIN_INTERVAL_SECONDS` | AI 域名分析执行周期，默认 `3600` 秒 |
| `AI_DOMAIN_LOOKBACK_SECONDS` | 每轮统计窗口长度，默认 `3600` 秒 |
| `AI_DOMAIN_BATCH_SIZE` | 单批送给分类器的最大域名数，默认 `50` |
| `CODEX_CLASSIFIER_ENABLED` | 是否优先启用本机 `codex` 做未知域名分类，默认 `1` |
| `CODEX_TIMEOUT_SECONDS` | 调用本机 `codex` 的超时时间，默认 `180` 秒 |
| `CODEX_MODEL` | 可选；指定 `codex exec --model` |
| `OPENAI_API_KEY` | 可选；OpenAI 兼容接口凭据，本地模型通常可留空 |
| `OPENAI_MODEL` | 回退分类时使用的模型名，默认 `gpt-5.5` |
| `OPENAI_BASE_URL` | OpenAI 兼容接口地址；默认 `https://api.openai.com/v1/responses` |
| `OPENAI_ALLOW_NO_KEY` | 是否允许未设置 Key 时仍调用兼容接口；本地接口常设为 `1` |
| `PANEL_ROUTE_LISTEN_PORT` | 可选；指定面板监听端口时，模板里的 `__PANEL_*__` 占位符优先使用该端口对应的上游 |

AI 域名管理服务默认使用：

- `logs/access.log` 作为输入日志
- `reports/hourly-domains` 作为输出目录
- `runtime/ai-domain-decisions.json` 保存分类缓存
- `runtime/codex-home` 保存容器内调用本机 `codex` 所需的复制配置
- `runtime/dynamic-routing.json` 保存动态路由片段
- `data/panel.db` 保存 AI 域名聚合表和窗口观察表
- 每 `3600` 秒刷新一次
- 每次统计最近 `3600` 秒窗口
- 优先走本机 `codex exec`
- `OPENAI_MODEL` 默认是 `gpt-5.5`，也可以改成你自己的本地推理模型

共享数据库会新增两张表：

- `ai_domains`：按域名聚合的当前 AI 分类、最近命中时间、累计命中次数
- `ai_domain_observations`：每个统计窗口的 AI 域名命中历史

`reports/hourly-domains/latest.txt` 中会额外显示：

- `panel_db_status`

用来确认当轮 AI 域名结果是否已经成功写入数据库。

如果本机 `codex` 可用：

- 未命中内建列表的未知域名会优先交给本机 `codex` 判定

如果本机 `codex` 不可用：

- 管理器会继续输出域名报告
- 并自动回退到配置好的 OpenAI 兼容接口

本地大模型示例：

```env
OPENAI_BASE_URL=http://host.docker.internal:11434/v1
OPENAI_MODEL=qwen2.5:14b
OPENAI_ALLOW_NO_KEY=1
```

说明：

- 容器访问宿主机本地模型应使用 `host.docker.internal`
- 对 `Ollama`、`LM Studio`、`vLLM` 这类兼容 `chat/completions` 的服务，只填 `/v1` 根地址即可
- 如果 `OPENAI_BASE_URL` 明确写成 `/v1/responses`，管理器会继续按 OpenAI Responses API 调用

如果既没有可用的本机 `codex`，也没有可用的 OpenAI 兼容接口：

- 管理器仍会生成域名报告
- 内建已知 AI 域名列表仍会自动命中，例如 `openai.com`、`chatgpt.com`、`anthropic.com`、`claude.ai`
- 其他未知域名不会自动得到 AI / 非 AI 分类

如果没有配置 `ai-proxy-outbound.json`：

- 管理器会直接使用内建 `freedom redirect`，把 AI 域名请求改发到 `AI_UPSTREAM_HOST:AI_UPSTREAM_PORT`

如果你创建了 `ai-proxy-outbound.json` 但内容不合法：

- 管理器会继续识别 AI 域名
- 但不会把错误模板应用到 `xray` 路由里

说明：

- 这里的“域名”来自 Xray `access.log` 里的目标域名，例如 `www.microsoft.com`
- 能拿到的是目标域名，不是完整 URL
- 如果目标是纯 IP，报告里不会计入“域名列表”

## 轮换密钥

如果需要更换 UUID 或 REALITY 密钥：

1. 重新执行 `./app/xray/generate-secrets.sh`
2. 更新 `app/xray/.env`
3. 重新执行 `python -m app.xray.render_config`
4. 执行 `docker compose --profile xray up -d xray-reality xray-ai-domain-manager`

客户端也需要同步更新新的参数。
