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
missing download fails closed. Backup Restore Synthetic and Canary Offline's
`minio-compat` job also build this fixture. The canary `offline` job stays unchanged;
its compatibility job extracts mc from `/usr/local/bin/mc` and uses loopback HTTP
as before (not a real Selectel connection). These consumers require their own CI
proof, in addition to the PITR workflows. Their path filters cover this fixture.
Rollback is a revert of this slice (restoring the old, currently unavailable fixture);
there is no data migration and no production change. Based on OpenSSL fix PR #331.
