"""SECURITY-2D-3E1B-3C1 — offline guard for the DORMANT PITR/backup operations foundation.

Checks structural sections + exact mandatory constructs in docs/pitr-operations-policy.md, forbids the
unsafe negatives (activation-by-default, secrets in examples, scheduler/monitoring added, prod compose
wiring PITR), and scans the comment-only env examples for real credentials. No Docker, no network.
"""

from __future__ import annotations

import configparser
from copy import deepcopy
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
POLICY = REPO / "docs" / "pitr-operations-policy.md"
PROD_COMPOSE = REPO / "docker-compose.yml"
PGBR_EX = REPO / "ops" / "pitr" / "pgbackrest.conf.example"
BK_ENV = REPO / "ops" / "backup" / ".env.example"
RS_ENV = REPO / "ops" / "backup" / "restore.env.example"
EXAMPLES = (PGBR_EX, BK_ENV, RS_ENV)

INACTIVE_CUSTODIAN_UNIT = Path("ops/backup/custodian/pult-erasure-custodian.service")
INACTIVE_CUSTODIAN_CONTRACT = {
    "Unit": {
        "Description": "Pult erasure journal custodian (operator-provisioned, storage only)",
        "After": "network-online.target",
        "Wants": "network-online.target",
    },
    "Service": {
        "Type": "exec",
        "User": "pult-erasure-custodian",
        "Group": "pult-erasure-writer",
        "SupplementaryGroups": "",
        "WorkingDirectory": "/opt/pult-erasure-custodian",
        "LoadCredential": "journal-reader:/etc/pult-erasure-custodian/reader.json",
        "ExecStartPre": "/opt/pult-erasure-custodian/venv/bin/python -I -S -B /opt/pult-erasure-custodian/ops/backup/erasure_custodian_host.py --config /etc/pult-erasure-custodian/host.json --reader-credential-file ${CREDENTIALS_DIRECTORY}/journal-reader --check",
        "ExecStart": "/opt/pult-erasure-custodian/venv/bin/python -I -S -B /opt/pult-erasure-custodian/ops/backup/erasure_custodian_host.py --config /etc/pult-erasure-custodian/host.json --reader-credential-file ${CREDENTIALS_DIRECTORY}/journal-reader --serve",
        "Restart": "no",
        "UMask": "0077",
        "NoNewPrivileges": "yes",
        "CapabilityBoundingSet": "",
        "AmbientCapabilities": "",
        "ProtectSystem": "strict",
        "ProtectHome": "yes",
        "PrivateTmp": "yes",
        "PrivateDevices": "yes",
        "ProtectKernelTunables": "yes",
        "ProtectKernelModules": "yes",
        "ProtectControlGroups": "yes",
        "ProtectProc": "invisible",
        "RestrictNamespaces": "yes",
        "RestrictSUIDSGID": "yes",
        "LockPersonality": "yes",
        "SystemCallArchitectures": "native",
        "RestrictAddressFamilies": "AF_UNIX AF_INET AF_INET6",
        "ReadWritePaths": "/var/lib/pult-erasure-custodian /run/pult-erasure-custodian",
        "LimitCORE": "0",
        "StandardInput": "null",
        "StandardOutput": "journal",
        "StandardError": "journal",
    },
}


def _r(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _assert_inactive_custodian_unit(path: Path, repo: Path):
    for component in (path, *path.parents):
        assert not component.is_symlink() and not getattr(component, "is_junction", lambda: False)(), \
            "inactive custodian template must be unlinked"
        if component == repo:
            break
    assert path.is_file(), "inactive custodian template must be a regular file"
    parser = configparser.ConfigParser(interpolation=None, strict=True, delimiters=("=",))
    parser.optionxform = str
    try:
        text = _r(path)
        # ConfigParser hides even an empty [DEFAULT] from sections(). No defaults
        # are part of the approved contract, including empty default sections.
        assert not re.search(r"(?m)^\s*\[DEFAULT\]", text), "inactive custodian defaults forbidden"
        parser.read_string(text)
    except (configparser.Error, OSError, UnicodeError):
        raise AssertionError("invalid inactive custodian template") from None
    assert not parser.defaults(), "inactive custodian defaults forbidden"
    assert set(parser.sections()) == set(INACTIVE_CUSTODIAN_CONTRACT), "inactive custodian sections changed"
    for section, expected in INACTIVE_CUSTODIAN_CONTRACT.items():
        assert dict(parser.items(section, raw=True)) == expected, f"inactive custodian {section} contract changed"


def _assert_no_scheduler_or_monitoring_files(repo: Path):
    for base in (repo / "ops", repo / ".github", repo / "docs"):
        if not base.exists():
            continue
        for pattern in ("*.service", "*.timer", "*.cron", "crontab*", "*.alertmanager.yml"):
            for path in base.rglob(pattern):
                assert path.relative_to(repo) == INACTIVE_CUSTODIAN_UNIT, \
                    f"3C1 must not add scheduler/monitoring files: {path}"
                _assert_inactive_custodian_unit(path, repo)


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
    # Only the separately approved inactive template is permitted, never a general
    # service exclusion. Every original scope and scheduling/monitoring glob stays.
    _assert_no_scheduler_or_monitoring_files(REPO)


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


def _inactive_unit_text(contract=None):
    contract = INACTIVE_CUSTODIAN_CONTRACT if contract is None else contract
    return "\n\n".join("[" + section + "]\n" + "\n".join(key + "=" + value for key, value in fields.items())
                        for section, fields in contract.items()) + "\n"


def _write_unit_fixture(repo, relative=INACTIVE_CUSTODIAN_UNIT, text=None):
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_inactive_unit_text() if text is None else text, encoding="utf-8", newline="\n")
    return path


