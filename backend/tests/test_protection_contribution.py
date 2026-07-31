"""PULT-LAUNCH-2.4 — expected-contribution engine (Model A). Pure Decimal logic, feature OFF.

Proves the three results stay independent (a proven loss is never lost to a missing provider
action), Model A never double-counts and never treats an unknown expense as 0, the Wilson upper
return bound is exact and conservative, and actionability is fail-closed (executable only with a
proven promo price + official tariff + confirmed capability).
"""
import ast
import inspect
import os
import sqlite3
from decimal import Decimal

import pytest

from services.protection import contribution as C
from services.protection.contribution import (
    ContributionInputs, Thresholds, CostLine, ReturnsStats, compute, wilson_upper,
    PROVIDER_OP, MANUAL_ADD, CONFIRMED_ZERO, MISSING,
    COMPLETE, INCOMPLETE, STALE, CONFLICT, SAFE, BELOW_TARGET, EMERGENCY,
    EXECUTABLE, MANUAL_ONLY, UNSUPPORTED,
)

D = Decimal


def _zero(key):
    return CostLine(key, CONFIRMED_ZERO)


def _inputs(**over):
    """A baseline COMPLETE calculation (economic=safe). Overridable per test."""
    base = dict(
        marketplace="ozon", store_id="s1", product_id="p1", promo_id="PR1",
        candidate_buyer_price=D("1000"), currency="RUB", promo_price_proven=False,
        cogs=CostLine("cogs", MANUAL_ADD, D("300"), source="manual"),
        cost_lines=[
            CostLine("commission", PROVIDER_OP, D("150"), source="api", ref="op-c"),
            CostLine("logistics", PROVIDER_OP, D("100"), source="api", ref="op-l"),
            _zero("storage"), _zero("acquiring"), _zero("penalties"), _zero("deductions"),
        ],
        returns=ReturnsStats(sold_units=100, returned_units=5, cost_per_return=D("50"),
                             cost_components=["reverse_logistics"], cost_proven=True),
        thresholds=Thresholds(emergency_abs=D("0"), target_margin_pct=D("10")),
        include_ad_spend=False, commission_official_tariff=False,
        provider_capability_confirmed=False,
    )
    base.update(over)
    return ContributionInputs(**base)


# ── Wilson upper: exact-ish fixtures + conservatism ──────────────────────────
def test_wilson_zero_history_is_not_zero_risk():
    u = wilson_upper(0, 100)
    assert u > 0                                    # no observed returns ≠ no return risk
    assert abs(u - D("0.036994")) < D("0.0005")


def test_wilson_upper_exceeds_observed_and_is_monotone():
    assert wilson_upper(5, 100) > D("0.05")         # upper bound above the point estimate
    assert abs(wilson_upper(5, 100) - D("0.111751")) < D("0.0005")
    assert wilson_upper(10, 100) > wilson_upper(5, 100)
    assert wilson_upper(0, 30) > wilson_upper(0, 100)   # smaller sample → wider bound


def test_wilson_requires_positive_sample():
    with pytest.raises(ValueError):
        wilson_upper(0, 0)


# ── return sample gate ───────────────────────────────────────────────────────
def test_sample_below_min_incomplete():
    r = compute(_inputs(returns=ReturnsStats(sold_units=29, returned_units=1,
                                             cost_per_return=D("50"), cost_proven=True)))
    assert r.calculation_status == INCOMPLETE and "returns_history_insufficient" in r.missing_fields


def test_sample_at_min_allowed():
    r = compute(_inputs(returns=ReturnsStats(sold_units=30, returned_units=1,
                                             cost_per_return=D("50"), cost_proven=True)))
    assert "returns_history_insufficient" not in r.missing_fields
    assert r.calculation_status == COMPLETE


def test_cancellations_cannot_inflate_returns():
    # there is no cancellation input — only returned_units — so a cancellation can never raise the rate
    assert "cancellation" not in {f for f in ReturnsStats.__dataclass_fields__}


