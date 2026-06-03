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
- The current full schema is captured by the baseline migration
  `47beea1df0c1_baseline_full_schema` (36 tables).
- `Base.metadata.create_all()` is **no longer** a production mechanism. It runs
  only in development/staging for local bootstrap convenience.

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
```
`init_db()` performs no schema creation when `APP_ENV=production`
(`backend/database.py`). Development bootstrap remains `create_all` + legacy
in-place column patches for zero friction.

---

## Automated drift audit (architectural protection)

The invariant

> **SQLAlchemy model schema == Alembic head schema**

is enforced by `backend/tests/test_schema_drift.py`. It builds a fresh database
from the Alembic head and asserts `alembic check` reports no pending operations.
The test **fails** if the models and the migration history have diverged.

This audit must remain part of architecture verification (CI / pre-merge). It is
the enforcement arm of the constitutional requirement above.
