# 开发与启动

## 前置条件

- Linux 宿主机
- Docker 和 Docker Compose
- 如果启用完整数据面，确认 `443` 未被其他进程占用
- 如果启用 Codex 域名分类，宿主机需要可用的 `codex` CLI 登录态

## 推荐本地路径：完整栈

```bash
./app/xray/generate-secrets.sh
cp app/xray/.env.example app/xray/.env
cp .env.example .env
python -m app.xray.render_config
docker compose --profile xray up -d --build
```

这会启动：

- `xray-routing-panel`
- `xray-routing-panel-db-backup`
- `xray-reality`
- `xray-ai-domain-manager`

## 只启动面板

```bash
docker compose up -d --build
```

适合先验证 UI、数据库和租户流程。此模式下：

- 面板仍会渲染 Xray 配置文件
- 如果没有单独运行的数据面，端口不会真正承载流量
- `/healthz` 如需仅检查面板本身，设置 `PANEL_HEALTH_REQUIRES_XRAY=0`

## 本地二进制 / 外部 Xray

如果你不使用 compose 里的 `xray-reality` 容器：

- 设置 `DATAPLANE_LOCAL_BIN=/path/to/xray`
- 设置 `DATAPLANE_API_SERVER=127.0.0.1:10085`
- 让外部 Xray 进程自行加载 `app/xray/runtime/config.json`

注意：

- 面板可以渲染并执行 `xray run -test`
- 面板不会替你守护或重启这个外部进程

## 远端控制面 / 数据面分离

至少配置：

- `DATAPLANE_SSH_TARGET`
- `DATAPLANE_CONFIG_PATH`
- `DATAPLANE_PANEL_PORTS_PATH`
- `DATAPLANE_ACCESS_LOG_PATH`

按需补充：

- `DATAPLANE_DYNAMIC_ROUTING_PATH`
- `DATAPLANE_AI_REPORT_PATH`
- `DATAPLANE_PANEL_DB_PATH`
- `DATAPLANE_RESTART_COMMAND`
- `DATAPLANE_CONTAINER_NAME`

远端模式下，探针目标不要继续使用本地回环：

- 把 `DATAPLANE_PROBE_HOST` 设置成远端入口 IP 或域名

## 常用命令

渲染配置：

```bash
python -m app.xray.render_config
```

查看完整栈状态：

```bash
docker compose --profile xray ps
```

查看面板日志：

```bash
docker compose logs -f xray-routing-panel
```

查看数据面日志：

```bash
docker compose --profile xray logs -f xray-reality
```

手动跑一轮 AI 域名分析：

```bash
docker compose --profile xray run --rm xray-ai-domain-manager python -m app.xray.ai_domain_manager --once
```

仅直接启动面板进程：

```bash
python app/panel.py
```

这会调用 `app/web.py:main()`，启动维护线程并监听 `PANEL_HOST:PANEL_PORT`。
