import fs from 'fs';
import fsp from 'fs/promises';
import path from 'path';
import crypto from 'crypto';
import { execFile } from 'child_process';
import { once } from 'events';
import { finished } from 'stream/promises';
import { promisify } from 'util';
import { fileURLToPath } from 'url';

const DEFAULT_PASSWORD = 'your-strong-password-here-123456';
const ALGORITHM = 'aes-256-gcm';
const SALT_SIZE = 16;
const IV_SIZE = 12;
const AUTH_TAG_SIZE = 16;
const args = parseArgs(process.argv.slice(2));

const CHUNK_SIZE = parsePositiveInt(
  args.shardSize || process.env.SHARD_SIZE_BYTES,
  5 * 1024 * 1024
);
const PASSWORD = args.password || process.env.SHARD_PASSWORD || DEFAULT_PASSWORD;
const FILES_DIR = args.filesDir || process.env.FILES_DIR || 'files';
const INPUT_FILES = resolveInputFiles(args.files, process.env.INPUT_FILE || '');
const ARTIFACT_NAME_OVERRIDE = sanitizeOptionalName(
  args.artifact || process.env.ARTIFACT_NAME || ''
);
const SHARDS_DIR = process.env.SHARDS_DIR || 'shards';
const UPLOAD_RECORD_FILE =
  process.env.UPLOAD_RECORD_PATH ||
  process.env.UPLOAD_RECORD_FILE ||
  'upload-records.json';
const RECORD_HISTORY_LIMIT = parsePositiveInt(
  process.env.UPLOAD_RECORD_HISTORY_LIMIT,
  20
);
const NPM_SCOPE = normalizeScope(
  args.scope != null
    ? args.scope
    : process.env.NPM_SCOPE !== undefined
      ? process.env.NPM_SCOPE
      : '@yourusername'
);
const NPM_ACCESS = process.env.NPM_ACCESS || 'public';
const PACKAGE_VERSION = args.version || process.env.NPM_PACKAGE_VERSION || '1.0.0';
const NPM_REGISTRY = normalizeBaseUrl(
  process.env.NPM_REGISTRY || 'https://registry.npmjs.org'
);
const NPM_WEB_BASE = normalizeBaseUrl(
  process.env.NPM_WEB_BASE || 'https://www.npmjs.com/package'
);
const NPM_TAG = process.env.NPM_TAG || 'latest';
const NPM_PUBLISH_OTP = process.env.NPM_PUBLISH_OTP || '';
const PUBLISH_CONCURRENCY = parsePositiveInt(process.env.PUBLISH_CONCURRENCY, 2);
const DRY_RUN = process.env.DRY_RUN === '1';
const PUBLISH_TIMEOUT_MS = parsePositiveInt(
  process.env.NPM_PUBLISH_TIMEOUT_MS,
  10 * 60 * 1000
);
// The scheduled panel backup sets this to 0 so npm remains a durable,
// immutable disaster-recovery channel.  Standalone uploader users retain the
// historical latest-only behavior unless they opt out explicitly.
const PRUNE_REMOTE_UPLOADS = process.env.PRUNE_REMOTE_UPLOADS !== '0';
const KEEP_UPLOAD_HISTORY = process.env.PRUNE_REMOTE_UPLOADS === '0';

const execFileAsync = promisify(execFile);
const MODULE_PATH = fileURLToPath(import.meta.url);
const IS_MAIN = process.argv[1] && path.resolve(process.argv[1]) === MODULE_PATH;

function parseArgs(argv) {
  const result = {
    files: []
  };

  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];

    if (!token.startsWith('--')) {
      result.files.push(token);
      continue;
    }

    const [flag, inlineValue] = token.split('=', 2);
    const nextValue = inlineValue ?? argv[index + 1];
    const consumeNext = inlineValue == null;

    switch (flag) {
      case '--file':
        result.files.push(nextValue);
        if (consumeNext) index += 1;
        break;
      case '--artifact':
        result.artifact = nextValue;
        if (consumeNext) index += 1;
        break;
      case '--version':
        result.version = nextValue;
        if (consumeNext) index += 1;
        break;
      case '--password':
        result.password = nextValue;
        if (consumeNext) index += 1;
        break;
      case '--files-dir':
        result.filesDir = nextValue;
        if (consumeNext) index += 1;
        break;
      case '--scope':
        result.scope = nextValue;
        if (consumeNext) index += 1;
        break;
      case '--shard-size':
        result.shardSize = nextValue;
        if (consumeNext) index += 1;
        break;
      default:
        throw new Error(`未知参数: ${flag}`);
    }
  }

  return result;
}

