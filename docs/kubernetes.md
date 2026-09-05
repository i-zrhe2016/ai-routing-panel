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

## 备份 CronJob 与灾备归档

三个阶段的 `cronjob-backup.yaml` 都执行 `scripts/run_db_backup_cycle.py`，而不是只执行数据库快照脚本。任务会在共享 PVC 的 `backups/` 下生成 `.db` 和 `*-disaster-*.tar.gz`；默认把共享卷的 `/app/xray/runtime`、Secret 提供的 `/app/xray/.env` 和 `/data/uploads` 纳入归档。

Kubernetes 清单默认不启用远端 SSH 采集（`DB_BACKUP_SSH_COLLECTION_ENABLED` 未设置时脚本默认 `0`），因为仓库不能携带生产私钥和 known_hosts。若需要把普通数据面的实际配置也纳入灾备，按以下最小边界扩展备份 CronJob；本机 AI 配置由控制面运行时目录归档：

1. 创建只包含 fleet 私钥的 Secret，并创建只包含已核验主机指纹的 Secret；两个 Secret 都只挂载到备份容器、使用 `readOnly: true`。
2. 在备份容器加入 `/run/secrets/fleet_ssh_key`、`/root/.ssh/known_hosts` 和 `/root/.ssh/known_hosts_ai` 三个挂载。
3. 通过 Secret 或受控的环境注入设置 `DB_BACKUP_SSH_COLLECTION_ENABLED=1`、普通数据面的 SSH target/port/path；Tailscale SSH 管理端口为 22。
4. 先保持 `DB_BACKUP_SSH_COLLECTION_REQUIRED=0` 观察 `nodes/remote-node-collection.json`，确认普通数据面 `configCollected=true` 后，再按发布门禁需要改为 `1`。

示例（内容值仅为占位符，不要提交真实 key）：

```yaml
volumes:
  - name: fleet-ssh-key
    secret:
      X xray-fleet-ssh
      defaultMode: 0600
  - name: fleet-known-hosts
    secret:
      X xray-fleet-known-hosts
      defaultMode: 0600
volumeMounts:
  - name: fleet-ssh-key
    mountPath: /run/secrets/fleet_ssh_key
    subPath: fleet_ssh_key
    readOnly: true
  - name: fleet-known-hosts
    mountPath: /root/.ssh/known_hosts
    subPath: known_hosts
    readOnly: true
  - name: fleet-known-hosts
    mountPath: /root/.ssh/known_hosts_ai
    subPath: known_hosts_ai
    readOnly: true
```

Kubernetes 变体若显式启用远端 SSH 采集，普通数据面使用 Tailscale `redacted-ip-003:22`；当前本机 AI 备用不需要 SSH。不要把 AI 业务端口 `27166` 当作 SSH 管理端口。完整采集器安全边界见[远端节点配置采集](remote-node-backup.md)。

如需把 `xray.env`、R2 密钥或其他项目文件加入归档，应通过 Secret/只读挂载提供文件，再在对应 ConfigMap 设置 `DB_BACKUP_EXTRA_PATHS`。R2 上传需显式设置 `DB_BACKUP_R2_ENABLED=1`、归档密码和 R2 认证信息；R2 对象的生命周期和保留策略在 Cloudflare 侧配置，不参与快速恢复。

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
