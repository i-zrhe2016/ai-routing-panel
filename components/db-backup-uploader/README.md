# db-backup-uploader

`db-backup-uploader` 是当前项目内的数据库备份归档组件。

它基于上游 `npm-uploader` 改造，职责固定为：

- 接收 `panel.db` 的备份文件
- 使用 `AES-256-GCM` 加密
- 切成多个分片
- 将每个分片封装成独立 npm 包并发布
- 按记录或本地分片恢复原始备份文件

## 项目内默认目录

- 输入备份：由 `scripts/run_db_backup_cycle.py` 指定单个文件路径
- 分片目录：`/db-backup-uploader-data/shards`
- 恢复目录：`/db-backup-uploader-data/restored`
- 上传记录：`/db-backup-uploader-data/upload-records.json`

## 运行方式

自动模式由 `xray-routing-panel-db-backup` 容器中的 cron 每天触发：

1. `scripts/backup_db.py` 生成新的 `.db` 备份
2. `scripts/run_db_backup_cycle.py` 调用本组件上传该备份

也可以手动执行：

```bash
docker compose run --rm xray-routing-panel-db-backup \
  python3 /app/scripts/run_db_backup_cycle.py
```

仅测试上传链路可用性：

```bash
docker compose run --rm \
  -e DB_BACKUP_UPLOADER_ENABLED=1 \
  -e DB_BACKUP_UPLOADER_DRY_RUN=1 \
  xray-routing-panel-db-backup \
  python3 /app/scripts/run_db_backup_cycle.py
```

## 关键配置

- `DB_BACKUP_UPLOADER_ENABLED`
- `DB_BACKUP_UPLOADER_PASSWORD`
- `DB_BACKUP_UPLOADER_SCOPE`
- `DB_BACKUP_UPLOADER_PACKAGE_VERSION`
- `DB_BACKUP_UPLOADER_DRY_RUN`
- `DB_BACKUP_UPLOADER_NPMRC_PATH`

真实发布通常需要在 `data/db-backup-uploader/.npmrc` 放置 npm 认证配置。

## 组件源码

- [shard-upload.js](shard-upload.js)
- [shard-restore.js](shard-restore.js)
- [UPSTREAM_README.md](UPSTREAM_README.md)
