# SSH 密钥登录与轮换

本文只说明控制面及数据面节点的 SSH 管理认证。业务端口和应用凭据不在本文范围内。

## 当前策略

| 节点 | SSH 目标 | 认证策略 |
| --- | --- | --- |
| 控制面 | `root@100.87.76.6:22` | Tailscale 网络，仅指定 Ed25519 公钥 |
| AI 备用 | 本机 Docker `xray-ai-node` | 不使用 SSH；配置由控制面运行时目录提供 |
| 普通数据面 | `root@100.65.108.93:22` | Tailscale 网络，仅指定 Ed25519 公钥 |

当前公钥指纹：

```text
SHA256:g8BcGWdEKZK3qLB11z1GnbhDY25SAk0Iq+il+g3siDM
```

私钥位于控制机 `/root/.ssh/xray_fleet_ed25519_20260805`，权限必须为 `0600`。私钥不得进入 Git、镜像、普通环境变量或本文档。

所有节点的 sshd 最终策略为：

```text
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PermitRootLogin prohibit-password
AuthenticationMethods publickey
```

`PermitRootLogin prohibit-password` 在部分 OpenSSH 版本的 `sshd -T` 输出中显示为 `without-password`，两者语义相同。

## 应用容器挂载

控制面应用只读挂载同一把运维私钥：

```yaml
- /root/.ssh/xray_fleet_ed25519_20260805:/run/secrets/fleet_ssh_key:ro
```

AI SSH 包装器默认读取 `/run/secrets/fleet_ssh_key`。数据面 SSH 参数也应使用此路径，并强制：

```text
IdentitiesOnly=yes
PreferredAuthentications=publickey
PasswordAuthentication=no
KbdInteractiveAuthentication=no
StrictHostKeyChecking=yes
```

控制面到三个节点的自动 SSH 连接默认使用 Tailscale 地址、fleet 私钥和
`BatchMode=yes`，不会提示或回退到密码认证。主机密钥分别通过受控的
`/root/.ssh/known_hosts` 和 `/root/.ssh/known_hosts_ai` 校验。

主机密钥仍通过独立 `known_hosts` 文件校验，禁止使用 `StrictHostKeyChecking=no`。

## 验证

每台主机轮换后执行以下检查：

1. 使用新私钥和 `IdentitiesOnly=yes` 建立全新连接。
2. `sshd -t` 必须成功。
3. `sshd -T` 必须显示仅公钥认证，密码和键盘交互关闭。
4. `/root/.ssh/authorized_keys` 只能包含一条有效公钥。
5. 远端公钥指纹必须等于本文记录的指纹。
6. 旧私钥必须返回 `Permission denied (publickey)`。

## 轮换流程

密钥轮换必须逐台进行，禁止同时修改所有节点：

1. 生成新的专用 Ed25519 密钥，不能覆盖当前可用私钥。
2. 通过当前密钥保持一个恢复连接。
3. 备份 `authorized_keys`、`sshd_config` 和 `sshd_config.d`。
4. 先将新公钥与旧公钥并存，使用新私钥建立独立连接。
5. 新连接成功后，原子替换 `authorized_keys`，只保留新公钥。
6. 写入 key-only sshd drop-in，运行 `sshd -t` 后只 reload，不 restart。
7. 分别验证新密钥成功、旧密钥失败、密码和键盘交互失败。
8. 验证全部通过后才关闭恢复连接并处理下一台。

## 恢复

每台节点的最近备份路径记录在：

```text
/root/.ssh-rotation-last-backup
```

备份目录格式为：

```text
/root/ssh-rotation-backup-<UTC timestamp>
```

如果新密钥无法登录，应在仍保持的旧会话或云厂商控制台中：

1. 从备份恢复 `/root/.ssh/authorized_keys`。
2. 恢复 `/etc/ssh/sshd_config` 和 `/etc/ssh/sshd_config.d`。
3. 运行 `sshd -t`。
4. reload `ssh` 或 `sshd` 服务。
5. 建立新的恢复连接后再关闭原会话。

备份包含旧公钥及 SSH 配置，验收完成前不要删除。私钥丢失且没有存活会话时，只能通过云厂商控制台恢复。