def test_return_without_proven_cost_incomplete():
    r = compute(_inputs(returns=ReturnsStats(sold_units=100, returned_units=5,
                                             cost_per_return=None, cost_proven=False)))
    assert r.calculation_status == INCOMPLETE and "return_cost_unconfirmed" in r.missing_fields


def test_returned_unit_does_not_auto_lose_full_cogs():
    # cost_per_return (50) is used, NOT the full cogs (300) — item may be resellable
    r = compute(_inputs(returns=ReturnsStats(sold_units=100, returned_units=5,
                                             cost_per_return=D("50"), cost_proven=True)))
    # expected_return_cost = wilson_upper(5,100) * 50 ≈ 5.59 (far below a full-cogs 300*rate)
    assert r.calculation_status == COMPLETE
    assert r.projected_contribution is not None and r.projected_contribution > D("400")


# ── commission / price gate actionability ────────────────────────────────────
def test_historical_commission_is_manual_only():
    r = compute(_inputs(commission_official_tariff=False, provider_capability_confirmed=True,
                        promo_price_proven=True,
                        thresholds=Thresholds(emergency_abs=D("1000"), target_margin_pct=D("10"))))
    assert r.economic_verdict == EMERGENCY            # forced emergency
    assert r.actionability == MANUAL_ONLY             # historical commission blocks executable


def test_no_official_tariff_not_executable():
    r = compute(_inputs(commission_official_tariff=False, promo_price_proven=True,
                        provider_capability_confirmed=True,
                        thresholds=Thresholds(emergency_abs=D("1000"), target_margin_pct=D("10"))))
    assert r.actionability != EXECUTABLE


def test_catalog_price_is_not_promo_price():
    r = compute(_inputs(promo_price_proven=False, commission_official_tariff=True,
                        provider_capability_confirmed=True,
                        thresholds=Thresholds(emergency_abs=D("1000"), target_margin_pct=D("10"))))
    assert r.actionability == MANUAL_ONLY             # no proven promo price → never executable


def test_all_preconditions_met_is_executable():
    r = compute(_inputs(promo_price_proven=True, commission_official_tariff=True,
                        provider_capability_confirmed=True,
                        thresholds=Thresholds(emergency_abs=D("1000"), target_margin_pct=D("10"))))
    assert r.calculation_status == COMPLETE and r.economic_verdict == EMERGENCY
    assert r.actionability == EXECUTABLE


def test_proven_loss_survives_missing_action():
    # complete + emergency, but provider capability not confirmed → verdict kept, action unsupported
    r = compute(_inputs(provider_capability_confirmed=False,
                        thresholds=Thresholds(emergency_abs=D("1000"), target_margin_pct=D("10"))))
    assert r.calculation_status == COMPLETE
    assert r.economic_verdict == EMERGENCY and r.actionability == UNSUPPORTED


# ── advertising ──────────────────────────────────────────────────────────────
def test_ad_default_off_and_evidence():
    r = compute(_inputs(include_ad_spend=False))
    assert r.evidence["advertising_included"] is False


def test_include_ad_without_attribution_incomplete():
    r = compute(_inputs(include_ad_spend=True, ad_attributed=False))
    assert r.calculation_status == INCOMPLETE and "ad_unattributed" in r.missing_fields


def test_policy_model_default_ad_off():
    from models.protection import ProtectionPolicy
    assert ProtectionPolicy.__table__.c.include_ad_spend.default.arg is False


# ── cost-key dedup / attribution ─────────────────────────────────────────────
def test_duplicate_cost_key_is_conflict_not_sum():
    r = compute(_inputs(cost_lines=[
        CostLine("commission", PROVIDER_OP, D("150"), source="api", ref="a"),
        CostLine("commission", PROVIDER_OP, D("150"), source="api", ref="b"),
        CostLine("logistics", PROVIDER_OP, D("100"), source="api"),
        _zero("storage"), _zero("acquiring"), _zero("penalties"), _zero("deductions")]))
    assert r.calculation_status == CONFLICT
    assert any("duplicate_cost:commission" in x for x in r.reasons)


