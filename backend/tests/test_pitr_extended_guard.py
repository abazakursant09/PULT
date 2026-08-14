"""SECURITY-2D-3E1B-3B2/B2 — offline guard for the EXTENDED PITR fault matrix workflow.

Structural checks only (no Docker). Requires the extended workflow to carry the DEFINING evidence
of each B2 scenario (not merely a case name), keeps the B1 foundation (positive + A–L 12/12)
unweakened, and forbids the unsafe shapes (unbounded sleep as proof, `|| true` on assertions,
artifact/image publish, missing cleanup/unpause).
"""

from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
EXT = REPO / ".github" / "workflows" / "pitr_extended.yml"
SYNTH = REPO / ".github" / "workflows" / "pitr_synthetic.yml"
POLICY = REPO / "docs" / "pitr-policy.md"


def _r(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _code(t: str) -> str:
    return "\n".join(ln for ln in t.splitlines() if not ln.lstrip().startswith("#"))


def test_files_exist():
    for p in (EXT, SYNTH, POLICY):
        assert p.is_file(), f"missing: {p.relative_to(REPO)}"


def test_workflow_yaml_loads_and_triggers():
    import yaml
    d = yaml.safe_load(_r(EXT))
    on = d[True] if True in d else d.get("on")
    assert on is not None and "workflow_dispatch" in on, "must be workflow_dispatch-runnable (PR evidence)"
    assert "schedule" in on, "must have a nightly schedule for post-merge runs"
    job = d["jobs"]["pitr-extended"]
    assert job["timeout-minutes"] <= 45, "job timeout must be <= 45 minutes"
    assert d.get("permissions", {}).get("contents") == "read"


def test_concurrency_group_no_cancel():
    import yaml
    d = yaml.safe_load(_r(EXT))
    conc = d.get("concurrency")
    assert isinstance(conc, dict) and conc.get("group") and conc.get("cancel-in-progress") is False, \
        "extended workflow must serialize (concurrency group, cancel-in-progress:false)"


def test_unique_resources_not_shared_with_b1():
    w = _r(EXT)
    assert "pitrnet-ext" in w and "pitr_ext_" in w, "must use unique network/volume names"
    assert "docker network create pitrnet\n" not in w and "pitrnet " not in w.replace("pitrnet-ext", ""), \
        "must not reuse the B1 'pitrnet' network name"


def test_pinned_and_no_artifacts_no_publish():
    w = _r(EXT)
    assert "minio/minio:RELEASE.2025-09-07T16-13-09Z@sha256:14cea493" in w, "MinIO pinned"
    bad = [ln for ln in w.splitlines() if re.search(r"uses:\s", ln) and not re.search(r"@[0-9a-f]{40}", ln)]
    assert not bad, f"unpinned actions: {bad}"
    assert "upload-artifact" not in w and "actions/cache" not in w, "no artifacts/cache (WAL/repo/keys must never leave CI)"
    assert "docker push" not in w and "--push" not in w, "must not publish images"
    assert "verify-tls=n" not in w, "TLS verify must stay enabled"


def test_cleanup_always_and_unpause():
    w = _r(EXT)
    assert "if: always()" in w, "teardown must run always"
    tail = w.split("Teardown", 1)[-1]
    assert "docker unpause minio" in tail, "teardown must unpause MinIO before removal"
    assert "docker network rm pitrnet-ext" in tail


def _matrix(w: str) -> str:
    # the big fault-matrix step body
    start = w.index("Extended fault matrix")
    end = w.index("name: Teardown", start)
    return w[start:end]


def test_bounded_polling_no_asserted_or_true():
    m = _code(_matrix(_r(EXT)))
    # every drain/appearance loop is a bounded for-seq with a break (no infinite/naked waits)
    assert "for k in $(seq 1" in m, "must use bounded polling loops"
    # a failing assertion must never be swallowed (these literal shapes would hide a real failure);
    # `|| true` is allowed ONLY on cleanup docker commands, never on an assertion/exit.
    assert "exit 1 || true" not in m and "|| true; then" not in m, "assertions/exits must not be swallowed with || true"
    assert not re.search(r'\]\s*\|\|\s*true', m), "a bracket-test assertion must not be swallowed with || true"
    # floor on bounded loops so replacing a critical drain/appearance loop with a naked sleep drops it
    assert m.count("for k in $(seq 1") >= 12, "critical drain/appearance loops must stay bounded (floor)"
    # the summary asserts all sections passed (no partial green)
    assert re.search(r'test "\$PASS" = 5', m), "must assert all 5 sections passed"


def test_probe_present():
    m = _matrix(_r(EXT))
    assert "PROBE" in m and "spool" in m and "pg_wal" in m, "must probe where backlog physically lives"
    assert "not in pg_wal" in m, "probe must physically assert exact segs retained in pg_wal"


def test_long_outage_measured():
    m = _matrix(_r(EXT))
    assert "LONG async S3 outage" in m
    assert "docker pause minio" in m and "docker unpause minio" in m, "outage = pause (DNS ok), not network cut"
    assert "N=8;" in m, "long outage must set N=8 segments (assignment, not just an echo)"
    assert "still reachable during pause" in m, "must confirm true isolation before generating backlog"
    assert "failed_count moved" in m, "must assert foreground accept (failed_count flat) under async"
    # physical local-retention proof must be the ACTUAL guarded check, not just its message
    assert 'wal_has longsrc "$s" ||' in m, "each exact seg must be physically proven retained in pg_wal"
    assert "offsite during outage" in m, "must prove each exact seg absent offsite during outage"
    assert "continuity intact during outage" in m, "status must be unsafe during outage"
    # exact-segment remote drain must be the ACTUAL bounded loop + guarded assertion
    assert 'for k in $(seq 1 60); do seg_in_s3 "$s" pult-long' in m, "drain must poll each exact seg in a bounded loop"
    assert '[ "$d" = 1 ] || { echo "LONG FAIL: $s never drained after S3 return"' in m, "must assert each exact seg drained"
    assert "drain rate" in m and "NOT production RPO" in m.replace("RPO/RTO", "RPO"), "drain-rate measured but not sold as SLO"
    assert "max_tested_backlog_bytes" in m


def test_concurrent_writers_enforced():
    """The three concurrent writers must be ENFORCED, not merely evidenced: own PIDs, each waited
    with exit-status checked, a DB barrier proving simultaneous liveness, and exact 40/40/40 counts."""
    m = _code(_matrix(_r(EXT)))  # executable lines only (comments must not satisfy the guard)
    # three separate background writer PIDs
    assert "PID1=$!" in m and "PID2=$!" in m and "PID3=$!" in m, "each writer must be its own tracked background PID"
    # each PID explicitly waited, and its exit status checked (never swallowed)
    assert 'wait "$PID1"' in m and 'wait "$PID2"' in m and 'wait "$PID3"' in m, "must wait each writer PID"
    assert "wait || true" not in m, "writer failures must not be swallowed with `wait || true`"
    assert "a writer exited nonzero" in m, "any nonzero writer exit must FAIL the job"
    assert re.search(r'\[ "\$s1" = 0 \] && \[ "\$s2" = 0 \] && \[ "\$s3" = 0 \]', m), "all three exit statuses must be checked"
    # DB barrier proving all three alive simultaneously before load
    assert "CREATE TABLE cbar" in m, "must use a synthetic DB barrier table"
    assert "ready_count != 3" in m and re.search(r'\[ "\$rc" = 3 \]', m), "barrier must require ready_count==3"
    assert "for t in $(seq 1 60)" in m, "barrier readiness poll must be bounded (deadline)"
    assert "INSERT INTO cbar(k,v) VALUES ('release',1)" in m, "release only after all three are ready"
    # release must come AFTER the ready barrier (not before)
    assert m.index("ready_count != 3") < m.index("VALUES ('release',1)"), "release must follow the ready barrier"
    # exact per-writer + total + distinct assertions (no weak threshold)
    assert re.search(r'\[ "\$w1" = 40 \]', m) and re.search(r'\[ "\$w2" = 40 \]', m) and re.search(r'\[ "\$w3" = 40 \]', m), "each writer must commit exactly 40"
    assert re.search(r'\[ "\$tot" = 120 \]', m) and re.search(r'\[ "\$dis" = 3 \]', m), "total=120 and distinct writers=3"
    assert "R_all >=" not in m and "R_all>=" not in m, "no weak row threshold"


def test_concurrent_multi_lsn_restore():
    m = _matrix(_r(EXT))
    # 3 DISTINCT ordered LSN checkpoints — each from its own rec_point call (not aliased)
    assert "rec_point M1" in m and "rec_point M2" in m and "rec_point M3" in m, "three distinct LSN checkpoints"
    assert 'LSN3="$(rec_point M3)"' in m, "LSN3 must be its own checkpoint, not aliased to another LSN"
    assert "LSN not strictly increasing" in m, "must assert LSN1<LSN2<LSN3"
    assert "authoritative order = numeric LSN" in m, "must not assume commit/filename order == LSN order"
    # two independent restore targets to different LSN
    assert "restore_to ctgtA" in m and "restore_to ctgtB" in m, "two independent restore targets"
    assert "target-A markers wrong" in m and "target-B missing M3" in m, "row assertions at each target"


def test_restart_matrix():
    m = _matrix(_r(EXT))
    assert "RESTART-A" in m, "PG restart empty backlog"
    assert "RESTART-B" in m and "LOST across restart" in m, "PG restart WITH backlog preserves exact segs"
    assert "RECREATE" in m and "lost across container recreation" in m, "container recreation on preserved volume"
    assert "RETRY" in m and "was NOT refused" in m, "restore-retry into non-empty target must fail-closed"


def test_corruption_scratch_repo():
    m = _matrix(_r(EXT))
    assert "scratch_copy" in m, "corruption must operate on a scratch copy, not the canonical repo"
    assert "pult-cor-trunc" in m and "pult-cor-zero" in m and "pult-cor-miss" in m, "truncation/zero/missing-middle"
    assert "canonical pult-cor untouched" in m
    assert "reached/promoted target" in m, "each corruption must be fail-closed (no promote)"


def test_b1_foundation_preserved():
    s = _r(SYNTH)
    # positive + full A–L matrix + pass==n>=12 must remain in the B1 workflow, unweakened
    for case in ("A missing base", "E S3 outage", "L async S3 outage", "PITR SYNTHETIC OK"):
        assert case in s, f"B1 workflow lost: {case!r}"
    assert re.search(r'test "\$pass" = "\$n"', s) and re.search(r'test "\$n" -ge 12', s), "B1 must stay pass==n, n>=12"


def test_policy_marks_b2_covered_and_deferred():
    p = _r(POLICY).lower()
    assert "b2 covered" in p or "covered synthetically" in p or "pitr_extended" in p, "policy must record what B2 covers"
    # honesty carried from 3B1 must remain
    assert "rpo=0" in p and "loss of the whole vds" in p
    assert "minio" in p and "selectel" in p, "policy must keep MinIO != Selectel honesty"
    # deferred items must be named as 3C, not claimed covered
    for deferred in ("disk", "multipart", "object lock", "iam"):
        assert deferred in p, f"policy must address deferred item honestly: {deferred}"
