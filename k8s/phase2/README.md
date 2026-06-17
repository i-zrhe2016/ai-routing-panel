# K3s second stage

这个目录是第二阶段迁移方案，把 `xray` 数据面也搬进 K3s，但仍然不迁 `xray-ai-domain-manager`。

## 当前范围

- 已迁移到 K3s：`panel`、`xray`、`xray-reloader`、SQLite 数据、备份 `CronJob`
- 暂不迁移：`xray-ai-domain-manager`

这意味着：

- 面板已经能在 K3s 内直接管理并验证同 Pod 里的 Xray
- `healthz` 会重新要求 Xray API 正常
- 你会开始占用目标节点的 `443` 和面板动态新增监听端口
- 还没有 AI 域名动态分类和动态路由报表链路

## 方案特点

- 使用 `hostNetwork: true`
- `Service` 只保留集群内 `ClusterIP`，对外访问仍然直接走节点 IP
- 需要 K3s 目标节点专用调度标签
- 需要处理 K3s 默认 Traefik 对 `443` 的占用

## 部署前提

1. 给目标节点打标签：

```bash
kubectl label node <your-node-name> xray-routing-panel/node=true
```

2. 确认 K3s 默认 Traefik 不占用该节点的 `443`

单节点 K3s 推荐在 `/etc/rancher/k3s/config.yaml` 里禁用：

```yaml
disable:
  - traefik
```

完整示例见 [../k3s-server-config.example.yaml](/root/xray-routing-panel/k8s/k3s-server-config.example.yaml:1)。

## 部署步骤

1. 替换镜像地址和密钥：

- `deployment.yaml`、`cronjob-backup.yaml` 里的 `ghcr.io/your-org/...`
- `secret.yaml` 里的面板密码和 `xray.env`

2. 部署：

```bash
kubectl apply -k k8s/phase2
```

3. 查看状态：

```bash
kubectl -n xray-routing-panel get pods -o wide
kubectl -n xray-routing-panel logs deploy/xray-routing-panel -c panel
kubectl -n xray-routing-panel logs deploy/xray-routing-panel -c xray
```

## 与第一阶段的差异

- 从 `LoadBalancer` Service 切回 `hostNetwork`
- Pod 健康检查重新依赖 Xray
- 节点会开始直接暴露 `18080`、`443` 和后续面板端口

## 第三阶段再做什么

如果要迁完整链路，再切到上一级 `k8s/`：

- `xray-ai-domain-manager`
- 报表目录和动态 AI 路由链路
- OpenAI / Codex 分类器配置
