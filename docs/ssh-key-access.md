# 内网 SSH 纳管

本文说明控制面通过内网 SSH 管理数据面。当前控制面为
`100.92.231.104`，普通数据面为 `100.116.187.106`。

## 当前策略

| 节点 | SSH 目标 | 认证策略 |
| --- | --- | --- |
| 控制面 | `100.92.231.104` | 服务本机运行控制面 |
| 普通数据面 | `root@100.116.187.106:22` | 内网直连，密码/键盘交互认证 |
| AI 备用 | 控制面本机 Docker `xray-ai-node` | 不使用 SSH |

控制面连接普通数据面时直接执行 SSH，不经过跳板，也不注入私钥：

```bash
ssh \
  -o PubkeyAuthentication=no \
  -o PreferredAuthentications=password,keyboard-interactive \
  root@100.116.187.106
```

应用和备份任务同样不读取 `-i`、`IdentityFile` 或任何私钥挂载。旧的
`DATAPLANE_SSH_KEY_FILE`、`AI_NODE_SSH_KEY_FILE` 和
`DB_BACKUP_SSH_KEY_PATH` 配置不再使用。

## 应用配置

```dotenv
DATAPLANE_SSH_TARGET=root@100.116.187.106
DATAPLANE_SSH_OPTIONS=
DATAPLANE_SSH_KNOWN_HOSTS=/root/.ssh/known_hosts
DB_BACKUP_DATAPLANE_SSH_TARGET=root@100.116.187.106
DB_BACKUP_DATAPLANE_SSH_PORT=22
DB_BACKUP_DATAPLANE_KNOWN_HOSTS=/root/.ssh/known_hosts
```

应用会固定关闭公钥认证并允许密码/键盘交互认证，同时继续执行
`StrictHostKeyChecking=yes`。`known_hosts` 只是主机指纹文件，不是登录私钥；
首次纳管前应从可信会话核对并写入目标主机指纹。

## 验证

在控制面上执行：

1. `ssh -o PubkeyAuthentication=no root@100.116.187.106 true`，确认内网 SSH 可达。
2. `ssh -o PubkeyAuthentication=no root@100.116.187.106 hostname`，确认远端账号具备纳管所需权限。
3. 重启面板和备份服务，确认数据面状态、配置同步和只读备份采集均成功。

如果目标只允许密码认证，人工验证时会出现密码提示；后台任务不会把密码写入环境变量、镜像、日志或备份归档。