def test_exact_inactive_template_is_the_only_positive_exception(tmp_path):
    _write_unit_fixture(tmp_path)
    _assert_no_scheduler_or_monitoring_files(tmp_path)


def test_empty_original_scan_scopes_remain_allowed(tmp_path):
    for name in ("ops", ".github", "docs"):
        (tmp_path / name).mkdir()
    _assert_no_scheduler_or_monitoring_files(tmp_path)


@pytest.mark.parametrize("base", ["ops", ".github", "docs"])
@pytest.mark.parametrize("name", ["fixture.service", "fixture.timer", "fixture.cron", "crontab", "crontab.fixture", "fixture.alertmanager.yml"])
def test_every_original_pattern_is_forbidden_in_every_original_scope(tmp_path, base, name):
    _write_unit_fixture(tmp_path)  # A permitted hit must not end the scan early.
    _write_unit_fixture(tmp_path, Path(base) / "negative" / name)
    with pytest.raises(AssertionError, match="must not add scheduler/monitoring files"):
        _assert_no_scheduler_or_monitoring_files(tmp_path)


@pytest.mark.parametrize("relative", ["ops/backup/pult-erasure-custodian.service",
                                      ".github/pult-erasure-custodian.service",
                                      "docs/pult-erasure-custodian.service"])
def test_identical_template_with_same_basename_at_any_other_path_is_forbidden(tmp_path, relative):
    _write_unit_fixture(tmp_path, Path(relative))
    with pytest.raises(AssertionError, match="must not add scheduler/monitoring files"):
        _assert_no_scheduler_or_monitoring_files(tmp_path)


@pytest.mark.parametrize("section,key", [(section, key) for section, fields in INACTIVE_CUSTODIAN_CONTRACT.items()
                                        for key in fields])
def test_inactive_template_requires_every_fixed_contract_key(tmp_path, section, key):
    altered = deepcopy(INACTIVE_CUSTODIAN_CONTRACT)
    del altered[section][key]
    _write_unit_fixture(tmp_path, text=_inactive_unit_text(altered))
    with pytest.raises(AssertionError, match="contract changed"):
        _assert_no_scheduler_or_monitoring_files(tmp_path)


@pytest.mark.parametrize("key,value", [
    ("Restart", "always"), ("User", "root"), ("SupplementaryGroups", "root"),
    ("NoNewPrivileges", "no"), ("CapabilityBoundingSet", "CAP_SYS_ADMIN"),
    ("ProtectSystem", "no"), ("ReadWritePaths", "/"),
    ("RestrictAddressFamilies", "AF_UNIX AF_INET AF_INET6 AF_PACKET"),
    ("LoadCredential", "journal-reader:/unapproved/reader.json"),
    ("ExecStartPre", INACTIVE_CUSTODIAN_CONTRACT["Service"]["ExecStartPre"].replace("--check", "--serve")),
    ("ExecStart", INACTIVE_CUSTODIAN_CONTRACT["Service"]["ExecStart"].replace(" -S", "")),
    ("ExecStart", INACTIVE_CUSTODIAN_CONTRACT["Service"]["ExecStart"] + " --bootstrap"),
    ("ExecStart", "/bin/sh -c 'echo synthetic-unapproved-command'"),
])
def test_altered_activation_commands_credentials_or_rights_are_rejected(tmp_path, key, value):
    altered = deepcopy(INACTIVE_CUSTODIAN_CONTRACT)
    altered["Service"][key] = value
    _write_unit_fixture(tmp_path, text=_inactive_unit_text(altered))
    with pytest.raises(AssertionError, match="contract changed"):
        _assert_no_scheduler_or_monitoring_files(tmp_path)


