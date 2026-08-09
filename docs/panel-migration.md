# 面板迁移文档

本文档用于把当前这套 `xray-routing-panel` 从旧机器迁移到新机器。

> 当前版本不再使用 `nginx`。入口端口由 Xray 直接监听，面板负责维护数据库、渲染 Xray 配置并在需要时重启 `xray-reality`。

## 迁移目标

- 保留端口规则、租户凭据和订阅 token
- 保留管理员配置
- 可选保留历史统计和 AI 域名聚合结果
- 在新机器恢复完整的 Xray + 面板运行链路

## 需要迁移的内容

最关键的是这些文件或目录：

- `data/panel.db`
- `app/xray/.env`
- `.env`，如果你启用了 `PANEL_PUBLIC_URL`、管理员认证或自定义 `PANEL_SECRET_KEY`
- 如果已经拆成控制面 / 数据面分离，也要保留 `.env` 里新增的远程节点配置：
  - `DEFAULT_NODE_*`
  - `AI_NODE_*`

按需迁移：

- `app/xray/ai-proxy-outbound.json`
  - 如果你自定义过 AI 出站模板
- `app/xray/runtime/ai-domain-decisions.json`
  - 如果你想保留已分类域名缓存
- `app/xray/runtime/dynamic-routing.json`
  - 一般可重新生成；只在你想保留当前路由快照时一并带走
- `app/xray/logs/access.log`
  - 只在你想让 AI 管理器在新机器继续参考最近访问窗口时保留
- `backups/`
  - 只在你想顺带迁移历史数据库备份时保留

通常**不需要**迁移这些生成文件：

- `app/xray/runtime/config.json`
- `app/xray/runtime/client-test.json`
- `app/xray/runtime/client-share.txt`
- `app/xray/runtime/panel-ports.json`

这些文件都可以在新机器上重新渲染生成。

## 推荐迁移方式

如果旧机器不可用但 npm 灾备归档仍可访问，先按[灾备归档与 npm 上传通道](disaster-backup.md)在隔离目录下载并校验 `*-disaster-*.tar.gz`，再把其中的 `database/panel.db`、`config/` 下控制面文件和 `nodes/` 下两个数据面主配置带入下述迁移步骤。远端节点文件只作为实际状态参考，恢复前仍需重新渲染、校验并人工确认目标路径。该路径是人工离线灾难恢复，不承诺快速恢复时间。

### 方案 A：完整迁移

适合想保留：

- 端口规则
- 租户凭据和订阅地址
- 累计统计
- AI 域名聚合结果
- 最近分类缓存

建议打包：

- `data`
- `app/xray/.env`
- `.env`
- `app/xray/ai-proxy-outbound.json`
- `app/xray/runtime/ai-domain-decisions.json`
- `app/xray/logs`

### 方案 B：只迁核心配置

适合只想恢复服务，不关心最近缓存和报告。

至少复制：

- `data/panel.db`
- `app/xray/.env`
- `.env`，如果你改过

## 迁移步骤

### 1. 在旧机器停服务

```bash
docker compose down
```

建议先停服务，再打包数据，避免数据库和运行期文件不一致。

### 2. 备份旧机器数据

最小示例：

```bash
tar -czf xray-routing-panel-backup.tar.gz \
  data \
  app/xray/.env \
  .env
```

如果要一并保留分类缓存、日志和自定义出站模板：

```bash
tar -czf xray-routing-panel-full-backup.tar.gz \
  data \
  .env \
  app/xray/.env \
  app/xray/ai-proxy-outbound.json \
  app/xray/runtime/ai-domain-decisions.json \
  app/xray/logs
```

### 3. 在新机器准备环境

- 拉取同一份项目代码
- 安装 Docker 和 Docker Compose
- 确认 `18080`、`443` 和你实际使用的入口端口没有冲突
- 如果你依赖 `codex` 自动分类，确认新机器上的挂载路径也可用：
  - `/root/.codex`
  - `/root/.nvm/versions/node`
  - 或者同步修改 `docker-compose.yml`

### 4. 恢复备份

```bash
tar -xzf xray-routing-panel-backup.tar.gz
```

如果你只迁移最关键数据，也可以手工复制：

- `./data/panel.db`
- `./app/xray/.env`
- `./.env`

### 5. 在新机器重新渲染配置

```bash
python -m app.xray.render_config
```

### 6. 启动新环境

完整栈：

```bash
docker compose --profile xray up -d --build
```

如果你当前只想先恢复面板：

```bash
docker compose up -d --build
```

## 迁移后验证

```bash
docker compose ps
docker compose logs -f xray-routing-panel
curl http://127.0.0.1:18080/healthz
```

完整栈建议再检查：

```bash
docker compose --profile xray logs -f xray-reality
docker compose --profile xray logs -f xray-ai-domain-manager
```

同时确认这些文件已经生成：

- `app/xray/runtime/config.json`
- `app/xray/runtime/client-test.json`
- `app/xray/runtime/panel-ports.json`

如果你保留了 AI 分类状态，可以再检查：

```bash
cat app/xray/reports/hourly-domains/latest.txt
python3 - <<'PY'
import sqlite3
conn = sqlite3.connect('./data/panel.db')
for row in conn.execute('select domain, classification, total_hits from ai_domains order by domain'):
    print(row)
PY
```

## 常见问题

- 只复制 `panel.db` 可以吗
  - 可以，端口规则、租户凭据、订阅 token 和历史累计统计都在里面
  - 但 `app/xray/.env` 不同步的话，新机器上的 REALITY 参数可能不一致
- `app/xray/runtime/*` 要不要带走
  - 一般不需要，重新执行 `python -m app.xray.render_config` 即可
- `access.log` 不复制会怎样
  - 不影响已有数据库累计统计
  - 但会丢失“最近一小时访问窗口”这部分原始输入，AI 管理器需要重新积累新日志
- 新机器上 `/healthz` 返回 `500`
  - 先确认 `xray-reality` 是否已启动
  - 再确认 `XRAY_API_SERVER` 默认的 `127.0.0.1:10085` 是否可访问
  - 如果你只恢复了面板而没有恢复 Xray，可临时设置 `PANEL_HEALTH_REQUIRES_XRAY=0`

## 回滚

如果新机器验证失败：

```bash
docker compose down
```

然后保留旧机器原目录，重新在旧机器执行：

```bash
docker compose --profile xray up -d --build
```
