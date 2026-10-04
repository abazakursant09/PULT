# Synthetic MinIO fixture (not production)

Build locally in GitHub Actions; never publish this image. Linux/amd64 only.
The old Docker Hub reference is unavailable. Keep the server release unchanged;
use official GitHub release binaries for MinIO and mc with BuildKit SHA-256 checks.
Checksums were cross-checked against release API digest fields and upstream
`.sha256sum` assets on 2026-10-04. Alpine 3.24 index digest was resolved from the
official library/alpine registry. No runtime package installation or floating downloads.

The fixture preserves `minio server /data`, root certificate directory, environment
credentials and `--entrypoint sh`/mc use. Existing TLS verification settings and
synthetic test data remain unchanged. The existing mc bootstrap uses --insecure;
this change does not broaden that exception or disable pgBackRest TLS verification.

Acceptance requires both synthetic and extended PITR workflows, not just a build.
Official binary availability remains an external dependency; checksum mismatch or
missing download fails closed. This does not fix other workflows using the old image.
Rollback is a revert of this slice (restoring the old, currently unavailable fixture);
there is no data migration and no production change. Based on OpenSSL fix PR #331.