function parsePositiveInt(value, fallback) {
  if (!value) return fallback;
  const parsed = Number.parseInt(value, 10);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

function normalizeScope(scope) {
  if (!scope) return '';
  return scope.startsWith('@') ? scope : `@${scope}`;
}

function normalizeBaseUrl(value) {
  return value.replace(/\/+$/, '');
}

function sanitizeName(value) {
  const sanitized = value
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .replace(/-{2,}/g, '-');

  return sanitized || 'file';
}

function sanitizeOptionalName(value) {
  if (!value || !String(value).trim()) {
    return '';
  }
  return sanitizeName(String(value).trim());
}

function resolveInputFiles(cliFiles, envFile) {
  const files = [];
  for (const item of cliFiles || []) {
    if (item && String(item).trim()) {
      files.push(String(item).trim());
    }
  }
  if (envFile && String(envFile).trim()) {
    files.push(String(envFile).trim());
  }
  return files;
}

function buildPackageName(scope, artifactName, chunkIndex) {
  const baseName = `${artifactName}-shard-${chunkIndex}`;
  return scope ? `${scope}/${baseName}` : baseName;
}

function buildRegistryMetadataUrl(registry, packageName) {
  return `${registry}/${encodeURIComponent(packageName)}`;
}

function buildPackageWebUrl(webBase, packageName, version) {
  return `${webBase}/${packageName}/v/${version}`;
}

function formatSize(bytes) {
  return `${(bytes / 1024 / 1024).toFixed(2)}MB`;
}

async function writeJson(targetPath, data) {
  await fsp.mkdir(path.dirname(targetPath), { recursive: true });
  await fsp.writeFile(targetPath, JSON.stringify(data, null, 2));
}

async function pathExists(targetPath) {
  try {
    await fsp.access(targetPath);
    return true;
  } catch {
    return false;
  }
}

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}

async function writeBuffer(stream, chunk) {
  if (!stream.write(chunk)) {
    await once(stream, 'drain');
  }
}

async function closeStream(stream) {
  if (!stream || stream.destroyed) return;
  stream.end();
  await finished(stream);
}

