"""Golden characterization test for logic.cross_mp_root_cause (Sprint 71)."""
from characterization._engine import run_snapshot
from .cases import build_cases


def test_characterization():
    run_snapshot(__file__, build_cases)