@pytest.mark.parametrize("key,value", [
    ("ExecStartPost", "/bin/true"), ("ExecStop", "/bin/true"),
    ("ExecStopPost", "/usr/bin/rm /run/pult-erasure-custodian/custodian.sock"),
    ("ExecReload", "/bin/true"), ("ExecCondition", "/bin/true"),
    ("RuntimeDirectory", "pult-erasure-custodian"), ("RuntimeDirectoryPreserve", "no"),
    ("StateDirectory", "pult-erasure-custodian"), ("CacheDirectory", "pult-erasure-custodian"),
    ("ConfigurationDirectory", "pult-erasure-custodian"), ("LogsDirectory", "pult-erasure-custodian"),
    ("DynamicUser", "yes"), ("RemoveIPC", "yes"),
    ("Environment", "READER_KEY=synthetic-forbidden-literal"),
    ("EnvironmentFile", "/synthetic/forbidden.env"), ("PassEnvironment", "READER_KEY"),
    ("SetCredential", "journal-reader:synthetic-forbidden-literal"),
    ("UnknownKey", "yes"), ("restart", "always"),
])
def test_extra_hooks_managed_directories_environment_and_unknown_keys_are_rejected(tmp_path, key, value):
    altered = deepcopy(INACTIVE_CUSTODIAN_CONTRACT)
    altered["Service"][key] = value
    _write_unit_fixture(tmp_path, text=_inactive_unit_text(altered))
    with pytest.raises(AssertionError, match="contract changed"):
        _assert_no_scheduler_or_monitoring_files(tmp_path)


@pytest.mark.parametrize("extra", ["[Install]\n", "[Install]\nWantedBy=multi-user.target\n", "[Timer]\nOnCalendar=daily\n",
                                  "[Unknown]\n", "[DEFAULT]\n", "\f[DEFAULT]\n", "[DEFAULT]\nRestart=always\n", "[unit]\n"])
def test_install_defaults_and_unknown_sections_are_forbidden_even_when_empty(tmp_path, extra):
    _write_unit_fixture(tmp_path, text=_inactive_unit_text() + "\n" + extra)
    with pytest.raises(AssertionError):
        _assert_no_scheduler_or_monitoring_files(tmp_path)


@pytest.mark.parametrize("text", [
    _inactive_unit_text().replace("Restart=no", "Restart=no\nRestart=always"),
    _inactive_unit_text() + "\n[Service]\n",
    _inactive_unit_text().replace("[Unit]", "[Unit"),
    _inactive_unit_text().replace("Restart=no", "Restart"),
    _inactive_unit_text().replace("[Service]", "[service]"),
    _inactive_unit_text().replace("Restart=no", "restart=no"),
])
def test_ambiguous_malformed_duplicate_or_wrong_case_unit_is_rejected(tmp_path, text):
    _write_unit_fixture(tmp_path, text=text)
    with pytest.raises(AssertionError):
        _assert_no_scheduler_or_monitoring_files(tmp_path)


@pytest.mark.parametrize("section,key", [("Unit", "After"), ("Service", "User"),
    ("Service", "NoNewPrivileges"), ("Service", "ProtectSystem"),
    ("Service", "CapabilityBoundingSet"), ("Service", "ExecStart")])
def test_colon_assignment_cannot_match_the_approved_template(tmp_path, section, key):
    value = INACTIVE_CUSTODIAN_CONTRACT[section][key]
    text = _inactive_unit_text().replace(key + "=" + value, key + ":" + value)
    _write_unit_fixture(tmp_path, text=text)
    with pytest.raises(AssertionError):
        _assert_no_scheduler_or_monitoring_files(tmp_path)


@pytest.mark.parametrize("linked", [INACTIVE_CUSTODIAN_UNIT, INACTIVE_CUSTODIAN_UNIT.parent, Path("ops")])
def test_link_flag_on_template_or_its_scoped_parents_refuses_before_read(tmp_path, monkeypatch, linked):
    # Model the Path link flag without requiring Windows symlink privileges. This
    # tests the guard decision, not OS ownership/isolation or link implementation.
    _write_unit_fixture(tmp_path)
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == tmp_path / linked or original(path))
    reads = []
    monkeypatch.setitem(globals(), "_r", lambda path: reads.append(path) or _inactive_unit_text())
    with pytest.raises(AssertionError, match="must be unlinked"):
        _assert_no_scheduler_or_monitoring_files(tmp_path)
    assert reads == []