def test_provider_and_manual_same_key_conflict():
    r = compute(_inputs(cost_lines=[
        CostLine("logistics", PROVIDER_OP, D("100"), source="api"),
        CostLine("logistics", MANUAL_ADD, D("40"), source="manual"),
        CostLine("commission", PROVIDER_OP, D("150"), source="api"),
        _zero("storage"), _zero("acquiring"), _zero("penalties"), _zero("deductions")]))
    assert r.calculation_status == CONFLICT


def test_unassigned_store_expense_incomplete():
    r = compute(_inputs(cost_lines=[
        CostLine("commission", PROVIDER_OP, D("150"), source="api"),
        CostLine("logistics", PROVIDER_OP, D("100"), source="api"),
        CostLine("storage", MISSING),          # unknown → not 0
        _zero("acquiring"), _zero("penalties"), _zero("deductions")]))
    assert r.calculation_status == INCOMPLETE and "storage_unconfirmed" in r.missing_fields


def test_deduction_not_guessed_as_acquiring():
    # acquiring absent entirely → still required-explicit → incomplete (never folded into deductions)
    r = compute(_inputs(cost_lines=[
        CostLine("commission", PROVIDER_OP, D("150"), source="api"),
        CostLine("logistics", PROVIDER_OP, D("100"), source="api"),
        _zero("storage"), _zero("penalties"), _zero("deductions")]))   # no acquiring line
    assert r.calculation_status == INCOMPLETE and "acquiring_unconfirmed" in r.missing_fields


def test_missing_cogs_incomplete():
    r = compute(_inputs(cogs=CostLine("cogs", MISSING)))
    assert r.calculation_status == INCOMPLETE and "no_cogs" in r.missing_fields


# ── status priority ──────────────────────────────────────────────────────────
def test_conflict_beats_incomplete_and_stale():
    r = compute(_inputs(cogs=CostLine("cogs", MISSING), source_conflicts=["revenue"],
                        stale_metrics=["price"]))
    assert r.calculation_status == CONFLICT


def test_incomplete_beats_stale():
    r = compute(_inputs(cogs=CostLine("cogs", MISSING), stale_metrics=["price"]))
    assert r.calculation_status == INCOMPLETE


def test_stale_only():
    r = compute(_inputs(stale_metrics=["price"]))
    assert r.calculation_status == STALE and r.economic_verdict is None


# ── thresholds table (per-unit contribution, revenue=100 → pct = value) ──────
def _at(projected, *, emergency_abs="0", emergency_pct=None, target="10"):
    # price 100, cogs tuned so contribution == projected; zero return cost
    cogs = D("100") - D(projected)
    return compute(_inputs(
        candidate_buyer_price=D("100"),
        cogs=CostLine("cogs", MANUAL_ADD, cogs, source="manual"),
        cost_lines=[_zero("commission"), _zero("logistics"), _zero("storage"),
                    _zero("acquiring"), _zero("penalties"), _zero("deductions")],
        returns=ReturnsStats(sold_units=100, returned_units=0, cost_per_return=D("0"), cost_proven=True),
        thresholds=Thresholds(emergency_abs=D(emergency_abs),
                              emergency_pct=(D(emergency_pct) if emergency_pct else None),
                              target_margin_pct=D(target))))


def test_threshold_table_abs_zero():
    assert _at("100").economic_verdict == SAFE            # +100 (100% ≥ 10)
    assert _at("1").economic_verdict == BELOW_TARGET      # +1 (1% < 10, > 0)
    assert _at("0").economic_verdict == EMERGENCY         # 0 ≤ 0
    assert _at("-1").economic_verdict == EMERGENCY
    assert _at("-100").economic_verdict == EMERGENCY


def test_threshold_table_with_pct():
    # emergency_pct=5% → +1 (1% ≤ 5%) is emergency; +100 (100%) safe
    assert _at("1", emergency_pct="5").economic_verdict == EMERGENCY
    assert _at("100", emergency_pct="5").economic_verdict == SAFE


def test_threshold_negative_abs():
    # emergency_abs=-50 → only ≤ -50 is emergency; -1 is below_target
    assert _at("-100", emergency_abs="-50").economic_verdict == EMERGENCY
    assert _at("-1", emergency_abs="-50").economic_verdict == BELOW_TARGET
    assert _at("0", emergency_abs="-50").economic_verdict == BELOW_TARGET


