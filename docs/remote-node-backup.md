# 远端节点配置采集

本模块只说明控制面如何通过 Tailscale SSH 读取远端数据面实际配置，并把结果交给灾备归档器。当前 AI 备用没有远端目标时运行在控制面本机 `xray-ai-node`，其配置随控制面运行时目录归档。

![远端节点只读配置采集流程](diagrams/remote-backup-flow.svg)

[PlantUML 源文件](diagrams/remote-backup-flow.puml)

## 采集边界

采集器是 `scripts/collect_remote_backup.py`，在备份容器内运行。每次任务只执行一条受限的远端 `python3 -c` 脚本，该脚本对配置路径执行 `stat` 和只读 `open(..., "rb")`：

- 只接受普通文件；目录、符号链接目标异常、超出大小上限或不可读文件会记录状态。
- 远端进程不写文件、不修改权限、不执行 `systemctl`/`docker restart`，也不会上传或替换配置。
- 内容以 Base64 返回，控制面重新计算 SHA-256 后才落入临时 staging 目录。
- `config.json` 和 `.env` 是节点恢复必需文件；运行时辅助产物是可选文件，缺失会保留在逐文件状态中。
- staging 文件和 `remote-node-collection.json` 使用 `0600`。
- Tailscale SSH 身份由宿主机的 `tailscaled` 管理；备份容器只读映射 Tailscale CLI 和 daemon socket，不挂载 SSH 私钥，也不保存登录密码。配置文件在本地打包前是明文，备份目录必须限制为备份服务可读。

## 三个节点的配置来源

灾备归档包含控制面本地文件和普通数据面实际宿主机文件：

```text
database/
config/                         # 控制面 DB_BACKUP_EXTRA_PATHS
nodes/
  normal-data-plane/
    root/xray-routing-panel/app/xray/runtime/config.json
  remote-node-collection.json
backup-manifest.json
node-recovery-manifest.json
```

默认通过只读 Tailscale SSH 采集的路径如下：

| 节点 | SSH 目标 | 主配置路径 | 配置环境文件 |
| --- | --- | --- | --- |
| 普通数据面 | `root@<normal-data-plane-host>` | `/root/xray-routing-panel/app/xray/runtime/config.json` | `.env`、`panel-ports.json`、`dynamic-routing.json`、客户端产物、最新报告 |
| 远端 AI 节点 | `AI_NODE_SSH_TARGETS` 中的目标 | `/root/xray-routing-panel/app/xray/runtime/config.json`（可覆盖） | `.env` 及显式配置路径 |
| 本机 AI 备用 | 本机 Docker `xray-ai-node` | `config/` 下的 `app/xray/runtime/config-ai-node.json` | `config/` 下的 `app/xray/.env` |

普通数据面上的 `/root/xray-routing-panel/app/xray/runtime/config.json` 是宿主机文件，Docker 容器内以只读方式挂载为 `/etc/xray/config.json`。不要把容器内路径误填为宿主机路径；如果部署目录不同，显式覆盖 `DB_BACKUP_DATAPLANE_REMOTE_PATHS`。默认还会请求 `.env`、`panel-ports.json`、`dynamic-routing.json`、客户端产物和最新 AI 报告；显式覆盖时必须保留 `config.json` 与 `.env`。

控制面自己的配置由 `DB_BACKUP_EXTRA_PATHS` 提供。Compose 默认把 `/app/xray/.env` 和 `/app/xray/runtime` 以只读方式挂载到备份服务，因此普通数据面和远端 AI 快照来自 Tailscale SSH，本机 AI 备用快照来自控制面本地目录。

## 认证与主机校验

- 默认传输是 `DB_BACKUP_SSH_TRANSPORT=tailscale`，采集器执行 `tailscale --socket /var/run/tailscale/tailscaled.sock ssh <target> <read-only-command>`。
- Compose 将宿主机的 Tailscale CLI 映射为 `/usr/local/bin/tailscale`，并将 `/var/run/tailscale/tailscaled.sock` 映射到备份容器；身份、节点授权和主机校验由 Tailscale SSH/ACL 负责。
- Tailscale 传输不读取 `known_hosts`、不使用 `-i`/`IdentityFile`，也不接受 OpenSSH options；远端命令固定为读取受限文件的 Python 脚本。
- `DB_BACKUP_SSH_TRANSPORT=openssh` 仅用于受控兼容环境；此时才使用 `known_hosts`、严格主机校验和受限 OpenSSH options。
- `AI_NODE_SSH_TARGETS` 中的多个目标会分别生成 `ai-data-plane-<node-id>` 恢复角色；`AI_NODE_IDS` 存在时用于角色后缀，否则按顺序编号。恢复时使用清单中的精确角色名。

不要把 root 密码、Tailscale auth key 或 SSH 私钥放在环境变量、日志、Markdown 或归档中。定时任务使用宿主机现有 Tailscale daemon 的授权状态，不在备份容器内保存登录凭据。

## 开关与失败策略

