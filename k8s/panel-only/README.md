# K3s 第一阶段

这个目录保留第一阶段的 K3s 清单：只迁移 `panel`、SQLite 数据和备份任务。

详细说明已迁入：

- [../../docs/kubernetes.md](../../docs/kubernetes.md)

适用场景：

- 先把管理 UI 跑进 K3s
- 暂不迁移 `xray` 数据面和 `xray-ai-domain-manager`
- 继续通过 `NodePort 30080` 暴露面板

如果你只是想知道这一阶段与后续阶段的差异，请直接看 `docs/kubernetes.md`。
