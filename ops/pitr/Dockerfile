# SECURITY-2D-3E1B-3B1 — PITR runner image: pinned PostgreSQL base + pgBackRest 2.59.0 built
# from the hash-verified upstream source, with an EXACT-PINNED Alpine dependency closure
# (discovered on this pinned base in CI: Alpine 3.24.1, repos v3.24 main+community).
#
# Supply chain:
#   - pgBackRest source: ADD --checksum sha256 faaf8faa… (upstream .sha256sum, verified).
#   - Every apk package is pinned name=version-rN. A package disappearing from the signed
#     Alpine repo breaks the build fail-closed (acceptable operational residual; no auto-bump).
#   - PG16/libpq PARITY: pgBackRest is built against the base image's OWN PostgreSQL 16.14
#     libpq/headers (via pg_config + /usr/local/lib/pkgconfig). NO postgresql18* package is
#     installed in either stage; the final image never receives a PostgreSQL 18 client/server.
#   - Final runtime shared-lib closure (scanelf NEEDED): libbz2/libssl3/libcrypto3/lz4-libs/
#     libpq(base PG16)/libxml2/zlib/zstd-libs + musl(base). Build toolchain never reaches final.

# ---- builder ----
FROM postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777 AS builder
# libpq comes from the base's own PG16 install under /usr/local (NOT an apk postgresql-dev).
ENV PKG_CONFIG_PATH=/usr/local/lib/pkgconfig
RUN apk add --no-cache \
      build-base=0.5-r4 \
      meson=1.11.1-r0 \
      samurai=1.2-r8 \
      pkgconf=2.5.1-r0 \
      openssl-dev=3.5.8-r0 \
      zlib-dev=1.3.2-r0 \
      lz4-dev=1.10.0-r1 \
      libxml2-dev=2.13.9-r2 \
      bzip2-dev=1.0.8-r6 \
      zstd-dev=1.5.7-r2 \
      yaml-dev=0.2.5-r2
# PG16/libpq parity evidence + fail-closed gate: prove we link the base's PG16 libpq and that
# NO postgresql18 dev package is present.
RUN set -eux; \
    pg_config --version | grep -q 'PostgreSQL 16\.'; \
    echo "includedir=$(pg_config --includedir) libdir=$(pg_config --libdir)"; \
    pkg-config --modversion libpq; \
    pkg-config --modversion libpq | grep -q '^16\.'; \
    test -f "$(pg_config --includedir)/libpq-fe.h"; \
    ! apk info -e postgresql18-dev 2>/dev/null; \
    ! apk info -e postgresql18-client 2>/dev/null
ADD --checksum=sha256:faaf8faa14a6392279654ee216a493fcd07b0c513af4b55fe34faec062cb8875 \
    https://github.com/pgbackrest/pgbackrest/releases/download/release/2.59.0/pgbackrest-2.59.0.tar.gz /tmp/pgbr.tgz
RUN set -eux; \
    cd /tmp; tar xzf pgbr.tgz; cd pgbackrest-2.59.0; \
    meson setup build . -Dlibssh2=disabled -Dlibsystemd=disabled; \
    ninja -C build; \
    install -m 0755 build/src/pgbackrest /usr/bin/pgbackrest; \
    /usr/bin/pgbackrest version | grep -q 'pgBackRest 2.59.0'; \
    echo "=== readelf NEEDED ==="; readelf -d /usr/bin/pgbackrest | grep NEEDED; \
    echo "=== ldd ==="; ldd /usr/bin/pgbackrest; \
    ldd /usr/bin/pgbackrest | awk '/libpq\.so/{print $3}' | grep -q '^/usr/local/lib/'

# ---- final runtime (SAME pinned base; only pgBackRest + its runtime libs; no build tools) ----
FROM postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777
RUN apk add --no-cache \
      libbz2=1.0.8-r6 \
      libssl3=3.5.8-r0 \
      libcrypto3=3.5.8-r0 \
      lz4-libs=1.10.0-r1 \
      libxml2=2.13.9-r2 \
      zlib=1.3.2-r0 \
      zstd-libs=1.5.7-r2
COPY --from=builder /usr/bin/pgbackrest /usr/bin/pgbackrest
# PITR runner scripts baked in (self-contained; no host bind mount of code at runtime).
COPY --chmod=0755 restore.sh status.sh /usr/local/bin/
# pgBackRest runtime dirs, owned by the image-native postgres user (uid 70). No recursive
# chown of PGDATA. The official postgres entrypoint/user/CMD are inherited unchanged.
RUN set -eux; \
    install -d -o postgres -g postgres -m 0750 /etc/pgbackrest /var/lib/pgbackrest /var/log/pgbackrest /var/spool/pgbackrest; \
    /usr/bin/pgbackrest version | grep -q 'pgBackRest 2.59.0'; \
    pg_config --version | grep -q 'PostgreSQL 16\.'; \
    ldd /usr/bin/pgbackrest | grep -q 'not found' && { echo 'FAIL: unresolved shared lib'; exit 1; } || true; \
    ldd /usr/bin/pgbackrest | awk '/libpq\.so/{print $3}' | grep -q '^/usr/local/lib/'; \
    ! apk info -e postgresql18-client 2>/dev/null || { echo 'FAIL: PG18 package in final image'; exit 1; }; \
    ! command -v gcc >/dev/null 2>&1 && ! command -v meson >/dev/null 2>&1 || { echo 'FAIL: build tools leaked into final image'; exit 1; }