async function encryptAndSplitFile(filePath, artifactName) {
  const shardRoot = path.join(SHARDS_DIR, artifactName);
  const shardFileName = `${artifactName}.part`;
  const sourceStats = await fsp.stat(filePath);
  const uploadId = crypto.randomUUID();
  const chunks = [];

  await fsp.rm(shardRoot, { recursive: true, force: true });
  await fsp.mkdir(shardRoot, { recursive: true });

  const salt = crypto.randomBytes(SALT_SIZE);
  const iv = crypto.randomBytes(IV_SIZE);
  const key = crypto.scryptSync(PASSWORD, salt, 32);
  const cipher = crypto.createCipheriv(ALGORITHM, key, iv, {
    authTagLength: AUTH_TAG_SIZE
  });
  const input = fs.createReadStream(filePath);
  const encryptedStream = input.pipe(cipher);

  const shardPaths = [];
  let currentStream = null;
  let currentShardSize = 0;
  let currentShardPath = '';
  let shardIndex = 0;
  let encryptedBytes = 0;

  async function openShard() {
    currentShardPath = path.join(shardRoot, `${shardFileName}${shardIndex}`);
    currentStream = fs.createWriteStream(currentShardPath);
    currentShardSize = 0;
    shardPaths.push(currentShardPath);
  }

  async function finalizeShard() {
    if (!currentStream) return;
    await closeStream(currentStream);
    chunks.push({
      index: shardIndex,
      fileName: path.basename(currentShardPath),
      size: currentShardSize
    });
    console.log(
      `✓ 生成切片: ${currentShardPath} (${formatSize(currentShardSize)})`
    );
    currentStream = null;
    currentShardPath = '';
    currentShardSize = 0;
    shardIndex += 1;
  }

  try {
    for await (const chunk of encryptedStream) {
      let offset = 0;

      while (offset < chunk.length) {
        if (!currentStream) {
          await openShard();
        }

        const writableBytes = Math.min(
          CHUNK_SIZE - currentShardSize,
          chunk.length - offset
        );
        const slice = chunk.subarray(offset, offset + writableBytes);

        await writeBuffer(currentStream, slice);

        currentShardSize += writableBytes;
        encryptedBytes += writableBytes;
        offset += writableBytes;

        if (currentShardSize === CHUNK_SIZE) {
          await finalizeShard();
        }
      }
    }

    if (!shardPaths.length) {
      await openShard();
    }

    await finalizeShard();
  } catch (error) {
    input.destroy(error);
    cipher.destroy(error);
    if (currentStream && !currentStream.destroyed) {
      currentStream.destroy(error);
    }
    throw error;
  }

  const manifest = {
    version: 1,
    uploadId,
    artifactName,
    encrypted: true,
    algorithm: ALGORITHM,
    authTagLength: AUTH_TAG_SIZE,
    chunkSize: CHUNK_SIZE,
    totalChunks: shardPaths.length,
    encryptedBytes,
    originalFileName: path.basename(filePath),
    originalFileSize: sourceStats.size,
    packageVersion: PACKAGE_VERSION,
    registry: NPM_REGISTRY,
    access: NPM_ACCESS,
    salt: salt.toString('hex'),
    iv: iv.toString('hex'),
    authTag: cipher.getAuthTag().toString('hex'),
    generatedAt: new Date().toISOString(),
    chunks
  };

  const manifestPath = path.join(shardRoot, 'manifest.json');
  await writeJson(manifestPath, manifest);

  return {
    manifest,
    manifestPath,
    shardRoot,
    shardFileName
  };
}

async function createNpmPackage(
  shardRoot,
  manifestPath,
  artifactName,
  chunkIndex,
  totalChunks,
  packageVersion
) {
  const shardFileName = `${artifactName}.part${chunkIndex}`;
  const pkgDir = path.join(shardRoot, `shard-${chunkIndex}`);
  const packageName = buildPackageName(NPM_SCOPE, artifactName, chunkIndex);

  await fsp.mkdir(pkgDir, { recursive: true });

  const pkgJson = {
    name: packageName,
    version: packageVersion,
    description: `Encrypted shard ${chunkIndex} of ${artifactName}`,
    main: 'index.js',
    files: ['index.js', 'manifest.json', shardFileName],
    private: false,
    license: 'MIT'
  };

  const indexSource = `module.exports = {
  shardIndex: ${chunkIndex},
  totalShards: ${totalChunks},
  manifest: require('./manifest.json'),
  shardFile: '${shardFileName}'
};
`;

  await Promise.all([
    fsp.writeFile(
      path.join(pkgDir, 'package.json'),
      JSON.stringify(pkgJson, null, 2)
    ),
    fsp.writeFile(path.join(pkgDir, 'index.js'), indexSource),
    fsp.copyFile(manifestPath, path.join(pkgDir, 'manifest.json')),
    fsp.copyFile(
      path.join(shardRoot, shardFileName),
      path.join(pkgDir, shardFileName)
    )
  ]);

  return {
    chunkIndex,
    packageName,
    pkgDir,
    version: packageVersion
  };
}

function buildPublishArgs() {
  const args = [
    'publish',
    '--registry',
    NPM_REGISTRY,
    '--access',
    NPM_ACCESS,
    '--tag',
    NPM_TAG
  ];
  if (NPM_PUBLISH_OTP) {
    args.push('--otp', NPM_PUBLISH_OTP);
  }
  return args;
}

function buildUnpublishArgs(packageName, version) {
  const args = [
    'unpublish',
    `${packageName}@${version}`,
    '--registry',
    NPM_REGISTRY
  ];
  if (NPM_PUBLISH_OTP) {
    args.push('--otp', NPM_PUBLISH_OTP);
  }
  return args;
}

