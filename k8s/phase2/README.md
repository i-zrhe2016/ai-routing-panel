# K3s 第二阶段

这个目录保留第二阶段的 K3s 清单：迁入 `panel`、`xray`、`xray-reloader`、SQLite 数据和备份任务，但暂不迁 `xray-ai-domain-manager`。

详细说明已迁入：

- [../../docs/kubernetes.md](../../docs/kubernetes.md)

适用场景：

- 需要把单一数据面收进 K3s
- 可以接受 `hostNetwork: true`
- 已经处理好目标节点 `443` 端口占用

完整链路和阶段差异请以 `docs/kubernetes.md` 为准。
