"""SECURITY-2D-3E1B-1 — guard PostgreSQL major/variant parity across prod and CI.

The production database runs as the docker-compose `postgres` service (self-hosted on the
VDS per docs/LAUNCH_RUNBOOK.md), while the real-PostgreSQL security/concurrency matrix runs
in the postgres-explain and postgres-migration CI jobs. If those drift apart on major or
variant, the code is proven on one PostgreSQL but shipped on another. This guard asserts all
three reference EXACTLY the canonical `postgres:16-alpine`.

It is OFFLINE and parses the compose / workflow YAML STRUCTURALLY (service image fields),
never a global substring — a correct string in an unrelated job/service, or a version in a
comment, cannot satisfy it. Registry availability and digest pinning are out of scope here
(digest pin = 3E1B-2).
"""

from __future__ import annotations

from pathlib import Path

import yaml

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
COMPOSE = REPO / "docker-compose.yml"
CONSTITUTIONAL_YML = REPO / ".github" / "workflows" / "constitutional_verification.yml"

CANONICAL = "postgres:16-alpine"
# CI jobs that must run the real-PostgreSQL matrix on the canonical image.
EXPECTED_PG_CI_JOBS = {"postgres-explain", "postgres-migration"}


def _is_postgres_image(image: str) -> bool:
    """True for an official postgres image reference (library/postgres), any tag."""
    name = image.split("@", 1)[0].split(":", 1)[0]
    return name in ("postgres", "library/postgres", "docker.io/library/postgres")


def _assert_canonical(image: str, where: str) -> None:
    assert image, f"{where}: PostgreSQL service has no image tag"
    assert ":" in image, f"{where}: PostgreSQL image is missing a tag: {image!r}"
    tag = image.split("@", 1)[0].split(":", 1)[1]
    assert tag != "latest", f"{where}: 'latest' is forbidden for the DB image: {image!r}"
    assert not tag.startswith("15"), f"{where}: PostgreSQL 15 is forbidden (canonical is 16-alpine): {image!r}"
    assert tag.endswith("-alpine"), f"{where}: variant must be -alpine (canonical parity): {image!r}"
    assert image == CANONICAL, (
        f"{where}: PostgreSQL image must be exactly {CANONICAL!r}, got {image!r} — "
        "prod compose and both real-PG CI jobs must share major+variant"
    )


def _compose_postgres_images() -> dict[str, str]:
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = data.get("services", {}) or {}
    return {
        name: (svc or {}).get("image", "")
        for name, svc in services.items()
        if isinstance(svc, dict) and _is_postgres_image((svc.get("image") or ""))
    }


def _ci_postgres_service_images() -> dict[str, str]:
    """{job_id: postgres-service-image} for every workflow job with a postgres service."""
    data = yaml.safe_load(CONSTITUTIONAL_YML.read_text(encoding="utf-8"))
    jobs = data.get("jobs", {}) or {}
    out: dict[str, str] = {}
    for job_id, job in jobs.items():
        services = (job or {}).get("services", {}) or {}
        for svc in services.values():
            image = (svc or {}).get("image", "") if isinstance(svc, dict) else ""
            if _is_postgres_image(image):
                out[job_id] = image
    return out


def test_compose_has_exactly_one_canonical_postgres_service():
    pg = _compose_postgres_images()
    assert len(pg) == 1, (
        f"expected exactly one production PostgreSQL service in docker-compose.yml, "
        f"found {sorted(pg)} — an unexpected second stateful DB image must be classified"
    )
    (name, image), = pg.items()
    _assert_canonical(image, f"docker-compose.yml service {name!r}")


def test_both_real_pg_ci_jobs_use_canonical_image():
    ci = _ci_postgres_service_images()
    missing = EXPECTED_PG_CI_JOBS - set(ci)
    assert not missing, f"real-PostgreSQL CI job(s) missing a postgres service: {sorted(missing)}"
    for job_id, image in ci.items():
        _assert_canonical(image, f"constitutional_verification.yml job {job_id!r}")


def test_no_unexpected_extra_postgres_ci_service():
    ci = _ci_postgres_service_images()
    unexpected = set(ci) - EXPECTED_PG_CI_JOBS
    assert not unexpected, (
        f"unexpected job(s) running a PostgreSQL service: {sorted(unexpected)} — classify "
        "and add to EXPECTED_PG_CI_JOBS (each must be canonical) before merging"
    )


def test_all_three_contours_share_major_and_variant():
    images = set(_compose_postgres_images().values()) | set(_ci_postgres_service_images().values())
    assert images == {CANONICAL}, (
        f"PostgreSQL image parity broken across compose + CI: {sorted(images)} "
        f"(all must be exactly {CANONICAL!r})"
    )
