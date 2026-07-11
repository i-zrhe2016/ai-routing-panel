# AI 路由

## 主链路

AI 路由由 `xray-ai-domain-manager` 驱动（运行在普通数据面上），默认流程如下：

1. 从 `app/xray/logs/access.log` 读取最近一小时访问域名
2. 先应用内建 AI 域名规则
3. 对未知域名优先调用本机 `codex`
4. 如 `codex` 不可用，再回退到 OpenAI 兼容接口
5. 生成动态路由、小时报表和数据库聚合结果
6. 路由变化时重新渲染并重启数据面

AI 域名流量最终通过 `dynamic-routing.json` 中的 freedom redirect 转发到 AI 节点（远端独立 Xray），由 AI 节点 freedom 直出。AI 节点不可达时自动回退到数据面直出，详见下方"AI 上游选择"和 [ai-node-deployment.md](ai-node-deployment.md)。

## 输入与输出

输入：

- `app/xray/logs/access.log`
- `app/xray/.env`
- 可选 `app/xray/ai-proxy-outbound.json`

输出：

- `app/xray/runtime/ai-domain-decisions.json`
- `app/xray/runtime/dynamic-routing.json`
- `app/xray/reports/hourly-domains/latest.json`
- `app/xray/reports/hourly-domains/latest.txt`
- `data/panel.db` 中的 `ai_domains` 和 `ai_domain_observations`

## AI 上游选择

AI 上游即 AI 节点的公网入口地址。常见配置方式有两种：

- 主上游 + 追加备用：
  - `AI_UPSTREAM_HOST`
  - `AI_UPSTREAM_PORT`
  - `AI_UPSTREAM_FALLBACKS`
- 直接提供完整优先级列表：
  - `AI_UPSTREAMS`

如果备用上游不是同一套 Reality 参数，而是另一条完整分享链接：

- 使用 `AI_UPSTREAM_FALLBACK_URL`

当 AI 节点被控制面纳管（`AI_NODE_SSH_TARGET` 已配置）时，`AI_UPSTREAM_HOST` / `AI_UPSTREAM_PORT` 应指向 AI 节点公网入口，控制面会自动派生 `CONTROL_PLANE_BACKUP_UPSTREAM_URL` 供 DNS 故障切换 relay 模式使用。

管理器会按顺序做 TCP 探测，首个不可达时切换到下一个可达上游。

如果所有 AI 上游都不可达：

- 不再下发 `ai_proxy` 动态路由
- 删除 `dynamic-routing.json`（`ai_domain_manager.py:1675`）
- 已命中的 AI 域名会回退到主链路流量（数据面 freedom 直出）
- 报表中的 `route_status` 会标记为 `fallback_to_primary`
- 回退判断由 `should_fallback_to_primary_route()`（`ai_domain_manager.py:1183`）完成
- **此回退不涉及 DNS 切换**

AI 节点恢复后，下一轮探测到可达，重新生成 `dynamic-routing.json`，AI 流量恢复转发到 AI 节点。

## 代理模板

仓库默认提供：

- `app/xray/ai-proxy-outbound.json`

模板中的这些占位符会在运行时替换：

- `__AI_UPSTREAM_HOST__`
- `__AI_UPSTREAM_PORT__`
- `__PANEL_UPSTREAM_HOST__`
- `__PANEL_UPSTREAM_PORT__`
- `__PANEL_LISTEN_PORT__`

如果模板不存在，管理器会回退到内建 `freedom redirect`。

## Codex / OpenAI 兼容分类器

默认 compose 会挂载宿主机这些路径，以便容器调用本机 `codex`：

- `/root/.codex`
- `/root/.nvm/versions/node`

如果你的环境不是这些路径：

- 调整 `docker-compose.yml` 中的挂载
- 或在 `app/xray/.env` 中设置 `CODEX_CLI_JS` / `CODEX_BIN`

如果没有可用的 `codex` 或 OpenAI 兼容接口：

- 内建已知 AI 域名仍会命中
- 未知域名不会自动得到 AI / 非 AI 分类

## MCP 工具

仓库自带一个辅助 MCP server：

```bash
python -m app.xray.google_search_mcp
```

它不是主链路的自动步骤，只用于辅助人工或半自动归类。默认提供：

- `collect_uncategorized_domains`
- `search_domains_with_google`
- `classify_domains_with_google`

Google 搜索层直接抓取搜索结果页，不依赖 Google Search API；分类默认使用 OpenRouter 上的 `openai/gpt-5-nano`。

## 常用命令

手动跑一轮 AI 域名分析：

```bash
docker compose --profile xray run --rm xray-ai-domain-manager python -m app.xray.ai_domain_manager --once
```

查看 AI 管理器日志：

```bash
docker compose --profile xray logs -f xray-ai-domain-manager
```

查看最新报告：

```bash
cat app/xray/reports/hourly-domains/latest.txt
sed -n '1,220p' app/xray/reports/hourly-domains/latest.json
```
