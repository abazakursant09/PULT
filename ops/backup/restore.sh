#!/bin/sh
# SECURITY-2D-3E1B-3A — restore runner: fetch -> verify size+sha256 -> age decrypt ->
# verify plaintext sha256 -> pg_restore --list -> assert target EMPTY -> pg_restore into a
# NEW empty target (never --clean against production) -> integrity. Fail-closed POSIX sh.
set -eu
umask 077

log() { printf '%s restore: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1"; }
die() { printf '%s restore: FAIL: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" >&2; exit 1; }

: "${PGHOST:?PGHOST (target) required}"; : "${PGDATABASE:?PGDATABASE (target) required}"; : "${PGUSER:?required}"
: "${RESTORE_S3_ENDPOINT:?required}"; : "${RESTORE_S3_BUCKET:?required}"; : "${RESTORE_S3_ACCESS_KEY_ID:?required}"
: "${RESTORE_S3_SECRET_ACCESS_KEY:?required}"; : "${RESTORE_OBJECT_KEY:?RESTORE_OBJECT_KEY required}"
: "${BACKUP_RESTORE_IDENTITY_FILE:?age private identity file required}"
PGPORT="${PGPORT:-5432}"
if [ -n "${PGPASSWORD_FILE:-}" ]; then PGPASSWORD="$(cat "$PGPASSWORD_FILE")"; export PGPASSWORD; fi
[ -f "$BACKUP_RESTORE_IDENTITY_FILE" ] || die "identity file not found"

export RCLONE_CONFIG_RD_TYPE=s3
export RCLONE_CONFIG_RD_PROVIDER="${RESTORE_S3_PROVIDER:-Other}"
export RCLONE_CONFIG_RD_ACCESS_KEY_ID="$RESTORE_S3_ACCESS_KEY_ID"
export RCLONE_CONFIG_RD_SECRET_ACCESS_KEY="$RESTORE_S3_SECRET_ACCESS_KEY"
export RCLONE_CONFIG_RD_ENDPOINT="$RESTORE_S3_ENDPOINT"
export RCLONE_CONFIG_RD_REGION="${RESTORE_S3_REGION:-}"
export RCLONE_CONFIG_RD_FORCE_PATH_STYLE=true

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT INT TERM
CIPHER="$TMP/db.dump.age"; PLAIN="$TMP/db.dump"; MANIFEST="$TMP/manifest.json"
KEY="$RESTORE_OBJECT_KEY"; MKEY="${KEY}.manifest.json"

log "fetch manifest + ciphertext"
rclone copyto "rd:${RESTORE_S3_BUCKET}/${MKEY}" "$MANIFEST" || die "manifest fetch failed"
rclone copyto "rd:${RESTORE_S3_BUCKET}/${KEY}" "$CIPHER" || die "ciphertext fetch failed"
[ -s "$CIPHER" ] || die "fetched ciphertext is empty"

MAN_CIPHER_SHA="$(sed -n 's/.*"ciphertext_sha256"[: ]*"\([0-9a-f]*\)".*/\1/p' "$MANIFEST")"
MAN_CIPHER_SIZE="$(sed -n 's/.*"ciphertext_size"[: ]*\([0-9][0-9]*\).*/\1/p' "$MANIFEST")"
MAN_PLAIN_SHA="$(sed -n 's/.*"plaintext_sha256"[: ]*"\([0-9a-f]*\)".*/\1/p' "$MANIFEST")"
[ -n "$MAN_CIPHER_SHA" ] || die "manifest missing ciphertext_sha256"

# HeadObject-equivalent remote size + local checksum must match the manifest.
REMOTE_SIZE="$(rclone lsjson --stat "rd:${RESTORE_S3_BUCKET}/${KEY}" 2>/dev/null | sed -n 's/.*"Size"[: ]*\([0-9][0-9]*\).*/\1/p' | head -1)"
[ "$REMOTE_SIZE" = "$MAN_CIPHER_SIZE" ] || die "remote size $REMOTE_SIZE != manifest $MAN_CIPHER_SIZE"
sha256sum "$CIPHER" > "$TMP/c.sha"; GOT_CIPHER_SHA="$(cut -d' ' -f1 "$TMP/c.sha")"
[ "$GOT_CIPHER_SHA" = "$MAN_CIPHER_SHA" ] || die "ciphertext sha256 mismatch (corrupt/tampered)"

log "decrypt (age identity)"
age -d -i "$BACKUP_RESTORE_IDENTITY_FILE" -o "$PLAIN" "$CIPHER" || die "decryption failed"
[ -s "$PLAIN" ] || die "decrypted plaintext empty"
sha256sum "$PLAIN" > "$TMP/p.sha"; GOT_PLAIN_SHA="$(cut -d' ' -f1 "$TMP/p.sha")"
[ "$GOT_PLAIN_SHA" = "$MAN_PLAIN_SHA" ] || die "plaintext sha256 mismatch"
pg_restore --list "$PLAIN" >/dev/null 2>&1 || die "pg_restore --list rejected the dump"

# Target MUST be empty — restore never runs --clean against an existing database.
NTAB="$(psql -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" | tr -d ' ')"
[ "$NTAB" = "0" ] || die "target is not empty ($NTAB public tables) — refusing (restore only into a NEW empty DB)"

log "pg_restore into empty target"
pg_restore --exit-on-error --no-owner --no-privileges --dbname="$PGDATABASE" "$PLAIN" || die "pg_restore failed"

log "integrity verification"
integrity.sh || die "integrity verification failed"
log "SUCCESS restored key=$KEY"
