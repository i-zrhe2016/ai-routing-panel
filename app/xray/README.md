# Xray / AI 路由子系统

这个目录仍然包含 `app.xray` 子系统代码和运行产物，但详细文档已经迁入仓库根目录 `docs/`。

## 权威文档

- [../../docs/ai-routing.md](../../docs/ai-routing.md)：AI 路由主链路、上游选择、MCP 工具
- [../../docs/configuration.md](../../docs/configuration.md)：`app/xray/.env` 变量说明
- [../../docs/development.md](../../docs/development.md)：本地启动和常用命令
- [../../docs/architecture.md](../../docs/architecture.md)：控制面 / 数据面 / AI 路由边界

## 目录提示

- `render_config.py`：渲染 `config.json`、`client-test.json` 和分享链接
- `ai_domain_manager.py`：域名分类、动态路由、小时报表
- `google_search_mcp.py`：辅助归类用 MCP server
- `runtime/`：渲染产物和运行时缓存
- `reports/`：小时域名报告

如果你是从旧 README 跳转过来，请以 `docs/` 下的文档为准。
