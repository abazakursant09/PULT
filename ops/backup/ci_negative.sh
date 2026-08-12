#!/bin/sh
# SECURITY-2D-3E1B-3A — runtime negative matrix (synthetic CI only). Given a GOOD uploaded
# backup (GOOD_KEY) + the age identity, this drives restore.sh under each failure condition
# and asserts it exits NON-ZERO (fail-closed) and leaves no restored data. Structural
# invariants (TLS-not-disabled, no plaintext-upload path, public-bucket forbidden, no Docker
# socket / PGDATA mount, non-root, no scheduling, no PITR claim) are covered by the OFFLINE
# guard test, not here.
set -eu
: "${GOOD_KEY:?GOOD_KEY required}"; : "${BACKUP_RESTORE_IDENTITY_FILE:?}"
: "${RESTORE_S3_ENDPOINT:?}"; : "${RESTORE_S3_BUCKET:?}"; : "${RESTORE_S3_ACCESS_KEY_ID:?}"; : "${RESTORE_S3_SECRET_ACCESS_KEY:?}"
: "${BACKUP_S3_BUCKET:?}"; : "${BACKUP_S3_ACCESS_KEY_ID:?}"; : "${BACKUP_S3_SECRET_ACCESS_KEY:?}"; : "${BACKUP_S3_ENDPOINT:?}"
: "${NEG_PREFIX:?NEG_PREFIX required}"
: "${EXPECTED_SCHEMA_SHA:?}"; : "${EXPECTED_USERS_COUNT:?}"; : "${EXPECTED_USERS_CKSUM:?}"
export EXPECTED_SCHEMA_SHA EXPECTED_USERS_COUNT EXPECTED_USERS_CKSUM

# write remote (wr) to stage mutated objects; read remote (rd) same as restore.
export RCLONE_CONFIG_WR_TYPE=s3 RCLONE_CONFIG_WR_PROVIDER="${BACKUP_S3_PROVIDER:-Other}" \
  RCLONE_CONFIG_WR_ACCESS_KEY_ID="$BACKUP_S3_ACCESS_KEY_ID" RCLONE_CONFIG_WR_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET_ACCESS_KEY" \
  RCLONE_CONFIG_WR_ENDPOINT="$BACKUP_S3_ENDPOINT" RCLONE_CONFIG_WR_REGION="${BACKUP_S3_REGION:-}" RCLONE_CONFIG_WR_FORCE_PATH_STYLE=true
export RCLONE_CONFIG_RD_TYPE=s3 RCLONE_CONFIG_RD_PROVIDER="${RESTORE_S3_PROVIDER:-Other}" \
  RCLONE_CONFIG_RD_ACCESS_KEY_ID="$RESTORE_S3_ACCESS_KEY_ID" RCLONE_CONFIG_RD_SECRET_ACCESS_KEY="$RESTORE_S3_SECRET_ACCESS_KEY" \
  RCLONE_CONFIG_RD_ENDPOINT="$RESTORE_S3_ENDPOINT" RCLONE_CONFIG_RD_REGION="${RESTORE_S3_REGION:-}" RCLONE_CONFIG_RD_FORCE_PATH_STYLE=true

W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT INT TERM
PASS=0; FAILED=0
# Fresh empty target DB per scenario (isolated). Uses the target PG* env.
mkdb() { psql -v ON_ERROR_STOP=1 -q -d postgres -c "CREATE DATABASE $1" >/dev/null; }
dropdb_() { psql -q -d postgres -c "DROP DATABASE IF EXISTS $1" >/dev/null 2>&1 || true; }

# expect restore.sh to FAIL for the given key/identity/target-db
expect_restore_fail() {
  desc="$1"; key="$2"; ident="$3"; db="$4"; prepnonempty="${5:-}"
  mkdb "$db"
  if [ "$prepnonempty" = "nonempty" ]; then
    psql -v ON_ERROR_STOP=1 -q -d "$db" -c "CREATE TABLE occupied (x int)" >/dev/null
  fi
  if RESTORE_OBJECT_KEY="$key" BACKUP_RESTORE_IDENTITY_FILE="$ident" PGDATABASE="$db" \
     RESTORE_S3_ENDPOINT="$RESTORE_S3_ENDPOINT" RESTORE_S3_BUCKET="$RESTORE_S3_BUCKET" \
     RESTORE_S3_ACCESS_KEY_ID="$RESTORE_S3_ACCESS_KEY_ID" RESTORE_S3_SECRET_ACCESS_KEY="$RESTORE_S3_SECRET_ACCESS_KEY" \
     restore.sh >/dev/null 2>&1; then
    echo "NEG WRONG: '$desc' unexpectedly SUCCEEDED"; FAILED=$((FAILED+1))
  else
    # must not have left a populated users table
    LEFT="$(psql -tAc "SELECT count(*) FROM users" -d "$db" 2>/dev/null | tr -d ' ' || echo 0)"
    if [ "${LEFT:-0}" != "0" ]; then echo "NEG WRONG: '$desc' failed but left $LEFT users"; FAILED=$((FAILED+1));
    else echo "NEG ok: $desc"; PASS=$((PASS+1)); fi
  fi
  dropdb_ "$db"
}

