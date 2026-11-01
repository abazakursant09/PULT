# Schema Governance (PULT Constitutional Persistence Layer)

**Status: FROZEN CONTRACT** — established Sprint 69. Do not weaken without an
explicit governance unlock.

The database schema is part of the PULT runtime substrate. Its integrity is a
constitutional requirement, not an implementation detail. Schema drift between
the application's models and the deployed database is treated as a critical
defect (Risk **R1**).

---

## Source of truth

- **SQLAlchemy models** under `backend/models/` define the schema.
- **Alembic** translates those models into versioned, ordered migrations under
  `backend/alembic/versions/`.
- The historical baseline migration
  `47beea1df0c1_baseline_full_schema` captured 36 tables at that revision.
  The current schema is defined by the complete migration chain through head,
  not by that baseline alone.
- `Base.metadata.create_all()` is **not** an application startup mechanism in
  development, staging or production. Test fixtures may use it to construct
  controlled legacy-schema scenarios; those fixtures are not deployment steps.

The legacy `backend/migrations/001_initial.sql` is obsolete and archived under
`backend/migrations/archive/`. It must not be executed.

---

## Constitutional requirement

> **No model change without a migration.**

Any change to a model — new table, new column, type change, index, constraint —
**must** be accompanied by an Alembic migration in the same change set. A model
change without a matching migration is a governance violation and is blocked by
the automated drift audit (see below).

---

## Schema evolution process

1. Edit the model(s) under `backend/models/`. If you add a new model module,
   ensure it is importable from the `models` package directory (the Alembic
   environment auto-imports every `models/*.py` module — see
   `backend/alembic/env.py::_register_all_models`).
2. Generate the migration:
   ```bash
   cd backend
   python -m alembic revision --autogenerate -m "<short_description>"
   ```
3. **Review the generated migration by hand.** Autogenerate is a draft, not an
   authority. Confirm the `upgrade()` and `downgrade()` operations are correct,
   that `downgrade()` is a true inverse, and that no destructive operation is
   unintentional.
4. Apply it locally and confirm zero residual drift:
   ```bash
   python -m alembic upgrade head
   python -m alembic check          # must print: No new upgrade operations detected.
   ```
5. Commit the model change and the migration **together**.

## Migration generation reference

- Generate (autogenerate draft): `python -m alembic revision --autogenerate -m "msg"`
- Generate (empty, hand-written):  `python -m alembic revision -m "msg"`
- Apply latest:                     `python -m alembic upgrade head`
- Show current DB revision:         `python -m alembic current`
- Show history:                     `python -m alembic history`

The DB URL is resolved by `backend/alembic/env.py` from
`config.settings.database_url`, or overridden by the `ALEMBIC_DATABASE_URL`
environment variable (used for throwaway baseline/drift runs). Both
`sqlite+aiosqlite` and `postgresql+asyncpg` drivers are supported.

## Rollback procedure

- Roll back the last migration:   `python -m alembic downgrade -1`
- Roll back to a specific rev:    `python -m alembic downgrade <revision_id>`
- Roll back everything:           `python -m alembic downgrade base`

Every migration must provide a working `downgrade()`. A round-trip
(`downgrade base` → `upgrade head`) must reproduce the schema with zero drift.
Verify before merging.

> **Production rollback note:** roll back application code and schema together.
> Never run `downgrade` against production without a verified backup and a
> rehearsed plan — a `downgrade` that drops a column destroys data.

## Deployment

Production deploys **must** run, before starting the API process:
```bash
cd backend
python -m alembic upgrade head
python -m alembic check
```
Verify that the inspected database is the intended application database; an
`ALEMBIC_DATABASE_URL` override must not accidentally target a different database
from `DATABASE_URL`. Do not print credential-bearing URLs in logs or reports.

`init_db()` delegates to `run_migrations()` in all environments
(`backend/database.py`, `backend/db_migrations.py`). `APP_ENV=production` alone
does **not** disable migrations. `AUTO_MIGRATE` defaults to true:

- An empty database or a versioned database behind head is upgraded with Alembic.
- An unversioned database with application tables is stamped only after a
  read-only metadata comparison accepts its schema.
- A database already marked at head is compared against model metadata before
  being accepted; detected drift refuses startup without automatic repair.
- If initial inspection fails, the existing fallback attempts `upgrade head`
  directly; this is not proof that a metadata comparison succeeded.

For multi-worker production, apply and verify migrations once during the
controlled deployment procedure, then set `AUTO_MIGRATE=0` for API workers to
avoid concurrent upgrades. With this flag disabled, startup skips both migration
and this runner's schema validation; the deployment verification is therefore
required. This document does not authorize a deployment or change any settings.

On a drift refusal, stop and investigate against a verified backup and the
migration history. Do not bypass it with a manual stamp, `create_all`, ad-hoc
ALTER statements, or table deletion. Reconciliation requires a reviewed plan.

---

## Automated drift audit (architectural protection)

The invariant

> **SQLAlchemy model schema == Alembic head schema**

is enforced by `backend/tests/test_schema_drift.py`. It builds a fresh database
from the Alembic head and asserts `alembic check` reports no pending operations.
The test **fails** if the models and the migration history have diverged.

That fresh-database audit is not an inspection of an existing deployed database.
Startup regression coverage additionally lives in
`test_legacy_schema_stamp_guard.py`, `test_stamped_schema_drift_guard.py` and
`test_startup_migration_pg.py`. Metadata comparison does not establish complete
equivalence of every server default, CHECK constraint or dialect-specific object.

This audit must remain part of architecture verification (CI / pre-merge). It is
the enforcement arm of the constitutional requirement above.
