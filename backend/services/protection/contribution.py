"""
PULT-LAUNCH-2.4 — expected-contribution calculation engine (Model A). Pure, Decimal, DB-free.

This is the CALCULATION service: given the inputs a caller has PROVEN, it returns THREE
independent results — never conflated — plus a full evidence dict:

  calculation_status ∈ complete | incomplete | stale | conflict
  economic_verdict   ∈ safe | below_target_margin | emergency_zero_or_loss  (None unless complete)
  actionability      ∈ executable | manual_only | unsupported

Model A: projected_contribution starts from the buyer-facing sale revenue (BEFORE marketplace
deductions) and subtracts each cost exactly ONCE. A missing required cost is NEVER treated as 0.
One cost_key may carry at most one contributing origin (a provider operation OR a manual line, never
both) — a double origin is a conflict, not a sum.

Fail-closed: actionability is `executable` ONLY when every hard precondition holds — a fresh proven
promo price, an official current commission tariff, cogs, all required costs proven, a sufficient
return history with a proven per-return cost, a single currency, an unambiguous store/product/promo,
no source conflict, and a confirmed provider capability — AND the economic verdict is
emergency_zero_or_loss. On today's data (no promo-price / tariff ingest) the honest result is
manual_only / unsupported; that is correct, not a reason to relax a check.

Gathering these inputs from real readers is a LATER slice (it needs promo-price + tariff ingest,
out of 2.4 scope). No provider call, no scheduler, no persistence — the caller decides what to store.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Mapping, Optional, Sequence

FORMULA_VERSION = "contribution-A-1"
ROUNDING_MODE = "ROUND_HALF_UP"
_MONEY = Decimal("0.01")
_PCT = Decimal("0.001")
# z for a 95% one-sided/upper Wilson bound (two-sided 95% z; used for the UPPER bound = conservative)
WILSON_Z_95 = Decimal("1.959964")
DEFAULT_RETURN_WINDOW_DAYS = 90
DEFAULT_MIN_RETURN_SAMPLE = 30

# calculation_status
COMPLETE, INCOMPLETE, STALE, CONFLICT = "complete", "incomplete", "stale", "conflict"
# economic_verdict
SAFE, BELOW_TARGET, EMERGENCY = "safe", "below_target_margin", "emergency_zero_or_loss"
# actionability
EXECUTABLE, MANUAL_ONLY, UNSUPPORTED = "executable", "manual_only", "unsupported"

# canonical cost keys — the money() reader's metrics map onto these; each is deducted once
COST_KEYS = (
    "cogs", "commission", "logistics", "last_mile", "storage", "acquiring", "advertising",
    "penalties", "deductions", "taxes", "return_cost",
)  # plus "additional:<name>" for typed seller costs
# These must be EXPLICITLY resolved (a real amount, confirmed_zero, or not_applicable). Absence or a
# MISSING origin → incomplete: an unknown expense is never silently 0, and storage/acquiring are never
# auto-folded into deductions. last_mile/advertising/taxes are conditional and handled separately.
_MUST_BE_EXPLICIT = ("cogs", "commission", "logistics", "storage", "acquiring", "penalties", "deductions")

# cost-line origin
PROVIDER_OP, MANUAL_ADD, CONFIRMED_ZERO, ESTIMATED, MISSING, NOT_APPLICABLE = (
    "provider_operation", "manual_additional", "confirmed_zero", "estimated", "missing", "not_applicable")
_REAL_ORIGINS = (PROVIDER_OP, MANUAL_ADD)   # origins that carry a real, seller-affecting amount


def _q(v: Optional[Decimal], quant: Decimal = _MONEY) -> Optional[Decimal]:
    return None if v is None else Decimal(v).quantize(quant, rounding=ROUND_HALF_UP)


def wilson_upper(returned_units: int, sold_units: int, z: Decimal = WILSON_Z_95) -> Decimal:
    """Upper bound of the Wilson score interval for the return rate (conservative). Pure Decimal.

        center = (p + z^2/2n) / (1 + z^2/n)
        margin = z * sqrt( p(1-p)/n + z^2/4n^2 ) / (1 + z^2/n)
        upper  = clamp(center + margin, 0, 1)
    """
    n = Decimal(sold_units)
    if n <= 0:
        raise ValueError("sold_units must be > 0 for a Wilson bound")
    p = Decimal(returned_units) / n
    z2 = z * z
    denom = Decimal(1) + z2 / n
    center = (p + z2 / (2 * n)) / denom
    under = p * (Decimal(1) - p) / n + z2 / (4 * n * n)
    margin = z * under.sqrt() / denom
    upper = center + margin
    if upper < 0:
        upper = Decimal(0)
    if upper > 1:
        upper = Decimal(1)
    return upper.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class CostLine:
    cost_key: str
    origin: str                       # PROVIDER_OP | MANUAL_ADD | CONFIRMED_ZERO | ESTIMATED | MISSING | NOT_APPLICABLE
    amount: Optional[Decimal] = None  # per-unit money; None for confirmed_zero/not_applicable/missing
    source: Optional[str] = None      # api | csv | manual
    ref: Optional[str] = None         # operation_id / row_id, never a secret
    currency: Optional[str] = None

    def value(self) -> Decimal:
        if self.origin in (CONFIRMED_ZERO, NOT_APPLICABLE):
            return Decimal(0)
        return Decimal(self.amount or 0)


@dataclass(frozen=True)
class ReturnsStats:
    sold_units: int
    returned_units: int                       # cancellations NOT included by the caller
    cost_per_return: Optional[Decimal] = None  # proven reverse-logistics + non-recoverable value (+attrib)
    cost_components: Sequence[str] = ()        # provenance of cost_per_return
    cost_proven: bool = False
    window_days: int = DEFAULT_RETURN_WINDOW_DAYS
    min_sample: int = DEFAULT_MIN_RETURN_SAMPLE


@dataclass(frozen=True)
class Thresholds:
    emergency_abs: Decimal            # per-unit rubles
    target_margin_pct: Decimal        # percent of expected_sale_revenue
    emergency_pct: Optional[Decimal] = None   # optional advanced


@dataclass(frozen=True)
class ContributionInputs:
    marketplace: str
    store_id: str
    product_id: str
    thresholds: Thresholds
    promo_id: Optional[str] = None
    candidate_buyer_price: Optional[Decimal] = None   # the price used for THIS evaluation
    promo_price_proven: bool = False                  # is it a fresh promo price of a specific action?
    currency: Optional[str] = None
    cogs: Optional[CostLine] = None
    cost_lines: Sequence[CostLine] = ()               # every non-revenue, non-cogs cost
    commission_rate: Optional[Decimal] = None         # if set, commission = price * rate (avoids circularity)
    commission_official_tariff: bool = False          # rate/amount from a current official tariff?
    returns: Optional[ReturnsStats] = None
    include_ad_spend: bool = False
    ad_attributed: bool = True                        # meaningful only when include_ad_spend
    source_conflicts: Sequence[str] = ()              # metric names where API and CSV disagree
    stale_metrics: Sequence[str] = ()                 # metrics older than their allowed max age
    provider_capability_confirmed: bool = False       # confirmed marketplace exit capability
    evaluated_at: Optional[datetime] = None
    freshness_ages: Mapping[str, object] = field(default_factory=dict)


@dataclass
class ContributionResult:
    calculation_status: str
    economic_verdict: Optional[str]
    actionability: str
    projected_contribution: Optional[Decimal]
    contribution_pct: Optional[Decimal]
    missing_fields: list
    reasons: list
    evidence: dict


def compute(inp: ContributionInputs) -> ContributionResult:
    missing: list[str] = []
    reasons: list[str] = []

    # ── collect the cost lines keyed once, detecting a double origin (never a sum) ────────────
    by_key: dict[str, list[CostLine]] = {}
    all_lines = list(inp.cost_lines)
    if inp.cogs is not None:
        all_lines = [inp.cogs] + all_lines
    for ln in all_lines:
        by_key.setdefault(ln.cost_key, []).append(ln)

    conflicts: list[str] = list(inp.source_conflicts)
    # currency conflict: every stated currency must match the evaluation currency
    currencies = {c for c in [inp.currency] + [ln.currency for ln in all_lines] if c}
    if len(currencies) > 1:
        conflicts.append(f"currency_mismatch:{sorted(currencies)}")
    # a cost_key carried by more than one REAL origin (provider op AND manual) is a conflict
    for key, lines in by_key.items():
        real = [ln for ln in lines if ln.origin in _REAL_ORIGINS]
        if len(real) > 1:
            conflicts.append(f"duplicate_cost:{key}")

    # ── returns: Wilson upper on a sufficient sample; expected cost needs a PROVEN per-return cost
    ret = inp.returns
    expected_return_cost: Optional[Decimal] = None
    returns_model: dict = {}
    if ret is not None:
        returns_model = {
            "window_days": ret.window_days, "min_sample": ret.min_sample,
            "sold_units": ret.sold_units, "returned_units": ret.returned_units,
            "method": "wilson_upper", "confidence": "0.95", "z": str(WILSON_Z_95),
        }
        if ret.sold_units < ret.min_sample:
            missing.append("returns_history_insufficient")
        else:
            observed = (Decimal(ret.returned_units) / Decimal(ret.sold_units)) if ret.sold_units else None
            upper = wilson_upper(ret.returned_units, ret.sold_units)
            returns_model.update(observed_rate=str(_q(observed, _PCT)) if observed is not None else None,
                                 upper_rate=str(upper))
            if ret.cost_proven and ret.cost_per_return is not None:
                expected_return_cost = _q(upper * Decimal(ret.cost_per_return))
                returns_model.update(cost_per_return=str(_q(ret.cost_per_return)),
                                     cost_components=list(ret.cost_components))
            else:
                missing.append("return_cost_unconfirmed")
    else:
        missing.append("returns_history_insufficient")   # no history ≠ zero return risk

    # ── price ────────────────────────────────────────────────────────────────────────────────
    price = _q(inp.candidate_buyer_price)
    if price is None:
        missing.append("revenue_unknown")

    # ── every must-be-explicit cost absent or MISSING → incomplete (unknown expense is NOT 0) ──
    for key in _MUST_BE_EXPLICIT:
        lines = by_key.get(key)
        if not lines or all(ln.origin == MISSING for ln in lines):
            missing.append("no_cogs" if key == "cogs" else f"{key}_unconfirmed")
    # any other cost line explicitly flagged unknown (e.g. an unattributed store expense) → incomplete
    for ln in all_lines:
        if ln.origin == MISSING and ln.cost_key not in _MUST_BE_EXPLICIT:
            tag = f"{ln.cost_key}_unattributed"
            if tag not in missing:
                missing.append(tag)

    # advertising: only when the seller opted in; then it MUST be attributed
    if inp.include_ad_spend and not inp.ad_attributed:
        missing.append("ad_unattributed")

    # ── verdict priority: conflict > incomplete > stale > complete ─────────────────────────────
    if conflicts:
        status = CONFLICT
        reasons += [f"source_conflict:{c}" for c in conflicts]
    elif missing:
        status = INCOMPLETE
        reasons += [f"missing:{m}" for m in missing]
    elif inp.stale_metrics:
        status = STALE
        reasons += [f"stale:{m}" for m in inp.stale_metrics]
    else:
        status = COMPLETE

    # ── projected_contribution (only meaningful when complete) ─────────────────────────────────
    projected: Optional[Decimal] = None
    pct: Optional[Decimal] = None
    if status == COMPLETE:
        commission = None
        if inp.commission_rate is not None and price is not None:
            commission = _q(price * Decimal(inp.commission_rate))
        else:
            cl = by_key.get("commission")
            commission = cl[0].value() if cl else None
        total_costs = Decimal(0)
        for key, lines in by_key.items():
            if key == "commission" and commission is not None:
                total_costs += commission
                continue
            # advertising is only deducted when opted in
            if key == "advertising" and not inp.include_ad_spend:
                continue
            total_costs += sum((ln.value() for ln in lines), Decimal(0))
        if expected_return_cost is not None:
            total_costs += expected_return_cost
        projected = _q(price - total_costs)
        pct = _q((projected / price) * Decimal(100), _PCT) if price and price > 0 else None

    # ── economic_verdict — ONLY on a complete calculation, kept separate from calculation_status
    economic: Optional[str] = None
    if status == COMPLETE and projected is not None:
        th = inp.thresholds
        is_emergency = (projected <= _q(th.emergency_abs)) or (
            th.emergency_pct is not None and pct is not None and pct <= _q(th.emergency_pct, _PCT))
        if is_emergency:
            economic = EMERGENCY
        elif pct is not None and pct < _q(th.target_margin_pct, _PCT):
            economic = BELOW_TARGET
        else:
            economic = SAFE

    # ── actionability — computed separately, fail-closed ───────────────────────────────────────
    executable_preconditions = (
        status == COMPLETE
        and inp.promo_price_proven                 # a proven promo price of THIS action
        and inp.commission_official_tariff         # official current tariff, not a historical estimate
        and inp.provider_capability_confirmed
        and (ret is not None and ret.sold_units >= (ret.min_sample if ret else 0) and ret.cost_proven)
        and economic == EMERGENCY                  # only act on a proven emergency
    )
    if not inp.provider_capability_confirmed:
        actionability = UNSUPPORTED
    elif executable_preconditions:
        actionability = EXECUTABLE
    else:
        actionability = MANUAL_ONLY

    # ── evidence (validated fields returned separately; snapshot is the full audit) ────────────
    evidence = {
        "formula_version": FORMULA_VERSION,
        "evaluated_at": inp.evaluated_at.isoformat() if inp.evaluated_at else None,
        "identity": {"marketplace": inp.marketplace, "store_id": inp.store_id,
                     "product_id": inp.product_id, "promo_id": inp.promo_id},
        "candidate_buyer_price": str(price) if price is not None else None,
        "promo_price_proven": inp.promo_price_proven,
        "currency": inp.currency,
        "cost_lines": [
            {"cost_key": ln.cost_key, "origin": ln.origin,
             "amount": str(_q(ln.amount)) if ln.amount is not None else None,
             "source": ln.source, "ref": ln.ref, "currency": ln.currency}
            for ln in all_lines],
        "advertising_included": inp.include_ad_spend,
        "returns_model": returns_model,
        "source_policy_decision": {ln.cost_key: ln.source for ln in all_lines if ln.source},
        "freshness_ages": dict(inp.freshness_ages),
        "capability_snapshot": {
            "provider_capability_confirmed": inp.provider_capability_confirmed,
            "commission_official_tariff": inp.commission_official_tariff,
            "promo_price_proven": inp.promo_price_proven},
        "threshold_snapshot": {
            "emergency_abs": str(_q(inp.thresholds.emergency_abs)),
            "emergency_pct": str(_q(inp.thresholds.emergency_pct, _PCT)) if inp.thresholds.emergency_pct is not None else None,
            "target_margin_pct": str(_q(inp.thresholds.target_margin_pct, _PCT))},
        "rounding_mode": ROUNDING_MODE,
        "projected_contribution": str(projected) if projected is not None else None,
        "contribution_pct": str(pct) if pct is not None else None,
        "calculation_status": status,
        "economic_verdict": economic,
        "actionability": actionability,
    }

    return ContributionResult(
        calculation_status=status, economic_verdict=economic, actionability=actionability,
        projected_contribution=projected, contribution_pct=pct,
        missing_fields=missing, reasons=reasons, evidence=evidence)
