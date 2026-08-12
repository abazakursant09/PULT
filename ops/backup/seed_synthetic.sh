#!/bin/sh
# SECURITY-2D-3E1B-3A — seed SYNTHETIC, non-PII data on the SOURCE database (after
# `alembic upgrade head`). Emails use the reserved `.invalid` TLD; no real personal data.
# Data round-trip is proven on `users` (a fully-known, constrained core table) with a
# deterministic row-level checksum; the WHOLE schema (every table incl. workspaces /
# execution_logs / execution_recovery_audit / auth_rate_limit_buckets / marketplace
# connections+credentials / imports / decisions) is covered by pg_dump and asserted intact
# via a schema-set checksum in integrity.sh — pg_dump backs up all tables regardless.
set -eu
: "${PGHOST:?}"; : "${PGDATABASE:?}"; : "${PGUSER:?}"
if [ -n "${PGPASSWORD_FILE:-}" ]; then PGPASSWORD="$(cat "$PGPASSWORD_FILE")"; export PGPASSWORD; fi
N="${SEED_USER_COUNT:-10}"

psql -v ON_ERROR_STOP=1 -q <<SQL
DO \$\$
DECLARE i int;
BEGIN
  FOR i IN 1..${N} LOOP
    INSERT INTO users (id, email, name, hashed_password)
    VALUES (
      lpad(i::text, 8, '0') || '-0000-0000-0000-000000000000',
      'user' || i || '@example.invalid',
      'Synthetic User ' || i,
      '\$2b\$12\$syntheticNotARealBcryptHashPlaceholderXXXXXXXXXXXXX'
    );
  END LOOP;
END\$\$;
SQL
echo "seed: inserted ${N} synthetic users (@example.invalid)"
