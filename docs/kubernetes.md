# K3s 部署

## 定位

仓库里的 `k8s/` 清单面向 K3s，不再把目标环境写成任意 Kubernetes。

约束：

- 默认依赖 `local-path` PVC
- 完整数据面会直接占用宿主机 `443`
- SQLite 决定了这是单副本、单工作节点部署

## 分阶段目录

| 目录 | 阶段 | 内容 |
| --- | --- | --- |
| `k8s/panel-only` | 第一阶段 | 仅迁移 `panel`、SQLite 数据和备份任务 |
| `k8s/phase2` | 第二阶段 | 增加 `xray` 和 `xray-reloader`，但不迁 AI 管理器 |
| `k8s/` | 目标态 | 完整链路，包括 `panel`、`xray`、`xray-ai-domain-manager` 和备份 |

清单目录只保留 YAML、Kustomize 和示例配置；阶段说明与部署操作以本文为唯一来源。

## 第一阶段：`k8s/panel-only`

特点：

- 不使用 `hostNetwork`
- 通过 `NodePort 30080` 暴露面板
- 不迁移数据面和 AI 域名管理器
- 健康检查不要求 Xray 可用

适合先把管理 UI 和 SQLite 数据迁入 K3s。

## 第二阶段：`k8s/phase2`

特点：

- 使用 `hostNetwork: true`
- 把 `panel`、`xray`、`xray-reloader`、SQLite 数据和备份一起迁入
- 仍不迁移 `xray-ai-domain-manager`
- 开始占用目标节点的 `443` 和后续面板端口

适合先把单一数据面收进同一个 Pod。

## 目标态：`k8s/`

特点：

- 完整迁入 `panel`、`xray`、`xray-ai-domain-manager`、备份
- 继续依赖 K3s 单节点能力和 `local-path` PVC
- 如果没有可用的分类器凭据，未知域名会留在待分类状态

## 部署前提

1. 给目标节点打标签：

```bash
kubectl label node <your-node-name> xray-routing-panel/node=true
```

2. 确认 K3s 默认 Traefik 不占用目标节点 `443`

单节点 K3s 推荐在 `/etc/rancher/k3s/config.yaml` 里禁用：

```yaml
disable:
  - traefik
```

示例配置见 [../k8s/k3s-server-config.example.yaml](../k8s/k3s-server-config.example.yaml)。

## 通用部署流程

1. 构建并推送镜像
2. 替换清单中的 `ghcr.io/your-org/...`
3. 修改 `secret.yaml` 里的面板密码、密钥和 `xray.env`
4. 修改 `configmap.yaml` 里的 `PANEL_PUBLIC_URL`
5. 对目标阶段执行：

```bash
kubectl apply -k k8s/panel-only
kubectl apply -k k8s/phase2
kubectl apply -k k8s
```

只执行与你当前阶段对应的那一条。

## 验证

```bash
kubectl -n xray-routing-panel get pods -o wide
kubectl -n xray-routing-panel get pvc
kubectl -n xray-routing-panel logs deploy/xray-routing-panel -c panel
```

完整栈还应查看：

```bash
kubectl -n xray-routing-panel logs deploy/xray-routing-panel -c xray-ai-domain-manager
```

## 边界

- 这不是高可用方案
- 如果要跨节点持久化，需要把 PVC 切到 Longhorn 一类分布式存储
- 即使做 K3s HA，应用层仍会受 SQLite 和固定宿主机端口约束