# ── currency / determinism / no-revenue ──────────────────────────────────────
def test_currency_mismatch_conflict():
    r = compute(_inputs(cost_lines=[
        CostLine("commission", PROVIDER_OP, D("150"), source="api", currency="USD"),
        CostLine("logistics", PROVIDER_OP, D("100"), source="api", currency="RUB"),
        _zero("storage"), _zero("acquiring"), _zero("penalties"), _zero("deductions")]))
    assert r.calculation_status == CONFLICT


def test_no_price_incomplete():
    r = compute(_inputs(candidate_buyer_price=None))
    assert r.calculation_status == INCOMPLETE and "revenue_unknown" in r.missing_fields


def test_deterministic_evidence():
    from datetime import datetime
    ts = datetime(2026, 7, 26, 12, 0, 0)
    a = compute(_inputs(evaluated_at=ts)).evidence
    b = compute(_inputs(evaluated_at=ts)).evidence
    assert a == b


def test_decimal_not_float():
    r = compute(_inputs())
    assert isinstance(r.projected_contribution, Decimal)
    assert r.projected_contribution == r.projected_contribution.quantize(D("0.01"))


# ── feature OFF / no provider calls / containment ────────────────────────────
def test_engine_imports_no_marketplace_client():
    src = inspect.getsource(C)
    tree = ast.parse(src)
    mods = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.append(n.module)
    for bad in ("wb_client", "ozon_client", "executor", "action_catalog", "marketplace.ingest"):
        assert not any(bad in m for m in mods), bad


def test_stop_auto_promotion_still_contained():
    from services.decision_outcome.decision_bridge import capability_supported
    assert capability_supported("stop_auto_promotion", "wildberries") is False
    assert capability_supported("stop_auto_promotion", "ozon") is False


# ── migration: include_ad_spend default off + single head ────────────────────
def test_single_alembic_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["atl1a2b3c4d01"]


def test_migration_upgrade_downgrade_reupgrade(tmp_path):
    from alembic.config import Config
    from alembic import command
    db = tmp_path / "mig.db"
    os.environ["ALEMBIC_DATABASE_URL"] = f"sqlite+aiosqlite:///{db.as_posix()}"
    try:
        cfg = Config("alembic.ini")
        command.upgrade(cfg, "plp1a2b3c4d01")
        con = sqlite3.connect(db)
        con.execute("PRAGMA foreign_keys=OFF")
        # a pre-migration row created under the old default would be include_ad_spend=1
        con.execute("INSERT INTO users(id,email,name,hashed_password) VALUES('u1','a@b.c','A','x')")
        con.execute("INSERT INTO workspaces(id,owner_user_id,created_at) VALUES('ws1','u1',CURRENT_TIMESTAMP)")
        con.execute("INSERT INTO marketplace_accounts(id,workspace_id,marketplace,identity_status) "
                    "VALUES('acc','ws1','wildberries','unverified')")
        con.execute("INSERT INTO marketplace_stores"
                    "(id,marketplace_account_id,marketplace,store_key,label,source,status,created_at,updated_at)"
                    " VALUES('s1','acc','wildberries','primary','S','manual','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        con.execute("INSERT INTO protection_policies(id,marketplace_store_id,include_ad_spend) VALUES('pol','s1',1)")
        con.commit(); con.close()

        command.upgrade(cfg, "head")
        con = sqlite3.connect(db)
        # backfilled to false; default flipped to '0'
        assert con.execute("SELECT include_ad_spend FROM protection_policies WHERE id='pol'").fetchone()[0] == 0
        dflt = [r for r in con.execute("PRAGMA table_info('protection_policies')")
                if r[1] == "include_ad_spend"][0][4]
        assert str(dflt).strip("'") == "0"          # SQLite stores the server_default as '0'
        con.close()

        command.downgrade(cfg, "plp1a2b3c4d01")
        command.upgrade(cfg, "head")          # re-upgrade must succeed
    finally:
        os.environ.pop("ALEMBIC_DATABASE_URL", None)
