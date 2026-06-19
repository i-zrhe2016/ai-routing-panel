import test from 'node:test';
import assert from 'node:assert/strict';

process.env.DRY_RUN = '1';

const moduleUrl = new URL(`../shard-upload.js?test=${Date.now()}`, import.meta.url).href;
const {
  buildUploadRecordUpdate,
  collectVersionedPackages,
  createEmptyRecord
} = await import(moduleUrl);

test('buildUploadRecordUpdate keeps only latest snapshot', () => {
  const record = createEmptyRecord();
  record.artifacts['xray-routing-panel-db-backup'] = {
    artifactName: 'xray-routing-panel-db-backup',
    latestUploadId: 'old-upload',
    updatedAt: '2026-06-19T00:00:00.000Z',
    latest: {
      uploadId: 'old-upload',
      packageVersion: '20260619.10000.0',
      chunks: [
        {
          index: 0,
          upload: {
            packageName: '@zrhe2016/xray-routing-panel-db-backup-shard-0',
            version: '20260619.10000.0'
          }
        }
      ]
    },
    history: [
      {
        uploadId: 'older-upload'
      }
    ]
  };

  const manifest = {
    uploadId: 'new-upload',
    artifactName: 'xray-routing-panel-db-backup',
    packageVersion: '20260619.20000.0',
    chunks: [
      {
        index: 0,
        upload: {
          packageName: '@zrhe2016/xray-routing-panel-db-backup-shard-0',
          version: '20260619.20000.0'
        }
      }
    ]
  };

  const update = buildUploadRecordUpdate(
    record,
    '/tmp/new-backup.db',
    manifest,
    '/tmp/shards/manifest.json',
    '/tmp/upload-records.json'
  );

  assert.equal(update.previousLatest.uploadId, 'old-upload');
  assert.equal(update.uploadId, 'new-upload');
  assert.equal(
    record.artifacts['xray-routing-panel-db-backup'].latest.packageVersion,
    '20260619.20000.0'
  );
  assert.deepEqual(record.artifacts['xray-routing-panel-db-backup'].history, []);
});

test('collectVersionedPackages de-duplicates shard package versions', () => {
  const packages = collectVersionedPackages({
    chunks: [
      {
        upload: {
          packageName: '@zrhe2016/xray-routing-panel-db-backup-shard-0',
          version: '20260619.20000.0'
        }
      },
      {
        upload: {
          packageName: '@zrhe2016/xray-routing-panel-db-backup-shard-0',
          version: '20260619.20000.0'
        }
      },
      {
        upload: {
          packageName: '@zrhe2016/xray-routing-panel-db-backup-shard-1',
          version: '20260619.20000.0'
        }
      },
      {
        upload: {
          packageName: '',
          version: ''
        }
      }
    ]
  });

  assert.deepEqual(packages, [
    {
      packageName: '@zrhe2016/xray-routing-panel-db-backup-shard-0',
      version: '20260619.20000.0'
    },
    {
      packageName: '@zrhe2016/xray-routing-panel-db-backup-shard-1',
      version: '20260619.20000.0'
    }
  ]);
});
