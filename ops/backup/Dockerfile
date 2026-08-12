# SECURITY-2D-3E1B-3A — least-privilege PostgreSQL backup/restore runner.
#
# Base is the SAME digest-pinned PostgreSQL image the production DB runs (so pg_dump /
# pg_restore / psql are version-identical to production — SECURITY-2D-3E1B-2). Only two
# extra tools are added, each downloaded by EXACT version over TLS and verified by SHA-256
# at BUILD time via BuildKit `ADD --checksum` (no `apk add`, no `curl | sh`, no latest URL,
# no runtime download):
#   - rclone v1.75.0  (S3 client)      sha256 aa2804e0…cabafbaa  (official SHA256SUMS)
#   - age    v1.3.1   (file encryption) sha256 bdc69c09…f51377  (GitHub release asset, TLS)
FROM postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777

USER root

# Pinned + hash-verified downloads. BuildKit refuses the layer if the artifact's SHA-256 does
# not match, so a repointed release or a MITM cannot slip in a different binary.
ADD --checksum=sha256:aa2804e08f48250e71009c727124b6341cd0288465804a9a09d14663cabafbaa \
    https://github.com/rclone/rclone/releases/download/v1.75.0/rclone-v1.75.0-linux-amd64.zip /tmp/rclone.zip
ADD --checksum=sha256:bdc69c09cbdd6cf8b1f333d372a1f58247b3a33146406333e30c0f26e8f51377 \
    https://github.com/FiloSottile/age/releases/download/v1.3.1/age-v1.3.1-linux-amd64.tar.gz /tmp/age.tgz

RUN set -eu; \
    cd /tmp; \
    unzip -q rclone.zip; \
    install -m 0755 rclone-v1.75.0-linux-amd64/rclone /usr/local/bin/rclone; \
    tar -xzf age.tgz; \
    install -m 0755 age/age /usr/local/bin/age; \
    install -m 0755 age/age-keygen /usr/local/bin/age-keygen; \
    rm -rf /tmp/rclone.zip /tmp/rclone-v1.75.0-linux-amd64 /tmp/age.tgz /tmp/age; \
    # dedicated non-root runner identity (distinct from postgres uid 70 and the app uid 10001)
    addgroup -g 10002 backup; \
    adduser -D -H -u 10002 -G backup -s /sbin/nologin backup; \
    mkdir -p /work; chown 10002:10002 /work; \
    rclone version | head -1; age --version

# Runner scripts are baked in (self-contained image; no host bind mounts of code at runtime).
COPY --chmod=0755 backup.sh restore.sh integrity.sh seed_synthetic.sh ci_negative.sh /usr/local/bin/

WORKDIR /work
USER 10002:10002
# Reset the postgres image entrypoint so this runner never tries to start a database.
ENTRYPOINT []
CMD ["backup.sh"]
