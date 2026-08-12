#!/bin/sh
# SECURITY-2D-3E1B-3B1 — SYNTHETIC PITR restore wrapper (fail-closed). Restores a pgBackRest
# repository into a NEW EMPTY PGDATA to a target LSN, writing recovery config for promote.
# B1 = synthetic only: an explicit synthetic marker is REQUIRED; never a production cutover;
# never restore over an existing DB; never delete source/repository here. PostgreSQL itself is
# started by the caller AFTER this script to replay WAL + promote.
set -eu
: "${PITR_SYNTHETIC_MARKER:?refusing: PITR_SYNTHETIC_MARKER required (B1 synthetic only)}"
[ "$PITR_SYNTHETIC_MARKER" = "synthetic-3b1" ] || { echo "restore FAIL: not a synthetic marker" >&2; exit 1; }
: "${PGDATA:?}"; : "${PITR_STANZA:=pult}"; : "${PITR_TARGET_LSN:?target LSN required}"

fail() { echo "restore FAIL: $1" >&2; exit 1; }

# Target PGDATA must be new & empty (no restore over an existing cluster).
if [ -f "$PGDATA/PG_VERSION" ]; then fail "target PGDATA is not empty (has PG_VERSION) — refusing"; fi
mkdir -p "$PGDATA"; chmod 0700 "$PGDATA"

# Repository / stanza presence before any restore. NOTE: `pgbackrest check` needs a RUNNING
# primary (it validates archive round-trip via a DB connection) — inappropriate on an empty
# restore target. `info` confirms the repo + stanza + that a base backup exists, with no DB.
pgbackrest --stanza="$PITR_STANZA" info || fail "repository/stanza info failed (repo unreachable)"
pgbackrest --stanza="$PITR_STANZA" info | grep -q "full backup" || fail "no base backup in repository for stanza"

# Restore to the target LSN and configure recovery to promote at target.
pgbackrest --stanza="$PITR_STANZA" \
  --type=lsn --target="$PITR_TARGET_LSN" --target-action=promote \
  --delta=n restore || fail "pgbackrest restore failed"

[ -f "$PGDATA/PG_VERSION" ] || fail "restore produced no PGDATA"
echo "restore OK: stanza=$PITR_STANZA target_lsn=$PITR_TARGET_LSN (recovery configured; caller starts PostgreSQL to promote)"