| 变量 | 默认值（Compose） | 作用 |
| --- | --- | --- |
| `DB_BACKUP_SSH_COLLECTION_ENABLED` | `1` | 是否采集普通数据面；关闭时仍生成控制面本地灾备归档 |
| `DB_BACKUP_SSH_COLLECTION_REQUIRED` | `1` | `1`：所有已配置远端节点的必需恢复文件必须成功采集；`0`：失联只写入 manifest 并继续控制面归档 |
| `DB_BACKUP_SSH_TRANSPORT` | `tailscale` | 远端采集传输；默认执行 `tailscale ssh` |
| `DB_BACKUP_TAILSCALE_BIN` | `/usr/local/bin/tailscale` | 备份容器内 Tailscale CLI 路径 |
| `DB_BACKUP_TAILSCALE_SOCKET` | `/var/run/tailscale/tailscaled.sock` | 宿主机 Tailscale daemon socket 的容器路径 |
| `DB_BACKUP_SSH_TIMEOUT_SECONDS` | `20` | 单节点连接/远端读取超时上限 |
| `DB_BACKUP_SSH_MAX_FILE_BYTES` | `5242880` | 单个远端文件大小上限，默认 5 MiB |
| `DB_BACKUP_DATAPLANE_REMOTE_PATHS` | 普通数据面配置、`.env`、运行时产物和最新报告 | 逗号或换行分隔；配置和 `.env` 是恢复必需文件 |
| `DB_BACKUP_DATAPLANE_DEPLOY_ROOT` | `/root/xray-routing-panel` | 将远端路径映射到便携恢复目录的部署根 |
| `DB_BACKUP_AI_NODE_SSH_PORT` | `22` | 仅显式启用远端 AI 节点 SSH 采集时使用 |
| `DB_BACKUP_AI_NODE_REMOTE_PATHS` | 空 | 当前本机 AI 备用不使用远端采集 |
| `DB_BACKUP_AI_NODE_DEPLOY_ROOT` | `/root/xray-routing-panel` | 远端 AI 节点的部署根 |

默认采用恢复完整性门禁：已配置的远端节点无法通过 Tailscale SSH 提供必需文件时，本次灾备上传失败，并保留 manifest/状态用于排障。只有计划中的节点维护、且明确接受控制面-only 归档时，才设置 `DB_BACKUP_SSH_COLLECTION_REQUIRED=0` 和 `DB_BACKUP_RECOVERY_REQUIRED=0`。

## manifest 与核验

`nodes/remote-node-collection.json` 为每个实际启用的远端节点和每个请求路径记录：

- `status`：节点整体状态（`ok`、`partial`、`failed` 或 `skipped_no_target`）。
- `target`、`sshPort`、`knownHosts`、`requestedPaths`：本次连接参数和请求路径（不包含密钥内容）。
- `path`、`exists`、`mode`、`mtime`、`size`、`sha256`：远端 stat 与内容摘要。
- `stagedPath`：归档内对应文件路径；不包含 Base64 内容。
- `configCollected`：主配置路径是否确实成功写入 staging。
- `requiredPaths` / `recoveryReady`：恢复必需路径和该节点是否具备完整恢复材料。

归档根部 `backup-manifest.json` 再记录所有文件的 SHA-256，`node-recovery-manifest.json` 将远端路径映射到便携恢复目录。灾难阶段先验证两层 manifest，再使用 `scripts/node_recovery.py prepare --node normal-data-plane` 将 `nodes/` 下的配置复制到隔离目录并启动 Xray；不要直接覆盖运行中的配置。

## 只读验证命令

在控制面上执行采集器（不会触碰远端状态）：

```bash
DB_BACKUP_SSH_TRANSPORT=tailscale \
DB_BACKUP_TAILSCALE_BIN=/usr/bin/tailscale \
DB_BACKUP_TAILSCALE_SOCKET=/var/run/tailscale/tailscaled.sock \
DB_BACKUP_DATAPLANE_SSH_TARGET='root@<normal-data-plane-host>' \
DB_BACKUP_DATAPLANE_SSH_PORT=22 \
DB_BACKUP_DATAPLANE_REMOTE_PATHS=/root/xray-routing-panel/app/xray/runtime/config.json,/root/xray-routing-panel/app/xray/.env,/root/xray-routing-panel/app/xray/runtime/panel-ports.json,/root/xray-routing-panel/app/xray/runtime/dynamic-routing.json \
python3 scripts/collect_remote_backup.py --output-dir /var/tmp/xray-remote-staging --required
```

检查输出目录中的 `remote-node-collection.json`，确认普通数据面 `configCollected=true` 且 `recoveryReady=true`。本机 AI 备用配置应在归档 `config/` 中核验。该命令只在本地 staging 目录写入临时文件；远端命令只执行 `stat`/读取。

## 排障顺序

1. `Tailscale SSH CLI is unavailable`：确认宿主机 `/usr/bin/tailscale` 存在且备份容器已重建，容器内 CLI 路径和 daemon socket 可读。
2. `Tailscale daemon socket is unavailable`：确认宿主机 `tailscaled` 正常运行，并核对 Compose 的 socket 挂载；不要把 auth key 写入容器。
3. `SSH collection failed`：确认 Tailscale ACL 允许控制面身份以目标用户连接，目标主机启用了 Tailscale SSH；先在宿主机用同一目标执行只读 `tailscale ssh` 验证。
4. `missing`：通过只读 `docker inspect`、`systemctl cat` 或部署清单确认宿主机真实路径，再覆盖节点的 `*_REMOTE_PATHS`。
5. `partial`：查看文件级 status；主配置缺失时不要把 `.env` 采集成功误判为完整配置。
6. `too_large`：提高 `DB_BACKUP_SSH_MAX_FILE_BYTES` 前先确认该文件确实属于灾备范围，并评估归档大小和本地/R2 保留成本。

SSH 采集失败不会触发节点重启、配置回滚或 DNS 切换；这些是独立运维流程。R2 上传仍只是加密后的异地灾备通道，不承担快速恢复。
