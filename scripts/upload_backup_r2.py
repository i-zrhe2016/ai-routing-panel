#!/usr/bin/env python3
"""Encrypt and upload disaster bundles to Cloudflare R2 via its S3 API."""
import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

MAGIC = b"XRAY-R2-AES256GCM\x01"


def _required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required when R2 backup upload is enabled")
    return value


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_endpoint(endpoint, bucket):
    """Accept the current R2 endpoint and the legacy bucket-suffixed form."""
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("DB_BACKUP_R2_ENDPOINT must use HTTPS")
    if parsed.query or parsed.fragment:
        raise RuntimeError("DB_BACKUP_R2_ENDPOINT must not include a query or fragment")

    path = parsed.path.rstrip("/")
    legacy_bucket_path = f"/{bucket}"
    if path not in ("", legacy_bucket_path):
        raise RuntimeError(
            "DB_BACKUP_R2_ENDPOINT must be the R2 base endpoint or include only the bucket path"
        )
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def encrypt_bundle(bundle_path, output_path=None, password=None):
    """Encrypt a bundle with AES-256-GCM using a password-derived key."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    except ImportError as exc:
        raise RuntimeError("cryptography is required for disaster bundle encryption") from exc
    password = password or _required("DB_BACKUP_ENCRYPTION_PASSWORD")
    source = Path(bundle_path)
    target = Path(output_path or f"{source}.enc")
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    key = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(password.encode())
    ciphertext = AESGCM(key).encrypt(nonce, source.read_bytes(), MAGIC)
    target.write_bytes(MAGIC + salt + nonce + ciphertext)
    os.chmod(target, 0o600)
    return target


def object_key(bundle_path, prefix="xray-routing-panel", digest=None):
    digest = digest or _sha256(bundle_path)
    stamp = datetime.now(timezone.utc).strftime("%Y/%m/%d/%Y%m%dT%H%M%SZ")
    return f"{prefix.strip('/')}/{stamp}-{digest[:16]}-{Path(bundle_path).name}"


def upload_bundle(bundle_path, record_path=None, client=None):
    bundle = Path(bundle_path)
    if not bundle.is_file():
        raise FileNotFoundError(bundle)
    bucket = _required("DB_BACKUP_R2_BUCKET")
    endpoint = _required("DB_BACKUP_R2_ENDPOINT")
    access_key = _required("DB_BACKUP_R2_ACCESS_KEY_ID")
    secret_key = _required("DB_BACKUP_R2_SECRET_ACCESS_KEY")
    endpoint = normalize_endpoint(endpoint, bucket)
    digest = _sha256(bundle)
    if client is None:
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("boto3 is required for R2 backup upload") from exc
        client = boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=access_key,
                              aws_secret_access_key=secret_key,
                              region_name=os.environ.get("DB_BACKUP_R2_REGION", "auto"))
    key = object_key(bundle, os.environ.get("DB_BACKUP_R2_PREFIX", "xray-routing-panel"), digest)
    with bundle.open("rb") as source:
        client.upload_fileobj(source, bucket, key, ExtraArgs={"ContentType": "application/octet-stream"})
    record = {"uploadedAt": datetime.now(timezone.utc).isoformat(), "bucket": bucket,
              "endpoint": endpoint, "key": key, "sha256": digest, "size": bundle.stat().st_size}
    if record_path:
        path = Path(record_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
    print(f"[backup:r2] uploaded key={key} size={record['size']}", flush=True)
    return record


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle")
    args = parser.parse_args()
    upload_bundle(args.bundle, os.environ.get("DB_BACKUP_R2_RECORD_PATH"))
