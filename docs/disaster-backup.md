# 灾备归档与 Cloudflare R2 上传通道

> Type: Runbook
> Status: Active
> Scope: 定时灾备归档生成、完整性校验、加密与 R2 上传

本模块只说明如何生成加密灾备归档并上传到 Cloudflare R2。它不负责故障切换或在线热备；节点快速恢复的准备命令见[节点备份完整性与快速恢复](node-recovery.md)。

## 目标与边界

- 每日生成一个本地 SQLite 快照。
- 将数据库快照和配置文件、运行时配置等额外文件打成一个 `tar.gz`。
- 通过 Cloudflare R2 S3 兼容 API 保存加密灾备归档。
- R2 只作为低频、异地、离线恢复通道，不参与快速恢复或故障切换。

## 归档流程

![灾备归档与加密上传流程](diagrams/disaster-backup-flow.svg)

[PlantUML 源文件](diagrams/disaster-backup-flow.puml)

任务入口是 `scripts/run_db_backup_cycle.py`：

1. 调用 `scripts/backup_db.py`，通过 SQLite 在线备份 API 生成 `backups/<prefix>-<UTC 时间戳>.db`。
2. `DB_BACKUP_SSH_COLLECTION_ENABLED=1` 时，调用 `scripts/collect_remote_backup.py`，通过隔离 broker 执行严格只读 Tailscale SSH，采集普通数据面和已配置远端 AI 节点的主配置与可选环境文件；没有远端 AI 目标时，本机 AI 备用由 `DB_BACKUP_EXTRA_PATHS` 归档。普通数据面目标优先使用 `DB_BACKUP_DATAPLANE_SSH_TARGET`，再回退到 `DATAPLANE_SSH_TARGET`；两者都为空时该角色记为 `skipped_no_target`。AI 目标按 `DB_BACKUP_AI_NODE_SSH_TARGETS`、`AI_NODE_SSH_TARGETS` 和单目标兼容变量的顺序解析。远端路径留空时使用该角色的内置默认路径。
3. 调用 `scripts/build_backup_bundle.py`，把数据库快照放在 `database/`、控制面额外路径放在 `config/`、远端 staging 放在 `nodes/`，并写入 `backup-manifest.json` 和 `node-recovery-manifest.json`。
4. 重新校验归档内所有文件的大小和 SHA-256，并把节点恢复状态写入 `node-recovery-status.json`。
5. `DB_BACKUP_R2_ENABLED=1` 时，使用 R2 S3 兼容 API 上传加密归档。
6. 上传记录写入 `DB_BACKUP_R2_RECORD_PATH`，本地文件保留用于核验和节点恢复准备。

单次任务的组件边界如下：

| 组件 | 单一职责 |
| --- | --- |
| `backup_db.py` | SQLite 一致性快照 |
| `collect_remote_backup.py` | 节点只读采集与 staging manifest |
| `build_backup_bundle.py` | 文件收集、归档与校验元数据 |
| `upload_backup_r2.py` | AES-256-GCM 加密、R2 上传与记录 |

## 默认收集内容

Docker Compose 的备份容器默认收集：

- 当次生成的 `panel.db` 一致性快照
- Compose 和 `.env`
- Xray `.env`、渲染运行配置、报告、备份脚本和 `data/uploads`

项目目录只读挂载到备份容器；缺失的可选文件会记录在 manifest 中，不阻断 `panel.db` 备份。远端节点采集默认关闭；启用完整节点模式后，归档还会加入：

```text
database/
config/                       # 控制面 DB_BACKUP_EXTRA_PATHS
nodes/
  normal-data-plane/...       # 普通数据面主机实际路径
  ai-data-plane/...           # AI 数据面主机实际路径
  remote-node-collection.json
backup-manifest.json
node-recovery-manifest.json
```

归档中的 `nodes/<role>/` 保留远端绝对路径（去掉开头的 `/`）；`node-recovery-manifest.json` 按节点部署根把文件映射成便携恢复路径。普通数据面默认主配置是 `/root/xray-routing-panel/app/xray/runtime/config.json`，AI 节点默认请求 `/etc/xray/config.json` 和 `/etc/xray/.env`，需按实际宿主机路径覆盖。未配置远端 AI 目标时，本机 AI 备用配置随控制面运行时目录归档。远端采集结果记录在 `nodes/remote-node-collection.json`。完整边界见[远端节点配置采集](remote-node-backup.md)。

