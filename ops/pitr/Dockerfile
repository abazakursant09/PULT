# SECURITY-2D-3E1B-3B1 — PITR runner image: pinned PostgreSQL base + pgBackRest 2.59.0 built
# from the hash-verified upstream source, with an EXACT-PINNED Alpine dependency closure
# (discovered on this pinned base in CI: Alpine 3.24.1, repos v3.24 main+community).
#
# Supply chain:
#   - pgBackRest source: ADD --checksum sha256 faaf8faa… (upstream .sha256sum, verified).
#   - Every apk package is pinned name=version-rN. A package disappearing from the signed
#     Alpine repo breaks the build fail-closed (acceptable operational residual; no auto-bump).
#   - Final runtime shared-lib closure (scanelf NEEDED): libbz2/libssl3/libcrypto3/lz4-libs/
#     libpq(base)/libxml2/zlib/zstd-libs + musl(base). Build toolchain never reaches the final stage.

# ---- builder ----
FROM postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777 AS builder
RUN apk add --no-cache \
      build-base=0.5-r4 \
      meson=1.11.1-r0 \
      samurai=1.2-r8 \
      pkgconf=2.5.1-r0 \
      postgresql18-dev=18.4-r0 \
      openssl-dev=3.5.7-r0 \
      zlib-dev=1.3.2-r0 \
      lz4-dev=1.10.0-r1 \
      libxml2-dev=2.13.9-r2 \
      bzip2-dev=1.0.8-r6 \
      zstd-dev=1.5.7-r2 \
      yaml-dev=0.2.5-r2
ADD --checksum=sha256:faaf8faa14a6392279654ee216a493fcd07b0c513af4b55fe34faec062cb8875 \
    https://github.com/pgbackrest/pgbackrest/releases/download/release/2.59.0/pgbackrest-2.59.0.tar.gz /tmp/pgbr.tgz
RUN set -eux; \
    cd /tmp; tar xzf pgbr.tgz; cd pgbackrest-2.59.0; \
    meson setup build . -Dlibssh2=disabled -Dlibsystemd=disabled; \
    ninja -C build; \
    install -m 0755 build/src/pgbackrest /usr/bin/pgbackrest; \
    /usr/bin/pgbackrest version | grep -q 'pgBackRest 2.59.0'

# ---- final runtime (SAME pinned base; only pgBackRest + its runtime libs; no build tools) ----
FROM postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777
RUN apk add --no-cache \
      libbz2=1.0.8-r6 \
      libssl3=3.5.7-r0 \
      libcrypto3=3.5.7-r0 \
      lz4-libs=1.10.0-r1 \
      libxml2=2.13.9-r2 \
      zlib=1.3.2-r0 \
      zstd-libs=1.5.7-r2
COPY --from=builder /usr/bin/pgbackrest /usr/bin/pgbackrest
# pgBackRest runtime dirs, owned by the image-native postgres user (uid 70). No recursive
# chown of PGDATA. The official postgres entrypoint/user/CMD are inherited unchanged.
RUN set -eux; \
    install -d -o postgres -g postgres -m 0750 /etc/pgbackrest /var/lib/pgbackrest /var/log/pgbackrest /var/spool/pgbackrest; \
    /usr/bin/pgbackrest version | grep -q 'pgBackRest 2.59.0'; \
    ldd /usr/bin/pgbackrest | grep -q 'not found' && { echo 'FAIL: unresolved shared lib'; exit 1; } || true; \
    ! command -v gcc >/dev/null 2>&1 && ! command -v meson >/dev/null 2>&1 || { echo 'FAIL: build tools leaked into final image'; exit 1; }
