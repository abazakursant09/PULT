#!/bin/sh
# SECURITY-2D-3E1B-3A — integrity verification of a RESTORED target database. Runs against
# the target via PG* env. Expected values (captured from the SOURCE before backup) arrive as
# env: EXPECTED_ALEMBIC_HEAD, EXPECTED_SCHEMA_SHA, EXPECTED_USERS_COUNT, EXPECTED_USERS_CKSUM.
# Fail-closed: any mismatch exits non-zero.
set -eu
: "${PGHOST:?}"; : "${PGDATABASE:?}"; : "${PGUSER:?}"
if [ -n "${PGPASSWORD_FILE:-}" ]; then PGPASSWORD="$(cat "$PGPASSWORD_FILE")"; export PGPASSWORD; fi
EXPECTED_ALEMBIC_HEAD="${EXPECTED_ALEMBIC_HEAD:-acl1a2b3c4d01}"
: "${EXPECTED_SCHEMA_SHA:?}"; : "${EXPECTED_USERS_COUNT:?}"; : "${EXPECTED_USERS_CKSUM:?}"
q() { psql -tAc "$1"; }
fail() { echo "integrity FAIL: $1" >&2; exit 1; }

# 1. Alembic head
HEAD="$(q 'SELECT version_num FROM alembic_version')"
[ "$HEAD" = "$EXPECTED_ALEMBIC_HEAD" ] || fail "alembic head $HEAD != $EXPECTED_ALEMBIC_HEAD"

# 2. Full schema set preserved (every table incl. all critical tables). Compare a sha256 of
#    the sorted public table-name set against the source.
TABLES="$(q "SELECT string_agg(table_name, ',' ORDER BY table_name) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")"
GOT_SCHEMA_SHA="$(printf '%s' "$TABLES" | sha256sum | cut -d' ' -f1)"
[ "$GOT_SCHEMA_SHA" = "$EXPECTED_SCHEMA_SHA" ] || fail "schema set sha $GOT_SCHEMA_SHA != $EXPECTED_SCHEMA_SHA"

# 3. Explicit presence of the named critical tables (defence-in-depth over the set sha).
for t in users workspaces execution_logs execution_recovery_audit auth_rate_limit_buckets import_records decisions; do
  EX="$(q "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='$t'")"
  [ "$EX" = "1" ] || fail "critical table missing after restore: $t"
done

# 4. users data round-trip: count + deterministic row-level checksum.
CNT="$(q 'SELECT count(*) FROM users')"
[ "$CNT" = "$EXPECTED_USERS_COUNT" ] || fail "users count $CNT != $EXPECTED_USERS_COUNT"
CKSUM="$(q "SELECT md5(coalesce(string_agg(id||'|'||email||'|'||name||'|'||hashed_password, ',' ORDER BY id),'')) FROM users")"
[ "$CKSUM" = "$EXPECTED_USERS_CKSUM" ] || fail "users checksum mismatch"

# 5. Prove the email-only UNIQUE index rejected the duplicate, not an unrelated
# SQL/connection/permission error. A successful DO block is the positive proof.
psql -X -v ON_ERROR_STOP=1 -q <<'SQL' || fail "UNIQUE(email) proof failed after restore"
DO $email_unique$
DECLARE
  email_indexes text[];
  failure_schema text;
  failure_table text;
  failure_constraint text;
BEGIN
  -- Stabilize the fixture/catalog checks and the probe against other writers/DDL.
  LOCK TABLE public.users IN SHARE ROW EXCLUSIVE MODE;
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_class
             WHERE oid = 'public.users'::regclass AND (relkind <> 'r' OR relrowsecurity)) THEN
    RAISE EXCEPTION 'email proof requires an ordinary users table without RLS';
  END IF;
  -- User triggers/rules could manufacture matching diagnostics or redirect INSERT.
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_trigger
             WHERE tgrelid = 'public.users'::regclass AND NOT tgisinternal
               AND tgenabled <> 'D' AND (tgtype & 4) <> 0)
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_rewrite
                WHERE ev_class = 'public.users'::regclass AND ev_type = '3' AND ev_enabled <> 'D') THEN
    RAISE EXCEPTION 'email proof does not accept user INSERT triggers or rules';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.users WHERE email = 'user1@example.invalid') THEN
    RAISE EXCEPTION 'email proof fixture user1@example.invalid is missing';
  END IF;
  IF EXISTS (SELECT 1 FROM public.users WHERE id = 'ffffffff-0000-0000-0000-000000000000') THEN
    RAISE EXCEPTION 'email proof id is already present';
  END IF;
  -- Standalone unique indexes (the application's ix_users_email) and unique
  -- constraints both appear here. PK/referral/composite/partial/expression indexes
  -- cannot stand in for a valid, live, immediate email-only uniqueness guarantee.
  SELECT array_agg(index_class.relname::text) INTO email_indexes
    FROM pg_catalog.pg_index AS idx
    JOIN pg_catalog.pg_class AS index_class ON index_class.oid = idx.indexrelid
    JOIN pg_catalog.pg_attribute AS attr ON attr.attrelid = idx.indrelid
      AND attr.attname = 'email' AND attr.attnum > 0 AND NOT attr.attisdropped
   WHERE idx.indrelid = 'public.users'::regclass
     AND idx.indisunique AND idx.indisvalid AND idx.indisready AND idx.indislive
     AND idx.indimmediate AND idx.indnkeyatts = 1 AND idx.indkey[0] = attr.attnum
     AND idx.indexprs IS NULL AND idx.indpred IS NULL;
  IF email_indexes IS NULL THEN
    RAISE EXCEPTION 'usable email-only UNIQUE index is missing after restore';
  END IF;
  BEGIN
    INSERT INTO public.users (id,email,name,hashed_password)
      VALUES ('ffffffff-0000-0000-0000-000000000000','user1@example.invalid','dup','x');
    -- Also catches a zero-row/suppressed insert. Raising rolls back any probe row.
    RAISE EXCEPTION 'UNIQUE(email) not enforced after restore (duplicate accepted)';
  EXCEPTION WHEN unique_violation THEN
    GET STACKED DIAGNOSTICS failure_schema = SCHEMA_NAME,
      failure_table = TABLE_NAME, failure_constraint = CONSTRAINT_NAME;
    IF failure_schema IS DISTINCT FROM 'public' OR failure_table IS DISTINCT FROM 'users'
       OR failure_constraint IS NULL OR NOT (failure_constraint = ANY(email_indexes)) THEN
      RAISE;
    END IF;
  END;
END
$email_unique$;
SQL

# 6. Fresh synthetic user insert succeeds — sequences/defaults/constraints usable post-restore.
psql -v ON_ERROR_STOP=1 -q -c "INSERT INTO users (id,email,name,hashed_password) VALUES ('aaaaaaaa-0000-0000-0000-000000000000','fresh@example.invalid','Fresh','x')" \
  || fail "fresh synthetic insert failed after restore"

echo "integrity OK: head=$HEAD tables_sha=$GOT_SCHEMA_SHA users=$CNT checksum-match unique-enforced fresh-insert-ok"