## 配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DB_BACKUP_BUNDLE_ENABLED` | `1` | 是否生成灾备归档；关闭后仍保留单独 `.db` 快照 |
| `DB_BACKUP_EXTRA_PATHS` | Compose 中的显式项目配置 allowlist | 逗号或换行分隔的文件、目录或 glob；不存在的可选路径会记录并跳过 |
| `DB_BACKUP_BUNDLE_DIR` | `DB_BACKUP_DIR` | 灾备归档本地目录 |
| `DB_BACKUP_BUNDLE_KEEP_DAYS` | `DB_BACKUP_KEEP_DAYS` | 本地灾备归档保留天数，`0` 表示不清理 |
| `DB_BACKUP_BUNDLE_PREFIX` | `DB_BACKUP_PREFIX` | 归档名前缀 |
| `DB_BACKUP_SSH_COLLECTION_ENABLED` | Compose 为 `0`，脚本默认 `0`；示例 `.env` 为 `1` | 是否在打包前读取远端节点配置；完整节点模式设为 `1` |
| `DB_BACKUP_SSH_COLLECTION_REQUIRED` | Compose 为 `0`；示例 `.env` 为 `1` | 所有必需远端恢复文件必须成功；开启时缺少普通数据面目标或已配置节点采集不完整都会阻止上传 |
| `DB_BACKUP_SSH_TRANSPORT` | `tailscale-broker`（Compose） | Compose 通过隔离 broker 执行 Tailscale SSH；直接运行采集器可用 `tailscale`，兼容环境可用 `openssh` |
| `DB_BACKUP_TAILSCALE_BROKER_SOCKET` | `/var/run/xray-backup/tailscale-ssh.sock` | 备份容器连接 broker 的 Unix socket；宿主机 Tailscale LocalAPI 只挂载到 broker |
| `DB_BACKUP_TAILSCALE_BIN_HOST` / `DB_BACKUP_TAILSCALE_SOCKET_HOST` | `./scripts/tailscale-disabled`（未配置时） | broker 使用的宿主机 CLI 和 daemon socket 源路径；启用远端采集时必须设为有效路径 |
| `DB_BACKUP_DATAPLANE_SSH_TARGET` | 空 | 普通数据面目标；为空时回退 `DATAPLANE_SSH_TARGET`，完整节点模式必须提供 |
| `DB_BACKUP_AI_NODE_SSH_TARGETS` | 空 | AI 节点目标列表；未设置时兼容 `AI_NODE_SSH_TARGETS` 及单目标别名 |
| `DB_BACKUP_SSH_TIMEOUT_SECONDS` | `20` | 单节点连接/远端读取超时上限 |
| `DB_BACKUP_SSH_MAX_FILE_BYTES` | `5242880` | 单个远端文件大小上限，默认 5 MiB |
| `DB_BACKUP_DATAPLANE_REMOTE_PATHS` | 普通数据面配置、`.env`、运行时产物和最新报告 | 逗号/换行分隔；配置和 `.env` 是恢复必需文件，其余为可选 |
| `DB_BACKUP_DATAPLANE_DEPLOY_ROOT` | `/root/xray-routing-panel` | 将远端路径映射到便携恢复目录的部署根 |
| `DB_BACKUP_AI_NODE_SSH_PORT` | `22` | AI 节点 SSH 服务端口；Tailscale SSH 使用 `22` |
| `DB_BACKUP_AI_NODE_REMOTE_PATHS` | `/etc/xray/config.json,/etc/xray/.env` | AI 节点只读采集路径；按实际宿主机路径覆盖 |
| `DB_BACKUP_AI_NODE_DEPLOY_ROOT` | `/root/xray-routing-panel` | AI 节点部署根；按实际部署目录覆盖 |
| `DB_BACKUP_RECOVERY_REQUIRED` | Compose 为 `0`；示例 `.env` 为 `1` | 恢复清单不完整时阻止上传；与远端采集门禁分别检查 |
| `DB_BACKUP_RECOVERY_STATUS_PATH` | 归档目录下的 `node-recovery-status.json` | 最近一次节点恢复完整性报告 |
| `DB_BACKUP_R2_ENABLED` | `1`（Compose） | 是否将加密灾备归档上传到 R2；直接执行脚本时需显式设置并注入凭据 |
| `DB_BACKUP_R2_ENDPOINT` | 空 | Cloudflare R2 S3 endpoint |
| `DB_BACKUP_R2_BUCKET` | 空 | R2 bucket 名称 |
| `DB_BACKUP_R2_ACCESS_KEY_ID` | 空 | R2 S3 access key ID |
| `DB_BACKUP_R2_SECRET_ACCESS_KEY` | 空 | R2 S3 secret；仅通过部署环境注入 |
| `DB_BACKUP_R2_PREFIX` | `xray-routing-panel` | 对象 key 前缀 |
| `DB_BACKUP_R2_RECORD_PATH` | `/backups/r2-upload-record.json` | 本地上传记录 |

