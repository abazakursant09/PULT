"""Private disposable-cluster probe. Never accepts a database URL or a remote host."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

if not __debug__:
    raise RuntimeError("Optimized Python is forbidden for the guarded browser PG harness")

import psycopg2
from psycopg2 import sql

SOURCE = "pult_browser_source"
TARGET = "pult_browser_restore"
PORT = 55432


def root_path():
    raw = Path(os.environ["PULT_E2E_PG_ROOT"])
    root = raw.resolve()
    assert raw.is_absolute() and raw == root, "absolute non-link root required"
    assert root.parent == Path(os.environ["RUNNER_TEMP"]).resolve()
    assert root.name.startswith("pult-pg-e2e.") and len(root.name) > 13
    return root


def owned_root():
    root = root_path()
    marker = json.loads((root / "owned.json").read_text())
    assert marker == {"purpose": "pult-browser-pg-e2e", "port": PORT}
    return root


def connection(database):
    assert database in ("postgres", SOURCE, TARGET)
    root = owned_root()
    conn = psycopg2.connect(host="127.0.0.1", port=PORT, user="pult_e2e",
                            dbname=database, password=(root / "password.txt").read_text().strip(),
                            connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_setting('data_directory'), current_user, current_database()")
            directory, user, name = cur.fetchone()
            assert Path(directory).resolve() == root / "data", "foreign cluster rejected"
            assert user == "pult_e2e" and name == database
        conn.rollback()
    except BaseException:
        conn.close()
        raise
    return conn


def tool(name, *args):
    executable = Path(os.environ["PULT_E2E_PG_BIN"]) / (name + (".exe" if os.name == "nt" else ""))
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("PG")}
    environment.update(PGPASSWORD=(owned_root() / "password.txt").read_text().strip(), PGCONNECT_TIMEOUT="5")
    return subprocess.run([str(executable), "-h", "127.0.0.1", "-p", str(PORT),
                           "-U", "pult_e2e", "--no-password", *args], check=True,
                          capture_output=True, env=environment, timeout=60).stdout


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def canonical_schema(schema):
    """Reuse the previous native-PG proof's narrowly equivalent array deparse.

    Keep owners, ACL, constraints, predicates and literals; never remove DDL.
    """
    item = r"'(?:[^']|'')*'::character varying"
    pattern = re.compile(r'\(ARRAY\[(' + item + r'(?:, ' + item + r')*)\]\)::text\[\]')
    lines = []
    for line in schema.splitlines():
        if (line.lstrip().startswith('CONSTRAINT ') and ' CHECK ' in line
                or line.startswith('CREATE UNIQUE INDEX ') and ' WHERE ' in line):
            def replace(match):
                if line[:match.start()].count("'") % 2:
                    return match.group(0)
                values = re.findall(item, match.group(1))
                return 'ARRAY[' + ', '.join('(' + value + ')::text' for value in values) + ']'
            line = pattern.sub(replace, line)
        lines.append(line)
    return '\n'.join(lines)


def snapshot(database):
    with connection(database) as conn:
        conn.set_session(isolation_level="REPEATABLE READ", readonly=True)
        with conn.cursor() as cur:
            cur.execute("SET LOCAL TIME ZONE 'UTC'")
            cur.execute("""SELECT n.nspname,c.relname,c.relkind FROM pg_class c
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                AND c.relkind IN ('r','p','m','S') ORDER BY 1,2""")
            objects = cur.fetchall()
            tables, sequences = {}, {}
            for schema, name, kind in objects:
                identifier = sql.Identifier(schema, name)
                if kind == "S":
                    cur.execute(sql.SQL("SELECT last_value,is_called FROM {}").format(identifier))
                    sequences[f"{schema}.{name}"] = list(cur.fetchone())
                else:
                    cur.execute(sql.SQL("SELECT row_to_json(t)::text FROM {} t").format(identifier))
                    rows = sorted(row[0] for row in cur.fetchall())
                    tables[f"{schema}.{name}"] = {"rows": len(rows), "sha256": digest(rows)}
    ddl = tool("pg_dump", "-d", database, "--schema-only").decode("utf-8")
    # pg_dump 16.15 adds a random psql restriction nonce, not a schema difference.
    ddl = "\n".join(line for line in ddl.splitlines()
                    if not line.startswith(("\\restrict ", "\\unrestrict ")))
    return {"schemaSha256": hashlib.sha256(canonical_schema(ddl).encode()).hexdigest(),
            "tables": tables, "sequences": sequences}


def run(action, arguments):
    if action == "init":
        root = root_path()
        assert not root.exists() or not any(root.iterdir()), "root is not empty"
        root.mkdir(mode=0o700, exist_ok=True)
        with (root / "owned.json").open("x") as stream:
            json.dump({"purpose": "pult-browser-pg-e2e", "port": PORT}, stream)
        password = os.open(root / "password.txt", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(password, "w") as stream:
            stream.write(secrets.token_hex(32) + "\n")
        return {"owned": True}
    root = owned_root()
    if action == "create":
        conn = connection("postgres")
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                for name in (SOURCE, TARGET):
                    cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        finally:
            conn.close()
        return {"created": [SOURCE, TARGET]}
    if action == "query":
        data = json.loads(arguments[0])
        with connection(data.get("database", SOURCE)) as conn:
            conn.set_session(readonly=True)
            with conn.cursor() as cur:
                cur.execute(data["sql"], data.get("params", []))
                columns = [item.name for item in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
    if action == "backup":
        baseline = snapshot(SOURCE)
        assert baseline["tables"], "source is empty"
        dump = root / "journey.dump"
        assert not dump.exists() and not (root / "baseline.json").exists()
        tool("pg_dump", "-d", SOURCE, "--format=custom", "--file", str(dump))
        assert snapshot(SOURCE) == baseline, "source changed during backup"
        with (root / "baseline.json").open("x") as stream:
            json.dump(baseline, stream, sort_keys=True)
        return {"sourceUnchanged": True, "tables": len(baseline["tables"]),
                "sequences": len(baseline["sequences"]), "bytes": dump.stat().st_size}
    if action == "restore":
        baseline = json.loads((root / "baseline.json").read_text())
        assert snapshot(SOURCE) == baseline, "source changed before restore"
        assert not snapshot(TARGET)["tables"], "restore target must be empty"
        tool("pg_restore", "-d", TARGET, "--exit-on-error", str(root / "journey.dump"))
        # Normalize tuple/list representation before comparing the JSON receipt.
        source = json.loads(json.dumps(snapshot(SOURCE)))
        target = json.loads(json.dumps(snapshot(TARGET)))
        assert source == baseline, "restore modified source"
        assert target == baseline, "restored schema/data/sequences mismatch"
        receipt = {"sourceUnchanged": True, "schemaDataSequencesMatch": True,
                   "tables": len(baseline["tables"]), "sequences": len(baseline["sequences"])}
        with (root / "restore-result.json").open("x") as stream:
            json.dump(receipt, stream)
        return receipt
    raise AssertionError("unknown command")


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1], sys.argv[2:]), default=str))
