import os
import tempfile
import unittest
from pathlib import Path

from scripts.upload_backup_r2 import object_key, upload_bundle


class R2UploadTest(unittest.TestCase):
    def test_object_key_contains_digest_and_bundle_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bundle.tar.gz"
            path.write_bytes(b"encrypted")
            key = object_key(path, "backups")
            self.assertTrue(key.startswith("backups/"))
            self.assertIn("bundle.tar.gz", key)

    def test_upload_requires_explicit_configuration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bundle.tar.gz"
            path.write_bytes(b"encrypted")
            old = os.environ.pop("DB_BACKUP_R2_BUCKET", None)
            try:
                with self.assertRaisesRegex(RuntimeError, "DB_BACKUP_R2_BUCKET"):
                    upload_bundle(path, client=object())
            finally:
                if old is not None:
                    os.environ["DB_BACKUP_R2_BUCKET"] = old

    def test_upload_records_metadata_without_secret(self):
        class Client:
            def upload_fileobj(self, source, bucket, key, ExtraArgs):
                self.args = (bucket, key, ExtraArgs, source.read())

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bundle.tar.gz"
            path.write_bytes(b"encrypted")
            os.environ.update({
                "DB_BACKUP_R2_BUCKET": "bucket",
                "DB_BACKUP_R2_ENDPOINT": "https://account.r2.cloudflarestorage.com",
                "DB_BACKUP_R2_ACCESS_KEY_ID": "access",
                "DB_BACKUP_R2_SECRET_ACCESS_KEY": "secret",
            })
            client = Client()
            record = upload_bundle(path, Path(tmpdir) / "record.json", client=client)
            self.assertEqual(record["bucket"], "bucket")
            self.assertNotIn("secret", record)
            self.assertEqual(client.args[3], b"encrypted")

    def test_upload_normalizes_legacy_bucket_suffixed_endpoint(self):
        class Client:
            def upload_fileobj(self, source, bucket, key, ExtraArgs):
                self.args = (bucket, key, ExtraArgs, source.read())

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bundle.tar.gz"
            path.write_bytes(b"encrypted")
            os.environ.update({
                "DB_BACKUP_R2_BUCKET": "bucket",
                "DB_BACKUP_R2_ENDPOINT": "https://account.r2.cloudflarestorage.com/bucket",
                "DB_BACKUP_R2_ACCESS_KEY_ID": "access",
                "DB_BACKUP_R2_SECRET_ACCESS_KEY": "secret",
            })
            record = upload_bundle(path, client=Client())
            self.assertEqual(record["endpoint"], "https://account.r2.cloudflarestorage.com")


if __name__ == "__main__":
    unittest.main()
