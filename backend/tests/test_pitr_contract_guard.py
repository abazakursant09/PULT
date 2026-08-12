"""SECURITY-2D-3E1B-3B1 — offline guard for the PITR foundation contract (tracked files only)."""

from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
PITR = REPO / "ops" / "pitr"
DOCKERFILE = PITR / "Dockerfile"
PGCONF = PITR / "postgresql.conf"
PGBRCONF = PITR / "pgbackrest.conf.example"
RESTORE = PITR / "restore.sh"
STATUS = PITR / "status.sh"
COMPOSE_PITR = REPO / "docker-compose.pitr.yml"
COMPOSE_PROD = REPO / "docker-compose.yml"
WORKFLOW = REPO / ".github" / "workflows" / "pitr_synthetic.yml"
POLICY = REPO / "docs" / "pitr-policy.md"

PG_DIGEST = "sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"
PGBR_SRC_SHA = "faaf8faa14a6392279654ee216a493fcd07b0c513af4b55fe34faec062cb8875"
MARKETPLACE = ("WB_", "OZON_", "YANDEX_", "TELEGRAM_BOT_TOKEN", "SECRET_KEY", "CRED_ENC_KEY", "SENTRY_DSN", "JWT")


def _r(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _code(t: str) -> str:
    return "\n".join(ln for ln in t.splitlines() if not ln.lstrip().startswith("#"))


def test_files_exist():
    for p in (DOCKERFILE, PGCONF, PGBRCONF, RESTORE, STATUS, COMPOSE_PITR, WORKFLOW, POLICY):
        assert p.is_file(), f"missing: {p.relative_to(REPO)}"


def test_no_discovery_leftovers():
    assert not (PITR / "Dockerfile.discovery").exists(), "discovery Dockerfile must be removed from the final head"
    assert not (REPO / ".github" / "workflows" / "pitr_discovery.yml").exists(), "discovery workflow must be removed"


def test_dockerfile_pinned_source_and_exact_apk():
    df = _r(DOCKERFILE)
    assert df.count(f"FROM postgres:16-alpine@{PG_DIGEST}") == 2, "both stages must FROM the pinned base"
    assert f"--checksum=sha256:{PGBR_SRC_SHA}" in df, "pgBackRest source must be hash-verified"
    assert "pgbackrest-2.59.0.tar.gz" in df
    # Each `apk add` INSTALL block (incl. backslash continuations): every package pinned
    # name=version-rN, and NO postgresql18 install (PG16 parity).
    for blk in re.findall(r"apk add(?:[^\n]*\\\n)*[^\n]*", _code(df)):
        assert "postgresql18" not in blk, "PG16 parity: must not `apk add` a postgresql18 package"
        for tok in blk.split():
            if tok in ("RUN", "apk", "add", "--no-cache", "\\"):
                continue
            assert re.match(r"^[a-z0-9][a-z0-9._+-]*=[0-9][0-9A-Za-z._]*-r[0-9]+$", tok), f"unpinned apk package: {tok!r}"
    assert "edge" not in df.lower(), "no edge repository"
    assert "curl" not in _code(df) and "| sh" not in _code(df)
    # final stage must reject build tools + unresolved libs (guard lines present)
    assert "not found" in df and "build tools leaked" in df
    # PG16/libpq parity evidence commands present + final image proven free of a PG18 install.
    assert "pg_config --version" in df and "PostgreSQL 16" in df, "must prove PG16 pg_config parity"
    assert "readelf -d" in df and "libpq" in df, "must prove libpq linkage (readelf/ldd)"
    assert "/usr/local/lib/" in df, "libpq must resolve to the base PG16 (/usr/local/lib), not an apk PG18"
    assert "postgresql18-client" in df and "PG18 package in final image" in df, "final stage must assert no PG18 client"


def test_postgresql_conf_valid_and_no_secrets():
    c = _r(PGCONF)
    assert "archive_mode = on" in c
    assert re.search(r"archive_command\s*=\s*'pgbackrest --stanza=pult archive-push %p'", c)
    code = _code(c)
    assert "|| true" not in code, "archive_command must never swallow failure"
    assert "archive-async" not in code, "archive-async is NOT a PostgreSQL GUC (belongs in pgbackrest.conf)"
    # No secret material as real directives (comments explaining the env-var contract are fine).
    for bad in ("s3-key", "s3_key", "cipher-pass", "cipher_pass"):
        assert bad not in code.lower(), f"no secret option in postgresql.conf: {bad}"


def test_pgbackrest_conf_async_here_and_no_secret_values():
    c = _r(PGBRCONF)
    assert "archive-async=y" in c, "archive-async belongs here"
    assert "repo1-storage-verify-tls=y" in c, "TLS verify must be enabled"
    assert "repo1-cipher-type=aes-256-cbc" in c
    # no actual secret VALUES (env var NAMES referenced in comments are fine)
    assert not re.search(r"repo1-s3-key\s*=\s*\S", c), "no S3 key value in config"
    assert not re.search(r"repo1-cipher-pass\s*=\s*\S", c), "no cipher pass value in config"


def test_restore_is_synthetic_gated_and_new_empty():
    r = _r(RESTORE)
    assert "PITR_SYNTHETIC_MARKER" in r and "synthetic-3b1" in r, "restore is synthetic-marker gated in B1"
    assert "PG_VERSION" in r and "not empty" in r, "must refuse a non-empty target"
    assert "--type=lsn" in r, "LSN target"
    assert "--target-action=promote" in r


def test_status_output_allowlisted():
    s = _r(STATUS)
    for bad in ("printenv", "env\n", "SELECT ", "s3-key", "PGBACKREST_REPO1_S3", "cipher"):
        assert bad.lower() not in s.lower(), f"status must not emit {bad!r}"
    assert "pitr_check_status=" in s and "pitr_pg_wal_bytes=" in s


def test_compose_pitr_profile_gated_and_no_bad_mounts():
    import yaml
    d = yaml.safe_load(_r(COMPOSE_PITR))
    svc = d["services"]["pitr-postgres"]
    assert svc.get("profiles") == ["pitr"], "must be profile-gated (not default up)"
    blob = _r(COMPOSE_PITR)
    code = _code(blob)
    assert "docker.sock" not in code, "no Docker socket"
    assert "ports:" not in code, "no published ports"
    # no host PGDATA bind (only the named synthetic volume)
    assert "/var/lib/postgresql/data:" not in code.replace("pitr_pgdata:/var/lib/postgresql/data", "")
    for s in MARKETPLACE:
        assert s not in code, f"PITR runner must not receive {s}"


def test_production_compose_and_3a_unchanged():
    prod = _r(COMPOSE_PROD).lower()
    for tok in ("archive_mode", "archive_command", "pgbackrest", "restore_command"):
        assert tok not in prod, f"3B1 must NOT wire {tok} into the production compose"
    assert (REPO / "ops" / "backup" / "backup.sh").exists(), "3A must remain"


def test_workflow_pinned_readonly_no_artifacts():
    w = _r(WORKFLOW)
    assert "permissions:" in w and "contents: read" in w
    assert "persist-credentials: false" in w
    assert "minio/minio:RELEASE.2025-09-07T16-13-09Z@sha256:14cea493" in w, "MinIO pinned"
    bad = [ln for ln in w.splitlines() if re.search(r"uses:\s", ln) and not re.search(r"@[0-9a-f]{40}", ln)]
    assert not bad, f"unpinned actions: {bad}"
    assert "verify-tls=n" not in w, "TLS verify must not be disabled"
    for m in re.findall(r"path:\s*(.+)", w):
        assert not re.search(r"\.(dump|age|key|crt)|pgdata|repo|wal", m, re.I), f"must not upload sensitive artifact: {m}"


def test_full_b1_negative_matrix_present():
    w = _r(WORKFLOW)
    # every mandatory B1 case must be present by a stable name
    for case in ("A missing base", "B wrong cipher", "C non-empty target", "D missing synthetic",
                 "E S3 outage", "F missing required WAL", "G corrupt required WAL",
                 "H wrong system", "I target before", "J WAL continuity gap", "K major mismatch"):
        assert case in w, f"B1 negative case missing from workflow: {case!r}"
    # S3-outage must prove the real semantics, not a helper network error
    assert "network disconnect pitrnet minio" in w and "failed_count" in w and "network connect pitrnet minio" in w, \
        "E must isolate MinIO, prove archive failed_count + retained WAL, then drain"
    assert "pg_wal" in w and "drain" in w
    # (archive_command must never use `|| true` — enforced against postgresql.conf elsewhere.)
    assert re.search(r'test "\$pass" = "\$n"', w), "must assert pass==n (wrong=0)"


def test_b1_cases_not_deferred_to_b2():
    p = _r(POLICY).lower()
    b2 = p.split("b2")[-1] if "b2" in p else ""
    for forbidden in ("s3 unavailable", "missing required wal", "corrupt", "system id",
                      "target before", "continuity gap", "major mismatch"):
        assert forbidden not in b2, f"mandatory B1 case wrongly listed under B2: {forbidden!r}"


def test_policy_honest_foundation_only():
    p = _r(POLICY).lower()
    for phrase in ("synthetic", "not", "pitr", "production", "3c", "rpo"):
        assert phrase in p, f"policy missing honesty phrase: {phrase}"
    assert "signed alpine" in p or "alpine repo" in p, "policy must state the Alpine-repo residual"
