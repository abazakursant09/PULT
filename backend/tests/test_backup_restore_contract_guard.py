"""SECURITY-2D-3E1B-3A — offline guard for the backup/restore contract.

Asserts the security/supply-chain invariants of the backup foundation from tracked files only
(no network, no Docker). Structural checks where practical; targeted text checks otherwise.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
OPS = REPO / "ops" / "backup"

APP_COMPOSE = REPO / "docker-compose.yml"
BACKUP_COMPOSE = REPO / "docker-compose.backup.yml"
DOCKERFILE = OPS / "Dockerfile"
BACKUP_SH = OPS / "backup.sh"
RESTORE_SH = OPS / "restore.sh"
BACKUP_ENV = OPS / ".env.example"
RESTORE_ENV = OPS / "restore.env.example"
BACKEND_ENV = BACKEND / ".env.example"
WORKFLOW = REPO / ".github" / "workflows" / "backup_restore_synthetic.yml"
POLICY = REPO / "docs" / "backup-restore-policy.md"
GITIGNORE = REPO / ".gitignore"
DOCKERIGNORE = BACKEND / ".dockerignore"

PG_DIGEST = "sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"
RCLONE_SHA = "aa2804e08f48250e71009c727124b6341cd0288465804a9a09d14663cabafbaa"
AGE_SHA = "bdc69c09cbdd6cf8b1f333d372a1f58247b3a33146406333e30c0f26e8f51377"
MINIO_REF = "minio/minio:RELEASE.2025-09-07T16-13-09Z@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e"

MARKETPLACE_SECRETS = ("WB_", "OZON_", "YANDEX_", "TELEGRAM_BOT_TOKEN", "SECRET_KEY",
                       "CRED_ENC_KEY", "SENTRY_DSN", "JWT")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _code_only(text: str) -> str:
    """Drop whole-line `#` comments so guard checks inspect real directives, not prose."""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def test_all_backup_files_exist():
    for p in (BACKUP_COMPOSE, DOCKERFILE, BACKUP_SH, RESTORE_SH, BACKUP_ENV, RESTORE_ENV,
              WORKFLOW, POLICY):
        assert p.is_file(), f"missing backup foundation file: {p.relative_to(REPO)}"


def test_runner_uses_pinned_base_and_pinned_verified_tools():
    df = _read(DOCKERFILE)
    assert f"FROM postgres:16-alpine@{PG_DIGEST}" in df, "runner base must be the pinned PostgreSQL image"
    # rclone + age added via BuildKit ADD --checksum with the exact verified sha256
    assert f"--checksum=sha256:{RCLONE_SHA}" in df, "rclone must be pinned+hash-verified"
    assert f"--checksum=sha256:{AGE_SHA}" in df, "age must be pinned+hash-verified"
    assert "v1.75.0" in df and "v1.3.1" in df
    # no unpinned/floating installs or runtime downloads (check real directives, not comments)
    code = _code_only(df)
    assert "apk add" not in code, "no unpinned apk add"
    assert "curl" not in code and "| sh" not in code, "no curl|sh"
    assert re.search(r"USER\s+10002", code), "runner must run as a non-root uid"


def test_backup_uses_custom_format_and_no_plaintext_upload():
    b = _read(BACKUP_SH)
    assert "--format=custom" in b, "pg_dump must use custom format"
    assert "age -r" in b, "encryption (age recipient) is mandatory"
    # only the ciphertext + manifest are uploaded — never the plaintext dump
    uploads = re.findall(r"rclone copyto\s+(\S+)", b)
    assert uploads, "expected rclone uploads"
    for src in uploads:
        assert "$PLAIN" not in src, f"plaintext dump must never be uploaded: {src}"
    assert '"$CIPHER"' in b or "$CIPHER" in " ".join(uploads), "ciphertext must be uploaded"


def test_no_insecure_tls_and_no_public_acl():
    for p in (BACKUP_SH, RESTORE_SH, OPS / "ci_negative.sh", BACKUP_ENV, RESTORE_ENV, BACKUP_COMPOSE):
        low = _code_only(_read(p)).lower()
        assert "--no-check-certificate" not in low, f"{p.name}: TLS verify must not be disabled"
        assert "insecure" not in low, f"{p.name}: no insecure TLS"
        assert "no_check_certificate" not in low
        assert "public-read" not in low and "acl=public" not in low, f"{p.name}: public ACL forbidden"
    # example production endpoint is https
    assert re.search(r"BACKUP_S3_ENDPOINT=https://", _read(BACKUP_ENV))


def test_writer_needs_no_delete_or_content_get():
    b = _read(BACKUP_SH)
    assert "rclone delete" not in b and "deletefile" not in b, "writer must not DeleteObject"
    # writer uploads (copyto local->remote) + stat-verifies; it never downloads content
    assert "rclone cat" not in b and "rclone copyto \"bk:" not in b, "writer must not GetObject content"


def test_backup_runner_has_no_marketplace_or_restore_secrets():
    # backup writer env contract + compose backup service env must not carry provider/JWT/Sentry
    # (inspect real directives, not explanatory comments)
    for p in (BACKUP_ENV, BACKUP_COMPOSE, BACKUP_SH):
        code = _code_only(_read(p))
        for s in MARKETPLACE_SECRETS:
            assert s not in code, f"{p.name}: backup runner must not reference {s}"
    # backup writer must not hold the restore private identity
    for p in (BACKUP_ENV, BACKUP_SH):
        assert "IDENTITY" not in _code_only(_read(p)).upper(), f"{p.name}: writer must not hold restore identity"


def test_app_does_not_receive_backup_secrets():
    # backend app env + app compose must not carry backup/restore/S3 credentials
    for p in (BACKEND_ENV, APP_COMPOSE):
        t = _read(p)
        for tok in ("BACKUP_S3_", "RESTORE_S3_", "BACKUP_ENCRYPTION_RECIPIENT", "BACKUP_RESTORE_IDENTITY"):
            assert tok not in t, f"{p.name}: app must not receive backup secret {tok}"


def test_backup_compose_is_hardened_and_not_in_default_up():
    data = yaml.safe_load(_read(BACKUP_COMPOSE))
    svc = data["services"]["pg-backup"]
    assert svc.get("profiles") == ["backup"], "backup service must be profile-gated (not in `docker compose up`)"
    assert svc.get("read_only") is True
    assert svc.get("restart") == "no"
    assert svc.get("user", "").startswith("10002"), "non-root uid"
    assert "no-new-privileges:true" in (svc.get("security_opt") or [])
    assert (svc.get("cap_drop") or []) == ["ALL"]
    assert svc.get("tmpfs"), "plaintext temp must live in tmpfs"
    # no docker socket, no PGDATA mount, no host dump mount
    vols = svc.get("volumes") or []
    blob = "\n".join(vols) + "\n" + _read(BACKUP_COMPOSE)
    assert "docker.sock" not in blob, "no Docker socket"
    assert "/var/lib/postgresql/data" not in blob and "postgres_data" not in blob, "no PGDATA mount"


def test_workflow_pins_images_and_actions_and_readonly_perms():
    w = _read(WORKFLOW)
    assert "FROM postgres" not in w  # images come via services/run, not a FROM here
    assert MINIO_REF in w, "MinIO must be pinned tag@sha256"
    assert PG_DIGEST in w, "source/target PostgreSQL must use the pinned digest"
    # every `uses:` is SHA-pinned (40 hex)
    bad = [ln for ln in w.splitlines() if re.search(r"uses:\s", ln) and not re.search(r"@[0-9a-f]{40}", ln)]
    assert not bad, f"unpinned actions: {bad}"
    data = yaml.safe_load(w)
    perms = data.get("permissions") or (data.get(True) or {}).get("permissions")
    assert perms == {"contents": "read"} or perms == {"contents": "read\n"} or "contents: read" in w
    assert "persist-credentials: false" in w
    assert "--load" in w or "push: false" in w, "runner image is built+loaded, never pushed"
    # no dump/ciphertext/key artifact upload
    for m in re.findall(r"path:\s*(.+)", w):
        assert not re.search(r"\.dump|\.age|identity|\.sql", m), f"must not upload sensitive artifact: {m}"


def test_workflow_separates_source_and_target():
    w = _read(WORKFLOW)
    assert "source" in w.lower() and "target" in w.lower(), "restore must target a separate DB, not the source"


def test_ignores_block_dumps_and_keys():
    gi = _read(GITIGNORE) if GITIGNORE.exists() else ""
    di = _read(DOCKERIGNORE) if DOCKERIGNORE.exists() else ""
    for pat in ("*.dump", "*.dump.age", "*.age"):
        assert pat in gi, f".gitignore missing {pat}"
    for pat in ("*.dump", "*.age"):
        assert pat in di, f"backend/.dockerignore missing {pat}"
    assert "identity" in gi.lower() and "identity" in di.lower(), "private identities must be ignored"


def test_policy_is_honest_foundation_only():
    p = _read(POLICY).lower()
    assert "foundation" in p
    assert "not" in p and "pitr" in p, "policy must state PITR is not delivered"
    assert "3e1b-3b" in p and "3e1b-3c" in p, "policy must keep 3B/3C mandatory"
    assert "not" in p and ("automatic" in p or "daily" in p), "must not claim automatic backup"


def test_no_wal_or_pitr_added_to_production_compose():
    app = _read(APP_COMPOSE).lower()
    for tok in ("archive_mode", "archive_command", "restore_command", "wal-g", "pgbackrest"):
        assert tok not in app, f"3A must not add {tok} to the production compose (that is 3B)"
