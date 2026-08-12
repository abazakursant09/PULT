#!/bin/sh
# SECURITY-2D-3E1B-3A — backup runner: pg_dump (custom) -> age encrypt -> checksum ->
# S3 upload -> scoped verify. Fail-closed. POSIX /bin/sh: `set -eu`, NO pipefail; no
# critical pipeline where a first-stage failure could be lost (each step writes a file and
# its exit code is checked directly). Plaintext never reaches a log or an artifact.
set -eu
umask 077

log() { printf '%s backup: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1"; }
die() { printf '%s backup: FAIL: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" >&2; exit 1; }

: "${PGHOST:?PGHOST required}"; : "${PGDATABASE:?PGDATABASE required}"; : "${PGUSER:?PGUSER required}"
: "${BACKUP_S3_ENDPOINT:?BACKUP_S3_ENDPOINT required}"; : "${BACKUP_S3_BUCKET:?BACKUP_S3_BUCKET required}"
: "${BACKUP_S3_PREFIX:?BACKUP_S3_PREFIX required}"; : "${BACKUP_S3_ACCESS_KEY_ID:?required}"
: "${BACKUP_S3_SECRET_ACCESS_KEY:?required}"; : "${BACKUP_ENCRYPTION_RECIPIENT:?age recipient required}"
PGPORT="${PGPORT:-5432}"
# PGPASSWORD may come from a file (never on the command line, never logged).
if [ -n "${PGPASSWORD_FILE:-}" ]; then PGPASSWORD="$(cat "$PGPASSWORD_FILE")"; export PGPASSWORD; fi

# rclone S3 remote configured purely from env (secret never appears on argv). TLS verification
# is left at rclone's secure default — this script has NO flag that disables it.
export RCLONE_CONFIG_BK_TYPE=s3
export RCLONE_CONFIG_BK_PROVIDER="${BACKUP_S3_PROVIDER:-Other}"
export RCLONE_CONFIG_BK_ACCESS_KEY_ID="$BACKUP_S3_ACCESS_KEY_ID"
export RCLONE_CONFIG_BK_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET_ACCESS_KEY"
export RCLONE_CONFIG_BK_ENDPOINT="$BACKUP_S3_ENDPOINT"
export RCLONE_CONFIG_BK_REGION="${BACKUP_S3_REGION:-}"
export RCLONE_CONFIG_BK_FORCE_PATH_STYLE=true

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT INT TERM
PLAIN="$TMP/db.dump"
CIPHER="$TMP/db.dump.age"
MANIFEST="$TMP/manifest.json"

RUN_UUID="$(cat /proc/sys/kernel/random/uuid)"
CREATED_AT="$(date -u +%Y%m%dT%H%M%SZ)"
KEY="${BACKUP_S3_PREFIX%/}/pult-pg-${CREATED_AT}-${RUN_UUID}.dump.age"
MKEY="${KEY}.manifest.json"

START="$(date -u +%s)"

log "pg_dump (custom format) start"
pg_dump --format=custom --no-password --file="$PLAIN" || die "pg_dump failed"
[ -s "$PLAIN" ] || die "dump is empty"
pg_restore --list "$PLAIN" >/dev/null 2>&1 || die "pg_restore --list rejected the dump"

sha256sum "$PLAIN" > "$TMP/plain.sha"
PLAIN_SHA="$(cut -d' ' -f1 "$TMP/plain.sha")"
PLAIN_SIZE="$(wc -c < "$PLAIN" | tr -d ' ')"

# Best-effort metadata that a least-privilege role can read; omitted honestly if not permitted.
SERVER_VERSION="$(psql -tAc 'SHOW server_version' 2>/dev/null || echo unavailable)"
ALEMBIC_HEAD="$(psql -tAc 'SELECT version_num FROM alembic_version' 2>/dev/null || echo unavailable)"
SYSID="$(psql -tAc 'SELECT system_identifier FROM pg_control_system()' 2>/dev/null || echo unavailable)"

log "encrypt (age recipient)"
age -r "$BACKUP_ENCRYPTION_RECIPIENT" -o "$CIPHER" "$PLAIN" || die "encryption failed — nothing uploaded"
[ -s "$CIPHER" ] || die "ciphertext is empty — nothing uploaded"
sha256sum "$CIPHER" > "$TMP/cipher.sha"
CIPHER_SHA="$(cut -d' ' -f1 "$TMP/cipher.sha")"
CIPHER_SIZE="$(wc -c < "$CIPHER" | tr -d ' ')"

# Manifest: NO PII (no email/store/user IDs), NO credentials, NO SQL, NO row data, no hostnames.
cat > "$MANIFEST" <<JSON
{
  "format_version": 1,
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "run_uuid": "$RUN_UUID",
  "postgres_server_version": "$SERVER_VERSION",
  "system_identifier": "$SYSID",
  "alembic_head": "$ALEMBIC_HEAD",
  "dump_format": "custom",
  "encryption": "age-x25519-recipient",
  "plaintext_sha256": "$PLAIN_SHA",
  "plaintext_size": $PLAIN_SIZE,
  "ciphertext_sha256": "$CIPHER_SHA",
  "ciphertext_size": $CIPHER_SIZE,
  "object_key": "$KEY",
  "tool_rclone": "v1.75.0",
  "tool_age": "v1.3.1"
}
JSON

log "upload ciphertext + manifest"
rclone copyto "$CIPHER" "bk:${BACKUP_S3_BUCKET}/${KEY}" || die "ciphertext upload failed"
rclone copyto "$MANIFEST" "bk:${BACKUP_S3_BUCKET}/${MKEY}" || die "manifest upload failed"

# Scoped verify (HeadObject-equivalent): the object exists and its remote size matches the
# ciphertext we uploaded. A bare upload is NOT treated as success without this.
REMOTE_SIZE="$(rclone lsjson --stat "bk:${BACKUP_S3_BUCKET}/${KEY}" 2>/dev/null | sed -n 's/.*"Size"[: ]*\([0-9][0-9]*\).*/\1/p' | head -1)"
[ -n "$REMOTE_SIZE" ] || die "verify: uploaded object not found (HeadObject empty)"
[ "$REMOTE_SIZE" = "$CIPHER_SIZE" ] || die "verify: remote size $REMOTE_SIZE != local $CIPHER_SIZE"

END="$(date -u +%s)"
log "SUCCESS key=$KEY ciphertext_size=$CIPHER_SIZE ciphertext_sha256=$CIPHER_SHA duration_s=$((END-START))"
