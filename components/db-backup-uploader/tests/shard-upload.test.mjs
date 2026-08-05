import test from 'node:test';
import assert from 'node:assert/strict';

process.env.DRY_RUN = '1';

const moduleUrl = new URL(`../shard-upload.js?test=${Date.now()}`, import.meta.url).href;
const {
  buildPublishArgs,
  buildUnpublishArgs,
  buildUploadRecordUpdate,
  collectVersionedPackages,
  createEmptyRecord,
  findPackageVersionConflicts,
  mapWithConcurrency
} = await import(moduleUrl);

test('npm publish and unpublish commands use the configured registry', () => {
  const publishArgs = buildPublishArgs();
  const unpublishArgs = buildUnpublishArgs('@scope/backup-shard-0', '1.2.3');

  assert.deepEqual(publishArgs.slice(0, 3), [
    'publish',
    '--registry',
    'https://registry.npmjs.org'
  ]);
  assert.deepEqual(unpublishArgs.slice(0, 4), [
    'unpublish',
    '@scope/backup-shard-0@1.2.3',
    '--registry',
    'https://registry.npmjs.org'
  ]);
});

test('findPackageVersionConflicts detects immutable npm versions', () => {
  const previous = {
    chunks: [
      {
        upload: {
          packageName: '@scope/backup-shard-0',
          version: '1.2.3'
        }
      }
    ]
  };
  const packages = [
    { packageName: '@scope/backup-shard-0', version: '1.2.3' },
    { packageName: '@scope/backup-shard-1', version: '1.2.4' }
  ];

  assert.deepEqual(findPackageVersionConflicts(previous, packages), [packages[0]]);
});

test('mapWithConcurrency exposes successful results when another item fails', async () => {
  await assert.rejects(
    mapWithConcurrency([0, 1, 2], 1, async (item) => {
      if (item === 1) throw new Error('publish failed');
      return { item };
    }),
    (error) => {
      assert.match(error.message, /publish failed/);
      assert.deepEqual(error.results, [{ item: 0 }, { item: 2 }]);
      return true;
    }
  );
});
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