# stage a GOOD copy locally
rclone copyto "rd:${RESTORE_S3_BUCKET}/${GOOD_KEY}" "$W/good.age"
rclone copyto "rd:${RESTORE_S3_BUCKET}/${GOOD_KEY}.manifest.json" "$W/good.manifest.json"

put() { rclone copyto "$1" "wr:${BACKUP_S3_BUCKET}/${NEG_PREFIX%/}/$2"; }
kk() { echo "${NEG_PREFIX%/}/$1"; }

# 1 missing object
expect_restore_fail "missing object" "$(kk missing.dump.age)" "$BACKUP_RESTORE_IDENTITY_FILE" neg1
# 2 zero-size object (+ copy good manifest)
: > "$W/zero"; put "$W/zero" "zero.dump.age"; put "$W/good.manifest.json" "zero.dump.age.manifest.json"
expect_restore_fail "zero-size object" "$(kk zero.dump.age)" "$BACKUP_RESTORE_IDENTITY_FILE" neg2
# 3 corrupt ciphertext (flip a byte) + good manifest
cp "$W/good.age" "$W/corrupt.age"; printf 'X' | dd of="$W/corrupt.age" bs=1 seek=8 count=1 conv=notrunc 2>/dev/null
put "$W/corrupt.age" "corrupt.dump.age"; put "$W/good.manifest.json" "corrupt.dump.age.manifest.json"
expect_restore_fail "corrupt ciphertext" "$(kk corrupt.dump.age)" "$BACKUP_RESTORE_IDENTITY_FILE" neg3
# 4 tampered manifest ciphertext_sha256
sed 's/\("ciphertext_sha256"[: ]*"\)[0-9a-f]*/\1deadbeef/' "$W/good.manifest.json" > "$W/m4.json"
put "$W/good.age" "t4.dump.age"; put "$W/m4.json" "t4.dump.age.manifest.json"
expect_restore_fail "tampered manifest ciphertext_sha256" "$(kk t4.dump.age)" "$BACKUP_RESTORE_IDENTITY_FILE" neg4
# 5 tampered manifest plaintext_sha256
sed 's/\("plaintext_sha256"[: ]*"\)[0-9a-f]*/\1deadbeef/' "$W/good.manifest.json" > "$W/m5.json"
put "$W/good.age" "t5.dump.age"; put "$W/m5.json" "t5.dump.age.manifest.json"
expect_restore_fail "tampered manifest plaintext_sha256" "$(kk t5.dump.age)" "$BACKUP_RESTORE_IDENTITY_FILE" neg5
# 6 wrong private identity
age-keygen -o "$W/wrong.identity" >/dev/null 2>&1
put "$W/good.age" "t6.dump.age"; put "$W/good.manifest.json" "t6.dump.age.manifest.json"
expect_restore_fail "wrong private identity" "$(kk t6.dump.age)" "$W/wrong.identity" neg6
# 7 missing manifest (cipher only)
put "$W/good.age" "t7.dump.age"
expect_restore_fail "missing manifest" "$(kk t7.dump.age)" "$BACKUP_RESTORE_IDENTITY_FILE" neg7
# 8 malformed manifest
printf 'not-json' > "$W/m8.json"; put "$W/good.age" "t8.dump.age"; put "$W/m8.json" "t8.dump.age.manifest.json"
expect_restore_fail "malformed manifest" "$(kk t8.dump.age)" "$BACKUP_RESTORE_IDENTITY_FILE" neg8
# 9 non-empty target
put "$W/good.age" "t9.dump.age"; put "$W/good.manifest.json" "t9.dump.age.manifest.json"
expect_restore_fail "non-empty target" "$(kk t9.dump.age)" "$BACKUP_RESTORE_IDENTITY_FILE" neg9 nonempty
# 10 manifest ciphertext_size mismatch (HeadObject verify)
sed 's/\("ciphertext_size"[: ]*\)[0-9][0-9]*/\199999999/' "$W/good.manifest.json" > "$W/m10.json"
put "$W/good.age" "t10.dump.age"; put "$W/m10.json" "t10.dump.age.manifest.json"
expect_restore_fail "manifest ciphertext_size mismatch" "$(kk t10.dump.age)" "$BACKUP_RESTORE_IDENTITY_FILE" neg10

echo "negative-matrix: pass=$PASS wrong=$FAILED"
[ "$FAILED" = "0" ] || exit 1
