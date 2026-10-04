# Synthetic CI only. Official release binaries, verified by BuildKit before execution.
FROM alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6
RUN test "$(uname -m)" = x86_64
ADD --checksum=sha256:7c5bd8512c6e966455b1d198209358b2d191c77a83ab377c4073281065fb855f --chmod=0755 https://github.com/minio/minio/releases/download/RELEASE.2025-09-07T16-13-09Z/minio.linux-amd64.RELEASE.2025-09-07T16-13-09Z /usr/local/bin/minio
ADD --checksum=sha256:01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891 --chmod=0755 https://github.com/minio/mc/releases/download/RELEASE.2025-08-13T08-35-41Z/mc.linux-amd64.RELEASE.2025-08-13T08-35-41Z /usr/local/bin/mc
RUN minio --version | grep -F 'RELEASE.2025-09-07T16-13-09Z' && mc --version | grep -F 'RELEASE.2025-08-13T08-35-41Z'
ENTRYPOINT ["minio"]
CMD ["server", "/data"]