async function publishPackage(chunkIndex, packageName, version, pkgDir) {
  const upload = {
    status: DRY_RUN ? 'dry-run' : 'published',
    packageName,
    version,
    installSpec: `${packageName}@${version}`,
    tag: NPM_TAG,
    access: NPM_ACCESS,
    registry: NPM_REGISTRY,
    packageUrl: buildPackageWebUrl(NPM_WEB_BASE, packageName, version),
    registryMetadataUrl: buildRegistryMetadataUrl(NPM_REGISTRY, packageName),
    publishedAt: DRY_RUN ? null : new Date().toISOString()
  };

  if (DRY_RUN) {
    console.log(`↷ dry-run: 跳过发布 ${packageName}`);
    return {
      chunkIndex,
      upload
    };
  }

  console.log(`正在发布 ${packageName}...`);

  try {
    const publishArgs = buildPublishArgs();

    const { stdout, stderr } = await execFileAsync(
      'npm',
      publishArgs,
      {
        cwd: pkgDir,
        timeout: PUBLISH_TIMEOUT_MS,
        maxBuffer: 10 * 1024 * 1024
      }
    );

    if (stdout.trim()) {
      console.log(stdout.trim());
    }
    if (stderr.trim()) {
      console.error(stderr.trim());
    }

    console.log(`✅ 发布成功: ${packageName}`);
    return {
      chunkIndex,
      upload
    };
  } catch (error) {
    const detail = [
      error.stderr && error.stderr.toString().trim(),
      error.stdout && error.stdout.toString().trim(),
      error.message
    ]
      .filter(Boolean)
      .join('\n');

    throw new Error(`发布失败 ${packageName}\n${detail}`);
  }
}

function createEmptyRecord() {
  return {
    version: 1,
    updatedAt: null,
    artifacts: {}
  };
}

async function readUploadRecord(recordPath = path.resolve(UPLOAD_RECORD_FILE)) {
  if (!(await pathExists(recordPath))) {
    return createEmptyRecord();
  }

  const parsed = JSON.parse(await fsp.readFile(recordPath, 'utf8'));
  return {
    version: parsed.version || 1,
    updatedAt: parsed.updatedAt || null,
    artifacts: parsed.artifacts && typeof parsed.artifacts === 'object'
      ? parsed.artifacts
      : {}
  };
}

function buildUploadRecordUpdate(record, filePath, manifest, manifestPath, recordPath) {
  const sourceFilePath = path.resolve(filePath);
  const localManifestPath = path.resolve(manifestPath);
  const resolvedRecordPath = path.resolve(recordPath);
  const recordUpdatedAt = new Date().toISOString();
  const uploadId =
    manifest.uploadId ||
    `${manifest.artifactName}:${manifest.packageVersion}:${recordUpdatedAt}`;
  const artifactRecord = record.artifacts[manifest.artifactName] || {
    artifactName: manifest.artifactName,
    latest: null,
    history: []
  };
  const previousLatest = artifactRecord.latest || null;
  const historyLimit = parsePositiveInt(
    process.env.UPLOAD_RECORD_HISTORY_LIMIT,
    20
  );
  const history = KEEP_UPLOAD_HISTORY && previousLatest
    ? [previousLatest, ...(artifactRecord.history || [])].slice(0, historyLimit)
    : [];
  const snapshot = {
    ...cloneJson(manifest),
    uploadId,
    sourceFilePath,
    localManifestPath,
    recordFile: resolvedRecordPath,
    recordUpdatedAt
  };

  record.artifacts[manifest.artifactName] = {
    artifactName: manifest.artifactName,
    latestUploadId: uploadId,
    updatedAt: recordUpdatedAt,
    latest: snapshot,
    history
  };
  record.updatedAt = recordUpdatedAt;

  return {
    record,
    previousLatest,
    recordPath: resolvedRecordPath,
    recordUpdatedAt,
    uploadId,
    historyLength: history.length
  };
}

