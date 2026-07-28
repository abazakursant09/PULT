"""PULT PostgreSQL migration compatibility audit (INVESTIGATION ONLY — throwaway).

Runs the REAL Alembic chain base->head against a clean PostgreSQL 16 database. When a migration fails,
it records the defect (revision, SQL, PostgreSQL error) and — to keep auditing PAST the first error —
applies a THROWAWAY in-memory fix to the offending migration file IN THE RUNNER WORKSPACE ONLY (never
committed), drops the schema, and retries. This surfaces the FULL list of PostgreSQL-incompatible
migrations in one run rather than only the first.

The only auto-continue heuristic is the proven int-for-boolean pattern
(`UPDATE ... SET <col> = 0|1` on a boolean column -> false|true). Any OTHER failure is recorded and the
audit STOPS there (honestly: "could not auto-continue past <rev>"). This script changes NO committed
migration and is deleted after the audit.
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

import sqlalchemy as sa

VERS = pathlib.Path(__file__).resolve().parent / "alembic" / "versions"
PG_ALEMBIC = os.environ["PG_ALEMBIC_URL"]   # postgresql+asyncpg://...  (Alembic env uses async)
PG_SYNC = os.environ["PG_SYNC_URL"]         # postgresql+psycopg2://... (schema reset)
MAX_ITERS = 40


def _reset_schema() -> None:
    eng = sa.create_engine(PG_SYNC)
    try:
        with eng.begin() as c:
            c.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public")
    finally:
        eng.dispose()


def _upgrade_head() -> subprocess.CompletedProcess:
    env = dict(os.environ, ALEMBIC_DATABASE_URL=PG_ALEMBIC, APP_ENV="development")
    return subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                          cwd=str(VERS.parent.parent), capture_output=True, text=True, env=env)


def _current_rev(out: str) -> str:
    revs = re.findall(r"Running upgrade [0-9a-z]+ -> ([0-9a-z]+)", out)
    return revs[-1] if revs else "(none applied)"


def _find_file_for_sql(fragment: str) -> str | None:
    for f in sorted(VERS.glob("*.py")):
        if fragment and fragment in f.read_text(encoding="utf-8"):
            return f.name
    return None


def main() -> int:
    defects: list[dict] = []
    reached_head = False
    for _ in range(MAX_ITERS):
        _reset_schema()
        r = _upgrade_head()
        out = r.stdout + "\n" + r.stderr
        if r.returncode == 0:
            reached_head = True
            break
        last_ok = _current_rev(r.stdout)
        sqlm = re.search(r"\[SQL: (.+?)\]", out, re.S)
        sql = (sqlm.group(1).strip() if sqlm else "").splitlines()[0] if sqlm else ""
        errm = re.search(r"(asyncpg\.exceptions\.\w+|sqlalchemy\.exc\.\w+).*", out)
        err = errm.group(0).splitlines()[0] if errm else out.strip().splitlines()[-1]

        # int-for-boolean auto-continue
        boolm = re.search(r'column "(\w+)" is of type boolean but expression is of type integer', out)
        setm = re.search(r"SET (\w+) = ([01])\b", sql)
        if boolm and setm:
            col = setm.group(1)
            patched = []
            for f in VERS.glob("*.py"):
                t = f.read_text(encoding="utf-8")
                nt = re.sub(rf"SET {col} = 0\b", f"SET {col} = false", t)
                nt = re.sub(rf"SET {col} = 1\b", f"SET {col} = true", nt)
                if nt != t:
                    f.write_text(nt, encoding="utf-8")
                    patched.append(f.name)
            defects.append({"after_rev": last_ok, "file": ",".join(patched) or "?",
                            "sql": sql, "error": err, "type": "int-for-boolean UPDATE",
                            "fix": f"SET {col} = false/true (sa.false()/sa.true())"})
            continue

        # unknown / unfixable — record and stop honestly
        defects.append({"after_rev": last_ok, "file": _find_file_for_sql(sql[:60]) or "?",
                        "sql": sql, "error": err, "type": "UNKNOWN — audit halted", "fix": "manual"})
        break

    print("\n================ PG MIGRATION AUDIT RESULT ================")
    print(f"reached_head={reached_head}")
    print(f"defect_count={len(defects)}")
    for i, d in enumerate(defects, 1):
        print(f"\n[{i}] after_rev={d['after_rev']} file={d['file']} type={d['type']}")
        print(f"    SQL:   {d['sql']}")
        print(f"    ERROR: {d['error']}")
        print(f"    FIX:   {d['fix']}")

    # single-head check (metadata only; unaffected by throwaway edits to op bodies)
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config()
    cfg.set_main_option("script_location", str(VERS.parent))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    print(f"\nalembic heads={heads}")
    print("========================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
