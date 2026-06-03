"""
Step 3 characterization — /insights data-path never fabricates demo.

The get_insights gate, when _compute_insights yields no insights, must return a
real empty response (is_demo=False) with has_data reflecting whether the user
has any imported rows — NOT _demo_response().

Static guarantees (no app/auth boot needed):
  * the gate no longer calls _demo_response()
  * the empty branch sets is_demo=False
  * _demo_response is defined but unreferenced in the production path
"""
import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "routers" / "action_engine.py").read_text(encoding="utf-8")


def _get_insights_body() -> str:
    # slice the get_insights endpoint function body
    start = SRC.index("async def get_insights(")
    nxt = re.search(r"\nasync def |\n@router|\ndef ", SRC[start + 10:])
    end = start + 10 + (nxt.start() if nxt else len(SRC))
    return SRC[start:end]


def test_gate_does_not_return_demo():
    body = _get_insights_body()
    assert "_demo_response" not in body, "get_insights must not return fabricated demo"
    assert "is_demo=False" in body, "empty branch must be real (is_demo=False)"
    assert "has_data=bool(" in body, "empty branch must set has_data from real import count"


def test_demo_response_has_no_production_callers():
    # _demo_response may still be DEFINED (sample/rollback) but never CALLED.
    defs  = len(re.findall(r"def _demo_response\(", SRC))
    calls = len(re.findall(r"(?<!def )_demo_response\(", SRC))
    assert defs == 1, f"expected 1 definition, found {defs}"
    assert calls == 0, f"_demo_response must have 0 callers, found {calls}"
