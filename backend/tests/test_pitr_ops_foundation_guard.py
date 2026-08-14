"""SECURITY-2D-3E1B-3C1 — offline guard for the DORMANT PITR/backup operations foundation.

Checks structural sections + exact mandatory constructs in docs/pitr-operations-policy.md, forbids the
unsafe negatives (activation-by-default, secrets in examples, scheduler/monitoring added, prod compose
wiring PITR), and scans the comment-only env examples for real credentials. No Docker, no network.
"""

from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
POLICY = REPO / "docs" / "pitr-operations-policy.md"
PROD_COMPOSE = REPO / "docker-compose.yml"
PGBR_EX = REPO / "ops" / "pitr" / "pgbackrest.conf.example"
BK_ENV = REPO / "ops" / "backup" / ".env.example"
RS_ENV = REPO / "ops" / "backup" / "restore.env.example"
EXAMPLES = (PGBR_EX, BK_ENV, RS_ENV)


def _r(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_policy_exists():
    assert POLICY.is_file(), "docs/pitr-operations-policy.md must exist"


def test_stage_boundaries_separate():
    p = _r(POLICY)
    for st in ("3C1", "3C2", "3C3", "3C4", "3C5"):
        assert st in p, f"policy must name stage {st}"
    assert "отдельный PR и отдельное одобрение Inal" in p, "each next stage = separate PR + Inal approval"
    assert "production PITR is NOT activated" in p
    assert "scheduler / dead-man / monitoring отсутствуют" in p, "must state scheduler/monitoring absent"


def test_three_iam_roles():
    p = _r(POLICY)
    assert "Role A — Backup/PITR writer" in p
    assert "Role B — Restore reader" in p
    assert "Role C — Retention / Object-Lock administrator" in p
    # writer no delete/admin; GET closure provisional
    assert "БЕЗ `DeleteObject`" in p and "bucket-policy administration" in p, "writer must not delete/admin"
    assert "PROVISIONAL" in p and ("AccessDenied" in p), "writer GET closure must be provisional until canary"
    # reader no put/delete
    assert "БЕЗ `PutObject` / `DeleteObject`" in p, "restore reader must not Put/Delete"
    # retention admin separate; writer does not expire
    assert "writer expire НЕ выполняет" in p or "writer НЕ выполняет expire" in p, "writer must not run expire"
    assert "BypassGovernanceRetention" in p


def test_object_lock_retention():
    p = _r(POLICY)
    assert "Object Lock включается при создании" in p and "versioning обязательно" in p
    assert re.search(r"Governance[^\n]*требует решени", p), "Governance must require Inal decision"
    assert "Compliance mode: do NOT enable" in p
    assert "WAL retention >= oldest retained full backup age" in p, "retention consistency invariant required"
    assert "одного владельца" in p, "single retention owner"


def test_capacity_formula_and_synthetic_disclaimer():
    p = _r(POLICY)
    assert "reserve_bytes = measured_peak_wal_bytes_per_second" in p, "capacity formula required"
    assert "128 MB НЕ использовать как production capacity" in p
    assert "drain rate НЕ использовать как production SLO" in p
    assert "VDS loss during local backlog can lose not-yet-offsite WAL" in p
    assert "запрещено `docker compose down -v`" in p


def test_activation_state_machine():
    p = _r(POLICY)
    for ph in ("Phase 0", "Phase 1", "Phase 2", "Phase 3", "Phase 4"):
        assert ph in p, f"activation state machine must document {ph}"
    assert "No single feature flag" in p, "no one-flag activation"
    assert p.count("Approval: Inal") >= 5, "each of the 5 activation phases needs Inal approval"
    assert p.count("Rollback:") >= 5 and p.count("STOP:") >= 5, "each of the 5 phases needs rollback + STOP"
    assert "exact segment offsite" in p and "continuity=intact" in p, "first-WAL gate"


def test_rpo_rto_honesty():
    p = _r(POLICY)
    assert "RPO=0 during an S3 outage is NOT promised" in p
    assert "RPO/RTO target ≠ measured" in p or "target ≠ measured" in p.replace("RPO/RTO ", "")
    for marker in ("proposed target", "synthetically proven", "production measured", "contractually promised"):
        assert marker in p, f"RPO/RTO must distinguish: {marker}"
    assert "MinIO ≠ Selectel" in p


def test_secret_inventory_and_prohibition():
    p = _r(POLICY)
    assert "Secret categories" in p
    for store in ("Git", "Docker image", "Compose YAML", "CI logs/artifacts", "MEMORY.md",
                  "shell history", "application environment", "Sentry"):
        assert store in p, f"secret prohibition must cover: {store}"
    # categories present
    for cat in ("cipher passphrase", "age private identity", "retention admin credentials"):
        assert cat in p, f"secret category missing: {cat}"


def test_restore_new_pgdata_and_legal_and_gate():
    p = _r(POLICY)
    assert "new empty PGDATA" in p and "никогда поверх production" in p, "restore only into new empty PGDATA"
    assert "RTO is NOT proven until the first real drill" in p
    assert "NOT a legal opinion" in p, "legal section must not be a legal conclusion"
    assert "DEFAULT: NOT READY" in p and "Текущий статус: **NOT READY.**" in p, "binary launch gate defaults NOT READY"


def test_prod_compose_does_not_wire_pitr():
    c = _r(PROD_COMPOSE).lower()
    for tok in ("archive_mode", "archive_command", "pgbackrest"):
        assert tok not in c, f"3C1 must NOT wire {tok} into production docker-compose.yml"


def test_no_scheduler_or_monitoring_files_added():
    # 3C1 must not introduce systemd/cron/monitoring artifacts anywhere in the repo scope it touches.
    for base in (REPO / "ops", REPO / ".github", REPO / "docs"):
        if not base.exists():
            continue
        for pat in ("*.service", "*.timer", "*.cron", "crontab*", "*.alertmanager.yml"):
            hits = list(base.rglob(pat))
            assert not hits, f"3C1 must not add scheduler/monitoring files: {hits}"


def test_examples_have_no_real_secrets():
    cred_key = re.compile(r"^[ \t]*[A-Z0-9_]*(ACCESS_KEY_ID|SECRET_ACCESS_KEY|S3_KEY|S3_SECRET|CIPHER_PASS)[ \t]*=[ \t]*(\S+)",
                          re.M)
    akia = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
    userinfo = re.compile(r"https?://[^/\s]*:[^/@\s]+@")
    placeholder_ok = re.compile(r"(\.invalid|REPLACE|<[^>]+>|/run/secrets/|^age1\.\.\.$)", re.I)
    for f in EXAMPLES:
        t = _r(f)
        assert not akia.search(t), f"{f.name}: looks-like a real access key (AKIA...)"
        assert not userinfo.search(t), f"{f.name}: URL must not embed credentials (user:pass@)"
        for m in cred_key.finditer(t):
            val = m.group(2)
            # a nonempty credential value is only allowed if it's an explicit placeholder or a secret-file path
            assert placeholder_ok.search(val), f"{f.name}: nonempty credential value {m.group(1)}={val!r} (only placeholder allowed)"


def test_examples_no_concrete_bucket_name():
    # bucket vars must stay empty or an explicit placeholder (no real production bucket name)
    bkt = re.compile(r"^[ \t]*[A-Z0-9_]*S3_BUCKET[ \t]*=[ \t]*(\S+)", re.M)
    placeholder_ok = re.compile(r"(REPLACE|<[^>]+>|\.invalid)", re.I)
    for f in (BK_ENV, RS_ENV):
        for m in bkt.finditer(_r(f)):
            assert placeholder_ok.search(m.group(1)), f"{f.name}: concrete bucket name {m.group(1)!r} forbidden in 3C1"
    # pgbackrest example keeps the REPLACE placeholder
    assert "pult-pitr-REPLACE" in _r(PGBR_EX), "pgbackrest example bucket must stay a REPLACE placeholder"


def test_examples_comment_only_intent():
    # the 3C1 additions must be comments (the runtime placeholder defaults stay .invalid / empty)
    assert "s3.ru-1.storage.selcloud.ru" in _r(PGBR_EX), "must document the real Selectel endpoint hostnames"
    assert "repo1-s3-endpoint=s3.example-ru-region.selectel.invalid" in _r(PGBR_EX), "runtime placeholder unchanged"
    assert "PROVISIONAL until the 3C2 canary" in _r(PGBR_EX)
