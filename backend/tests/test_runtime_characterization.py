"""Runtime characterization suite (Sprint 70).

Golden-master protection for the deterministic runtime surface. The committed
snapshot (characterization/snapshots/runtime.json) is the REFERENCE behavior.
This test recomputes live outputs and asserts they equal the snapshot, case by
case. Any change to runtime behavior makes a case diverge -> the test fails.

Intentional behavior changes require regenerating the snapshot explicitly:

    CHAR_UPDATE=1 python -m pytest tests/test_runtime_characterization.py

That rewrites the snapshot; the diff must then be reviewed like any other
change. Without that env flag the snapshot is read-only.

See docs/governance/runtime_characterization.md.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from characterization.cases import build_cases

SNAPSHOT = Path(__file__).resolve().parent / "characterization" / "snapshots" / "runtime.json"


def _load_snapshot() -> dict:
    with SNAPSHOT.open(encoding="utf-8") as fh:
        return json.load(fh)


def test_snapshot_exists() -> None:
    if os.environ.get("CHAR_UPDATE") == "1":
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        with SNAPSHOT.open("w", encoding="utf-8") as fh:
            json.dump(build_cases(), fh, ensure_ascii=False, indent=2, sort_keys=True)
        pytest.skip("CHAR_UPDATE=1 — snapshot regenerated; rerun without the flag to verify.")
    assert SNAPSHOT.exists(), (
        "Characterization snapshot missing. Generate it once with "
        "`CHAR_UPDATE=1 python -m pytest tests/test_runtime_characterization.py`."
    )


def _case_names() -> list[str]:
    # Names come from the live driver so a newly-added case is auto-covered.
    return sorted(build_cases().keys())


@pytest.mark.skipif(os.environ.get("CHAR_UPDATE") == "1", reason="regenerating snapshot")
@pytest.mark.parametrize("name", _case_names())
def test_runtime_behavior_frozen(name: str) -> None:
    snapshot = _load_snapshot()
    live = build_cases()
    assert name in snapshot, (
        f"Case '{name}' has no recorded reference. A new runtime case was added. "
        f"Regenerate the snapshot with CHAR_UPDATE=1 and review the diff."
    )
    assert live[name] == snapshot[name], (
        f"RUNTIME BEHAVIOR CHANGED for case '{name}'.\n"
        f"reference: {snapshot[name]!r}\n"
        f"live:      {live[name]!r}\n"
        f"If intentional: rerun with CHAR_UPDATE=1 and review the snapshot diff."
    )


def test_no_orphaned_snapshot_cases() -> None:
    """Snapshot must not retain cases the driver no longer produces (silent drift)."""
    if os.environ.get("CHAR_UPDATE") == "1":
        pytest.skip("regenerating snapshot")
    snapshot = _load_snapshot()
    live = build_cases()
    orphaned = sorted(set(snapshot) - set(live))
    assert not orphaned, f"Snapshot has stale cases not produced by the driver: {orphaned}"
