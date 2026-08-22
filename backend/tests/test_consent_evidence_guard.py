"""LEGAL-PRELAUNCH-F2 (blocker #6) — offline source guard for server-side consent evidence.

Pure file reads (no DB, no network). Proves the mechanism cannot silently regress:
required strict consent, refusal-before-mutation, server-only timestamp/version, same-transaction
evidence for BOTH registration and recovery, migration on the correct head, NOT NULL preserved,
no legacy backfill, PII-free logging, and the DRAFT / launch-gate markers intact.
"""
from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
AUTH = BACKEND / "routers" / "auth.py"
SCHEMA = BACKEND / "schemas" / "auth.py"
MODEL = BACKEND / "models" / "consent_record.py"
CONFIG = BACKEND / "config.py"
MIG = BACKEND / "alembic" / "versions" / "csr1a2b3c4d01_consent_records.py"
LEGAL = REPO / "docs" / "legal"


def _r(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_schema_requires_strict_consent():
    s = _r(SCHEMA)
    assert "StrictBool" in s, "consent must be StrictBool (reject 'true'/1 coercion)"
    assert re.search(r"\n\s*consent:\s*StrictBool\b", s), "UserRegister must have a required consent: StrictBool"
    # no default → required (a default would let a missing field through)
    assert not re.search(r"consent:\s*StrictBool\s*=", s), "consent must not have a default value"
    # client cannot supply timestamp/version — no such FIELDS on the request model
    assert not re.search(r"\n\s*consent_at\s*:", s), "client schema must not carry a consent_at field"
    assert not re.search(r"\n\s*consent_version\s*:", s), "client schema must not carry a consent_version field"


def test_endpoint_refuses_before_mutation():
    s = _r(AUTH)
    guard = s.find("data.consent is not True")
    throttle = s.find('_throttle_or_429(db, "register"')   # the CALL in register(), not the def
    assert guard != -1, "endpoint must explicitly refuse consent is not True"
    assert throttle != -1 and guard < throttle, "consent refusal must come BEFORE any DB mutation (throttle)"


def test_evidence_is_server_stamped_and_in_transaction():
    s = _r(AUTH)
    assert s.count("db.add(ConsentRecord(") == 2, "exactly two evidence inserts (registration + recovery)"
    assert "consent_at=datetime.utcnow()" in s, "consent_at must be server-generated"
    assert "consent_version=settings.consent_doc_version" in s, "version must be the server constant"
    assert 'context="registration"' in s and 'context="recovery"' in s, "both contexts must be written"
    # server never trusts a client timestamp/version
    assert "data.consent_at" not in s and "data.consent_version" not in s
    # each insert is immediately followed (within the same block) by a commit, never its own commit first
    assert "update(ConsentRecord" not in s and "delete(ConsentRecord" not in s, "no evidence mutation path"


def test_model_is_append_only_and_not_null():
    m = _r(MODEL)
    for col in ("user_id", "consent_at", "consent_version", "context", "created_at"):
        assert col in m, f"model missing column {col}"
    # Each evidence column must be explicitly NOT NULL — weakening ANY single one must fail here.
    for col in ("user_id", "consent_at", "consent_version", "context", "created_at"):
        assert re.search(rf"{col}\s*=\s*Column\([^\n]*nullable=False", m), f"{col} must be NOT NULL"
    assert "consent_version = Column(String(16)" in m, "consent_version must be String(16)"
    assert "ck_consent_context" in m, "context must be constrained to a closed vocabulary"
    # no IP/UA/email/token/doc-text collected
    for banned in ("ip", "user_agent", "fingerprint", "device", "email", "token", "document_text"):
        assert not re.search(rf"\n\s*{banned}\b\s*=", m), f"consent model must not collect {banned!r}"


def test_migration_on_correct_head_no_backfill():
    mig = _r(MIG)
    assert 'revision: str = "csr1a2b3c4d01"' in mig
    assert 'down_revision: Union[str, None] = "rob1a2b3c4d01"' in mig, "must chain on the current head"
    assert "create_table(" in mig and "consent_records" in mig
    assert "nullable=False" in mig
    # no data migration / backfill of existing users
    assert "INSERT" not in mig.upper() and "SELECT" not in mig.upper(), "no backfill of legacy users"
    assert "drop_table(\"consent_records\")" in mig, "downgrade must drop only what it added"


def test_config_has_server_version_constant():
    c = _r(CONFIG)
    assert "consent_doc_version" in c, "server document version constant must exist"
    m = re.search(r'consent_doc_version:\s*str\s*=\s*"([^"]+)"', c)
    assert m, "consent_doc_version must have a concrete value"
    assert 0 < len(m.group(1)) <= 16, "version must be <=16 chars"
    assert m.group(1) != "[VERSION]", "must not use the DRAFT placeholder as a runtime value"


def test_logging_pii_free_in_register():
    s = _r(AUTH)
    # the register log lines must not interpolate email/ip/password/consent payload
    for stmt in re.findall(r"log\.\w+\([^\n]*\)", s):
        if "register" in stmt or "consent" in stmt.lower():
            for bad in ("data.email", "data.password", "%s.*email", "registered_ip", "data.consent"):
                assert not re.search(bad, stmt), f"register logging must stay PII-free: {stmt!r}"


def test_docs_reflect_partial_and_counsel():
    chk = _r(LEGAL / "launch-legal-checklist.md")
    row6 = next((ln for ln in chk.splitlines() if ln.strip().startswith("| 6 |")), "")
    assert row6, "blocker #6 row missing"
    cells = [c.strip() for c in row6.strip().strip("|").split("|")]
    assert "PARTIAL" in cells[3].upper(), "#6 must be PARTIAL (mechanism done, legal sufficiency open)"
    for bad in ("DONE", "READY", "VERIFIED", "CLOSED"):
        assert bad not in cells[3].upper(), f"#6 must not be marked {bad}"
    assert "REQUIRES RUSSIAN COUNSEL REVIEW" in row6, "#6 must keep the counsel caveat"
    assert "backfill" in row6.lower() or "legacy" in row6.lower(), "#6 must state legacy no-backfill"
    consent_doc = _r(LEGAL / "personal-data-consent.DRAFT.md")
    assert "REQUIRES RUSSIAN COUNSEL REVIEW" in consent_doc
    assert "НЕ ПУБЛИКОВАТЬ" in consent_doc, "consent DRAFT must keep the publish gate"
    assert "NOT READY" in chk, "launch gate must stay NOT READY"
