# K3s deployment

如果你现在只想分阶段迁移：

- 第一阶段：用 [panel-only/README.md](/root/xray-routing-panel/k8s/panel-only/README.md:1)
- 第二阶段：用 [phase2/README.md](/root/xray-routing-panel/k8s/phase2/README.md:1)
- 第三阶段：把完整链路迁进 K3s
- 第四阶段：去掉宿主机 `codex` 依赖，只保留持久化状态和可选 OpenAI 兼容分类器

当前目录 `k8s/` 现在表示第四阶段目标态。

这套清单现在按 K3s 部署收敛，不再把目标环境写成“任意 Kubernetes”。

## 为什么要单独适配 K3s

- K3s 自带 `local-path` 存储，PVC 现在直接固定到 `storageClassName: local-path`
- K3s 默认启用 Traefik，而它的 `LoadBalancer` 会占用 `80/443`
- 这套应用使用 `hostNetwork: true`，Xray 默认直接监听宿主机 `443`
- 因为数据库还是 SQLite，所以这里只支持单副本、单工作节点部署

## 部署前提

先准备一台专门跑这个栈的 K3s 节点，并确认：

- 该节点需要直接对外暴露 `18080`、`443` 和后续面板新增的监听端口
- 不要让 K3s 自带 Traefik 占用这台机器的 `443`
- 如果你是单节点 K3s，最直接的做法是禁用 Traefik

示例 K3s 服务端配置见 [k3s-server-config.example.yaml](/root/xray-routing-panel/k8s/k3s-server-config.example.yaml:1)。

## 节点准备

1. 给目标节点打标签：

```bash
kubectl label node <your-node-name> xray-routing-panel/node=true
```

2. 如果已经部署了 K3s 默认 Traefik，先确认它不会继续占用 `443`

单节点 K3s 推荐直接在 `/etc/rancher/k3s/config.yaml` 里禁用：

```yaml
disable:
  - traefik
```

## 文件说明

- `deployment.yaml`: `panel + xray + xray-reloader + ai-domain-manager` 四容器单 Pod
- `storage.yaml`: K3s `local-path` PVC
- `service.yaml`: 仅提供集群内访问面板的 `ClusterIP`
- `cronjob-backup.yaml`: 每天 03:00 备份 `panel.db`
- `secret.yaml`: 面板账号、OpenAI key、Xray `.env`

## 使用方式

1. 构建并推送镜像：

```bash
docker build -t ghcr.io/your-org/xray-routing-panel:latest .
docker build -f app/xray/Dockerfile.ai-manager -t ghcr.io/your-org/xray-routing-panel-ai-manager:latest .
docker push ghcr.io/your-org/xray-routing-panel:latest
docker push ghcr.io/your-org/xray-routing-panel-ai-manager:latest
```

2. 修改占位值：

- `k8s/deployment.yaml` 里的 `ghcr.io/your-org/...`
- `k8s/cronjob-backup.yaml` 里的 `ghcr.io/your-org/...`
- `k8s/secret.yaml` 里的 `PANEL_PASSWORD`、`PANEL_SECRET_KEY`
- `k8s/secret.yaml` 里的 `xray.env`
- `k8s/configmap.yaml` 里的 `PANEL_PUBLIC_URL`

3. 部署到 K3s：

```bash
kubectl apply -k k8s
```

如果你想走 K3s 的 AddOn 自动部署方式，可以直接写入 manifests 目录：

```bash
kubectl kustomize k8s > /var/lib/rancher/k3s/server/manifests/xray-routing-panel.yaml
```

## 验证

```bash
kubectl -n xray-routing-panel get pods -o wide
kubectl -n xray-routing-panel logs deploy/xray-routing-panel -c panel
kubectl -n xray-routing-panel logs deploy/xray-routing-panel -c xray-ai-domain-manager
kubectl -n xray-routing-panel get pvc
```

外部访问面板时，直接访问运行该 Pod 的节点地址：

```text
http://<node-ip>:18080
```

Xray 流量入口同样直接落在该节点公网或内网地址上，而不是通过 Kubernetes `Service` 转发。

## 运行边界

- 这是 K3s 单节点工作负载，不是高可用方案
- 如果你要跨节点持久化，应该把 PVC 换成 Longhorn 一类分布式存储
- 如果你要多 server 的 K3s HA，应用本身仍然会受 SQLite 和固定宿主机端口约束
- `healthz` 依赖 Xray API，Xray 没起来时 Pod 会保持不就绪
- 如果没有可用的 OpenAI 兼容分类器凭据，新增未知域名会保留在 `pending_domains_without_classifier`