完整节点模式需要将 `DB_BACKUP_SSH_COLLECTION_ENABLED`、`DB_BACKUP_SSH_COLLECTION_REQUIRED` 和 `DB_BACKUP_RECOVERY_REQUIRED` 都设为 `1`，并配置普通数据面目标及 broker 的宿主机挂载。没有普通数据面目标或已配置远端节点缺少必需文件时，采集/恢复门禁会阻止本次归档上传；状态文件保留失败原因。未配置远端 AI 目标时，AI 备用仍从控制面本地目录归档。

示例：加入控制面项目文件和自定义密钥目录（目录必须以只读方式挂载到备份容器）：

```dotenv
DB_BACKUP_EXTRA_PATHS=/app/xray/.env,/app/xray/runtime,/app/xray/reports,/data/uploads,/backup-input/docker-compose.yml
```

`DB_BACKUP_EXTRA_PATHS` 路径会被写入归档的 `config/` 前缀下，远端 SSH staging 则写入 `nodes/`，避免恢复时覆盖宿主机绝对路径。归档内的 `backup-manifest.json` 记录每个文件的来源、大小和 SHA-256；`remote-node-collection.json` 记录 SSH 目标和逐路径状态；`node-recovery-manifest.json` 再声明哪些文件足以快速恢复每类节点。

启用完整节点的 Tailscale broker 采集前，必须在控制面 `.env` 中配置普通数据面目标、`DB_BACKUP_TAILSCALE_BIN_HOST` 和 `DB_BACKUP_TAILSCALE_SOCKET_HOST`；Compose 的 `scripts/tailscale-disabled` 回退只用于让本地-only 模式在未安装 Tailscale 的主机上能够启动，不能用于实际 Tailscale 采集。

`DB_BACKUP_EXTRA_PATHS` 可以包含业务敏感配置，但不要把 R2 密钥、SSH 私钥或其他不需要迁移的凭据目录加入列表；数据库快照和灾备归档在本地生成时仍是明文，文件权限统一为 `0600`，备份目录也必须限制为备份服务可读。Tailscale SSH 的身份由宿主机 daemon 管理，备份容器只读映射 CLI 和 socket，不保存登录密码或私钥。R2 凭据只通过部署环境、Docker Secret 或外部 Secret 管理注入，灾备加密密码必须与 R2 Secret Access Key 分离保存。任何出现在聊天、日志或 shell 历史中的 token 都应立即撤销。

## R2 灾备保留策略

R2 上传成功后，本地会保留归档和 `r2-upload-record.json`。对象 key 包含 UTC 时间和归档 SHA-256 前缀，避免同名覆盖。R2 生命周期策略应在 Cloudflare 侧配置，不在面板任务中删除远端对象。

## 灾难阶段恢复

恢复仍是人工操作，不纳入健康检查或 DNS 故障切换。下载对象并用独立保存的 `DB_BACKUP_ENCRYPTION_PASSWORD` 解密后，按[节点备份完整性与快速恢复](node-recovery.md)执行 `validate` 和 `prepare`。准备目录自带单节点 `docker-compose.node.yml`，可直接启动 Xray；恢复后仍须人工完成新主机的 Tailscale、密钥、known_hosts、防火墙、控制面目标和业务验证。

解密结果只是归档文件，不会自动覆盖运行中的配置。R2 凭据仅用于下载对象，不等同于归档解密密码。

## 排查

- 本地 `.db` 有、归档没有：检查 `DB_BACKUP_BUNDLE_ENABLED`、`DB_BACKUP_BUNDLE_DIR` 的权限和备份容器日志。
- 归档有、R2 没有：检查 `DB_BACKUP_R2_ENABLED`、R2 凭据、endpoint、bucket 和备份容器网络访问。
- 配置文件缺失：查看 `backup-manifest.json` 的 `skippedExtraPaths` 和 `node-recovery-manifest.json` 的 `missingRequiredArtifacts`。
- `validate --require-ready` 失败：使用最近一个 `node-recovery-status.json` 为 `recoveryReady=true` 的归档，不要使用当前节点失联后生成的不完整版本。