function collectVersionedPackages(snapshot) {
  const items = [];
  const seen = new Set();

  for (const chunk of snapshot?.chunks || []) {
    const packageName = chunk?.upload?.packageName;
    const version = chunk?.upload?.version;
    if (!packageName || !version) {
      continue;
    }

    const key = `${packageName}@${version}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    items.push({ packageName, version });
  }

  return items;
}

function findPackageVersionConflicts(previousLatest, packageInfos) {
  const previous = new Set(
    collectVersionedPackages(previousLatest).map(
      ({ packageName, version }) => `${packageName}@${version}`
    )
  );

  return packageInfos.filter(({ packageName, version }) =>
    previous.has(`${packageName}@${version}`)
  );
}

async function unpublishPreviousVersion({ packageName, version }) {
  const unpublishArgs = buildUnpublishArgs(packageName, version);

  const { stdout, stderr } = await execFileAsync('npm', unpublishArgs, {
    timeout: PUBLISH_TIMEOUT_MS,
    maxBuffer: 10 * 1024 * 1024
  });

  if (stdout.trim()) {
    console.log(stdout.trim());
  }
  if (stderr.trim()) {
    console.error(stderr.trim());
  }
}

async function prunePreviousUpload(previousLatest, currentUploadId) {
  if (
    DRY_RUN ||
    !PRUNE_REMOTE_UPLOADS ||
    !previousLatest ||
    previousLatest.uploadId === currentUploadId
  ) {
    return [];
  }

  const packages = collectVersionedPackages(previousLatest);
  if (!packages.length) {
    return [];
  }

  const warnings = [];
  for (const item of packages) {
    try {
      console.log(`正在删除旧版本 ${item.packageName}@${item.version}...`);
      await unpublishPreviousVersion(item);
      console.log(`🗑 已删除旧版本: ${item.packageName}@${item.version}`);
    } catch (error) {
      const detail = [
        error.stderr && error.stderr.toString().trim(),
        error.stdout && error.stdout.toString().trim(),
        error.message
      ]
        .filter(Boolean)
        .join('\n');
      const warning = `删除旧版本失败 ${item.packageName}@${item.version}\n${detail}`;
      console.error(`⚠ ${warning}`);
      warnings.push(warning);
    }
  }

  return warnings;
}

async function updateUploadRecord(filePath, manifest, manifestPath) {
  const recordPath = path.resolve(UPLOAD_RECORD_FILE);
  const record = await readUploadRecord(recordPath);
  const update = buildUploadRecordUpdate(record, filePath, manifest, manifestPath, recordPath);

  await writeJson(update.recordPath, update.record);

  return update;
}

async function rollbackPublishedPackages(results) {
  const warnings = [];
  for (const result of results.filter(Boolean)) {
    const item = {
      packageName: result.upload.packageName,
      version: result.upload.version
    };
    try {
      console.log(`正在回滚 ${item.packageName}@${item.version}...`);
      await unpublishPreviousVersion(item);
      console.log(`↩ 已回滚: ${item.packageName}@${item.version}`);
    } catch (error) {
      const detail = [
        error.stderr && error.stderr.toString().trim(),
        error.stdout && error.stdout.toString().trim(),
        error.message
      ]
        .filter(Boolean)
        .join('\n');
      warnings.push(`回滚失败 ${item.packageName}@${item.version}\n${detail}`);
    }
  }
  return warnings;
}

async function mapWithConcurrency(items, concurrency, worker) {
  if (!items.length) return [];

  const results = new Array(items.length);
  const failures = [];
  let cursor = 0;

  async function runWorker() {
    while (true) {
      const index = cursor;
      cursor += 1;

      if (index >= items.length) {
        return;
      }

      try {
        results[index] = await worker(items[index], index);
      } catch (error) {
        failures.push(error);
      }
    }
  }

  const workerCount = Math.min(concurrency, items.length);
  await Promise.all(Array.from({ length: workerCount }, runWorker));

  if (failures.length) {
    const error = new Error(failures.map((failure) => failure.message).join('\n\n'));
    error.results = results.filter(Boolean);
    throw error;
  }

  return results;
}

async function processFile(filePath, artifactNameOverride = '') {
  const parsed = path.parse(filePath);
  const artifactName = artifactNameOverride || sanitizeName(parsed.name);

  console.log(`加密并切分文件: ${filePath}`);
  const { manifest, manifestPath, shardRoot } = await encryptAndSplitFile(
    filePath,
    artifactName
  );

  const packageInfos = await Promise.all(
    Array.from({ length: manifest.totalChunks }, (_, chunkIndex) =>
      createNpmPackage(
        shardRoot,
        manifestPath,
        artifactName,
        chunkIndex,
        manifest.totalChunks,
        manifest.packageVersion
      )
    )
  );

  const recordPath = path.resolve(UPLOAD_RECORD_FILE);
  const record = await readUploadRecord(recordPath);
  const previousLatest = record.artifacts[artifactName]?.latest || null;
  const conflicts = findPackageVersionConflicts(previousLatest, packageInfos);
  if (conflicts.length) {
    const versions = conflicts
      .map(({ packageName, version }) => `${packageName}@${version}`)
      .join(', ');
    throw new Error(
      `上传版本已存在，拒绝覆盖不可变的 npm 包版本: ${versions}。请设置新的 NPM_PACKAGE_VERSION。`
    );
  }

  let publishResults;
  try {
    publishResults = await mapWithConcurrency(
      packageInfos,
      PUBLISH_CONCURRENCY,
      ({ chunkIndex, packageName, pkgDir, version }) =>
        publishPackage(chunkIndex, packageName, version, pkgDir)
    );
  } catch (error) {
    const rollbackWarnings = await rollbackPublishedPackages(error.results || []);
    if (rollbackWarnings.length) {
      error.message += `\n\n${rollbackWarnings.join('\n\n')}`;
    }
    throw error;
  }

  for (const result of publishResults) {
    if (!result) continue;
    manifest.chunks[result.chunkIndex] = {
      ...manifest.chunks[result.chunkIndex],
      upload: result.upload
    };
  }

  manifest.lastUpdatedAt = new Date().toISOString();
  const recordInfo = await updateUploadRecord(filePath, manifest, manifestPath);
  const cleanupWarnings = await prunePreviousUpload(
    recordInfo.previousLatest,
    recordInfo.uploadId
  );
  manifest.record = {
    filePath: recordInfo.recordPath,
    latestUploadId: recordInfo.uploadId,
    updatedAt: recordInfo.recordUpdatedAt,
    historyLength: recordInfo.historyLength
  };
  if (cleanupWarnings.length) {
    manifest.record.cleanupWarnings = cleanupWarnings;
  }
  await writeJson(manifestPath, manifest);
  console.log(`已写入上传记录: ${manifestPath}`);
  console.log(`已更新唯一记录文件: ${recordInfo.recordPath}`);
}

async function main() {
  if (PASSWORD === DEFAULT_PASSWORD) {
    console.warn(
      '警告: 当前仍在使用默认密码，建议设置 SHARD_PASSWORD 环境变量后再执行。'
    );
  }

  if (INPUT_FILES.length > 1 && ARTIFACT_NAME_OVERRIDE) {
    throw new Error('指定 --artifact / ARTIFACT_NAME 时，一次只能处理一个输入文件。');
  }

  let files = INPUT_FILES;
  if (!files.length) {
    await fsp.mkdir(FILES_DIR, { recursive: true });

    const entries = await fsp.readdir(FILES_DIR, { withFileTypes: true });
    files = entries
      .filter((entry) => entry.isFile())
      .map((entry) => path.join(FILES_DIR, entry.name));
  }

  if (!files.length) {
    console.log(
      INPUT_FILES.length
        ? '未找到可处理的输入文件。'
        : `未在 ${FILES_DIR} 目录中找到待处理文件。`
    );
    return;
  }

  for (const file of files) {
    await processFile(file, ARTIFACT_NAME_OVERRIDE);
  }
}

if (IS_MAIN) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}

export {
  buildPublishArgs,
  buildUnpublishArgs,
  buildUploadRecordUpdate,
  collectVersionedPackages,
  createEmptyRecord,
  findPackageVersionConflicts,
  mapWithConcurrency,
  prunePreviousUpload,
  readUploadRecord
};
