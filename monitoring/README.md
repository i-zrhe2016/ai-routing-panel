# 监控栈（Prometheus + Grafana + node_exporter）

抓取面板 `/metrics`（业务/存活/数据面指标）与本机 node_exporter（系统资源），用 Grafana 出图。

## 组成

- `docker-compose.monitoring.yml` — 三个容器：`node-exporter`(host 网络, :9100)、`prometheus`(:9090)、`grafana`(:3000)，均设了内存上限。
- `prometheus/prometheus.yml` — 抓取配置。面板 job 用 Bearer token（`credentials_file`）。
- `prometheus/secrets/metrics_token` — 抓取令牌（= 面板 `.env` 的 `METRICS_TOKEN`）。**已 gitignore，不入库。**
- `grafana/provisioning/` — 自动配置 Prometheus 数据源 + dashboard provider。
- `grafana/dashboards/xray-panel.json` — 完整运维大盘。
- `grafana/dashboards/xray-observability.json` — 内嵌专用大盘（UID `xray-observability`，panel 带显式 id），供面板「监控」标签按 `d-solo` 单图内嵌。
- `.env` — `GRAFANA_ADMIN_PASSWORD`。**已 gitignore。**

## 启停

```bash
cd monitoring
docker compose -f docker-compose.monitoring.yml up -d     # 启动
docker compose -f docker-compose.monitoring.yml ps        # 状态
docker compose -f docker-compose.monitoring.yml down       # 停止（保留数据卷）
```

改了 `prometheus.yml` 后热加载：`curl -X POST http://127.0.0.1:9090/-/reload`。

## 访问

- Grafana：`http://<host>:3000`，用户 `admin`，密码见 `monitoring/.env`。Dashboard：`/d/xray-panel`。
- Prometheus：`http://<host>:9090`。

## 内嵌到面板「监控」标签

Grafana 已开启匿名只读（`GF_AUTH_ANONYMOUS_ENABLED=true` + `Viewer`）与 iframe 内嵌（`GF_SECURITY_ALLOW_EMBEDDING=true`），所以面板后台「监控」标签可直接 `d-solo` 内嵌 `xray-observability` 的单图，免 Grafana 登录。给面板设置 `GRAFANA_PUBLIC_URL`（浏览器可达的 Grafana 地址）即可，详见 `docs/operations.md`。

## ⚠️ 安全

开启匿名只读后，**任何能访问 Grafana `:3000` 的人都能只读全部图表**；`9090`(Prometheus) 与 `9100`(node_exporter) 也**默认无认证**，当前绑定 `0.0.0.0` 公网可达。强烈建议用云防火墙/iptables 把 `9090`、`9100`、`3000` 限制为你的可信来源 IP。面板 `/metrics`(:18080) 有 token 保护。更稳妥的内嵌加固是把 Grafana 反代到面板受登录鉴权的同源子路径（`GF_SERVER_ROOT_URL` + `serve_from_sub_path`），免开匿名。

## 加入 DMIT 数据面系统指标

在 DMIT `64.186.224.96` 上也跑一份 node_exporter，然后取消 `prometheus.yml` 里 `job_name: node` 下 DMIT target 的注释并 reload。
