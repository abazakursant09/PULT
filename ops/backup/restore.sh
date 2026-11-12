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
# A concrete NEW database name only, never libpq conninfo or a system database.
case "$PGDATABASE" in postgres|template0|template1|*[!a-zA-Z0-9_-]*) die "invalid restore target name";; esac
[ "${#PGDATABASE}" -le 63 ] || die "invalid restore target name"
if [ -n "${PGPASSWORD_FILE:-}" ]; then PGPASSWORD="$(cat "$PGPASSWORD_FILE")"; export PGPASSWORD; fi
[ -f "$BACKUP_RESTORE_IDENTITY_FILE" ] || die "identity file not found"

export RCLONE_CONFIG_RD_TYPE=s3
export RCLONE_CONFIG_RD_PROVIDER="${RESTORE_S3_PROVIDER:-Other}"
export RCLONE_CONFIG_RD_ACCESS_KEY_ID="$RESTORE_S3_ACCESS_KEY_ID"
export RCLONE_CONFIG_RD_SECRET_ACCESS_KEY="$RESTORE_S3_SECRET_ACCESS_KEY"
export RCLONE_CONFIG_RD_ENDPOINT="$RESTORE_S3_ENDPOINT"
export RCLONE_CONFIG_RD_REGION="${RESTORE_S3_REGION:-}"
export RCLONE_CONFIG_RD_FORCE_PATH_STYLE=true

TMP="$(mktemp -d /work/pult-restore.XXXXXX)" || die "cannot allocate private /work directory"
trap 'rm -rf "$TMP"' EXIT INT TERM
CIPHER="$TMP/db.dump.age"; PLAIN="$TMP/db.dump"; MANIFEST="$TMP/manifest.json"
KEY="$RESTORE_OBJECT_KEY"; MKEY="${KEY}.manifest.json"

# Trust input is external to the restored database AND to the backup/manifest.
# Missing/untrusted/nonempty history blocks before any fetch/decrypt/database work.
: "${RESTORE_ERASURE_CHECKPOINT_FILE:?external complete checkpoint required}"
[ -f "$RESTORE_ERASURE_CHECKPOINT_FILE" ] && [ ! -L "$RESTORE_ERASURE_CHECKPOINT_FILE" ] || die "invalid checkpoint source"
head -c 65537 "$RESTORE_ERASURE_CHECKPOINT_FILE" > "$TMP/erasure.checkpoint" || die "cannot snapshot checkpoint"
erasure-checkpoint.sh "$TMP/erasure.checkpoint" || die "erasure checkpoint blocked restore"

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
[ "$MAN_CIPHER_SHA" = "$RESTORE_ERASURE_EXPECTED_CIPHER_SHA256" ] || die "backup does not match trusted erasure checkpoint"

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
# This runner never releases a database to the application. The target must
# already be isolated from applications/workers throughout restore. Close new
# connections after integrity, and refuse a terminal result with other sessions.
psql -X -d postgres -v "target_db=$PGDATABASE" -v ON_ERROR_STOP=1 >/dev/null 2>&1 <<'SQL' || die "target quarantine failed; keep infrastructure isolated"
SELECT set_config('pult.restore_target', :'target_db', false);
SELECT format('ALTER DATABASE %I ALLOW_CONNECTIONS false', current_setting('pult.restore_target')) \gexec
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname=current_setting('pult.restore_target')) THEN
    RAISE EXCEPTION 'other sessions remain';
  END IF;
END $$;
SQL
# Detect a changed local head/export, but do not claim this proves a durable
# external head has not advanced. Fresh authority is required before ANY release.
cmp -s "$RESTORE_ERASURE_CHECKPOINT_FILE" "$TMP/erasure.checkpoint" || die "checkpoint changed during restore; target remains quarantined"
erasure-checkpoint.sh "$TMP/erasure.checkpoint" || die "final checkpoint validation failed"
log "QUARANTINED restored key=$KEY (not application release)"
