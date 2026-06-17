# K3s partial migration

这个目录是第一阶段迁移方案，只把面板和数据库备份迁到 K3s。

## 当前范围

- 迁移到 K3s：`panel`、SQLite 数据、备份 `CronJob`
- 暂不迁移：`xray` 数据面、`xray-ai-domain-manager`

这意味着：

- 面板可以先在 K3s 上稳定运行
- 面板健康检查不再把 “Xray 未运行” 视为 Pod 故障
- UI 里仍会显示 `xray_running: false`，这是预期行为

## 方案特点

- 不使用 `hostNetwork`
- `Service` 类型是 `NodePort`
- 不要求禁用 K3s 默认 Traefik，因为这一阶段不会抢占 `443`
- PVC 仍然使用 K3s 默认 `local-path`
- 默认按小内存节点收敛，避免额外的 `ServiceLB` Pod

## 内存注意

- 这一阶段默认不用 `LoadBalancer`，而改成 `NodePort 30080`
- 原因是 K3s 的 `LoadBalancer` Service 会额外创建 `ServiceLB` DaemonSet 和 `svclb-*` Pod
- 对低内存机器，这些额外 Pod 纯粹是额外开销，第一阶段没有必要承担
- `panel` Deployment 和备份 `CronJob` 已经带了保守的内存请求/限制

当前默认资源：

- `panel`: `requests.memory=96Mi`, `limits.memory=256Mi`
- `backup CronJob`: `requests.memory=48Mi`, `limits.memory=128Mi`
- `initContainer`: `requests.memory=16Mi`, `limits.memory=32Mi`

## 部署步骤

1. 给目标节点打标签：

```bash
kubectl label node <your-node-name> xray-routing-panel/node=true
```

2. 替换镜像地址和密钥：

- `deployment.yaml`、`cronjob-backup.yaml` 里的 `ghcr.io/your-org/...`
- `secret.yaml` 里的面板密码和 `xray.env`

3. 部署：

```bash
kubectl apply -k k8s/panel-only
```

4. 查看状态：

```bash
kubectl -n xray-routing-panel get pods,svc,pvc
kubectl -n xray-routing-panel logs deploy/xray-routing-panel -c panel
```

默认访问地址：

```text
http://<node-ip>:30080
```

## 第二阶段再做什么

后续如果要继续推进，下一步直接切到 [../phase2/README.md](/root/xray-routing-panel/k8s/phase2/README.md:1)：

- `xray` sidecar
- `xray-reloader`
- `hostNetwork`
- 节点 `443` 端口占用协调
