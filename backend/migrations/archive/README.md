# Archived migrations

`001_initial.sql.obsolete` — the original hand-written SQL schema. It covered
only 3 of the current 36 tables and never represented the live schema. It is
kept here for historical reference only.

**Do not run it.** Schema lifecycle is now owned by Alembic. The full current
schema is the Alembic baseline `47beea1df0c1_baseline_full_schema`.

See `docs/governance/schema_governance.md`.
