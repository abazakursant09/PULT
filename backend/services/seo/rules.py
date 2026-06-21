"""
SEO Rule Catalog — engine implementation (A4).

12 deterministic `static_card` rules over a canonical CardSnapshot. Pure: no DB,
no API, no signal building, no marketplace-specific logic, no external data.
Every rule:
  1. declares `required_fields` (field_availability keys it needs);
  2. is NOT_EVALUATED (with reason) when any required field is unavailable —
     never silently "passed";
  3. when evaluable, is TRIGGERED (with snapshot-derived evidence) or NOT_TRIGGERED.

Evidence values come ONLY from the snapshot. No raw marketplace payloads exist in
CardSnapshot, so evidence cannot leak MP specifics. keyword_cannibalization is
deliberately excluded (cross-card analysis, needs own_catalog).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Tuple, Union

from .card_snapshot import CardSnapshot
from .evaluation import RuleEvaluation, RuleResult

# Bumped whenever the rule set / thresholds semantics change — stamped on every
# seo_audit for cross-run determinism.
RULE_CATALOG_VERSION = "1"

# Predicate outcome (rule already passed the availability gate):
#   ("triggered", evidence_dict) | ("not_triggered", None) | ("not_evaluated", reason)
_Pred = Tuple[str, Union[dict, str, None]]


@dataclass(frozen=True)
class Rule:
    problem_type: str
    category: str
    severity: str
    estimated_effect_type: str
    detectability: str
    required_fields: Tuple[str, ...]
    predicate: Callable[[CardSnapshot], _Pred]

    def evaluate(self, snap: CardSnapshot) -> RuleEvaluation:
        missing = [f for f in self.required_fields if not snap.field_availability.get(f)]
        if missing:
            return self._mk(RuleResult.NOT_EVALUATED, reason=f"missing_fields: {','.join(missing)}")
        kind, payload = self.predicate(snap)
        if kind == "triggered":
            return self._mk(RuleResult.TRIGGERED, evidence=dict(payload))   # type: ignore[arg-type]
        if kind == "not_evaluated":
            return self._mk(RuleResult.NOT_EVALUATED, reason=str(payload))
        return self._mk(RuleResult.NOT_TRIGGERED)

    def _mk(self, result, *, evidence=None, reason=None) -> RuleEvaluation:
        return RuleEvaluation(
            problem_type=self.problem_type, category=self.category, severity=self.severity,
            estimated_effect_type=self.estimated_effect_type, detectability=self.detectability,
            result=result, evidence=evidence, reason=reason,
        )


# ── helpers (snapshot-only) ──────────────────────────────────────────────────

def _filled_keys(snap: CardSnapshot) -> set:
    return {a.key for a in snap.attributes if a.is_filled}


# ── predicates ───────────────────────────────────────────────────────────────

def _p_required_attributes_missing(s: CardSnapshot) -> _Pred:
    filled = _filled_keys(s)
    missing = [k for k in s.category_schema.required_attributes if k not in filled]
    if missing:
        return "triggered", {"missing_required_attributes": missing,
                             "required_count": len(s.category_schema.required_attributes),
                             "filled_count": len(filled)}
    return "not_triggered", None


def _p_wrong_category_placement(s: CardSnapshot) -> _Pred:
    if s.expected_category_path is None:
        return "not_evaluated", "no_expected_category_path"
    if tuple(s.expected_category_path) != tuple(s.category_path):
        return "triggered", {"category_path": list(s.category_path),
                             "expected_category_path": list(s.expected_category_path)}
    return "not_triggered", None


def _p_title_too_short(s: CardSnapshot) -> _Pred:
    if s.title is None:
        return "not_evaluated", "no_title"
    n = len(s.title)
    if n < s.constraints.title_min_len:
        return "triggered", {"title_length": n, "title_min_len": s.constraints.title_min_len}
    return "not_triggered", None


def _p_title_too_long(s: CardSnapshot) -> _Pred:
    if s.title is None:
        return "not_evaluated", "no_title"
    n = len(s.title)
    if n > s.constraints.title_max_len:
        return "triggered", {"title_length": n, "title_max_len": s.constraints.title_max_len}
    return "not_triggered", None


def _p_attributes_incomplete(s: CardSnapshot) -> _Pred:
    total = len(s.attributes)
    if total == 0:
        return "not_evaluated", "no_attributes_to_rate"
    filled = sum(1 for a in s.attributes if a.is_filled)
    rate = round(filled / total, 4)
    if rate < s.constraints.attribute_fill_rate_threshold:
        return "triggered", {"attribute_fill_rate": rate,
                             "attribute_fill_rate_threshold": s.constraints.attribute_fill_rate_threshold,
                             "filled_count": filled, "total_count": total}
    return "not_triggered", None


def _p_filter_attributes_missing(s: CardSnapshot) -> _Pred:
    filled = _filled_keys(s)
    missing = [k for k in s.category_schema.filterable_attributes if k not in filled]
    if missing:
        return "triggered", {"missing_filter_attributes": missing}
    return "not_triggered", None


def _p_variant_attributes_missing(s: CardSnapshot) -> _Pred:
    present = set(s.variants)
    missing = [k for k in s.category_schema.variant_attributes if k not in present]
    if s.category_schema.variant_attributes and missing:
        return "triggered", {"missing_variant_attributes": missing}
    return "not_triggered", None


def _p_attribute_values_invalid(s: CardSnapshot) -> _Pred:
    invalid = [a.key for a in s.attributes if a.is_filled and not a.is_valid_format]
    if invalid:
        return "triggered", {"invalid_attributes": invalid}
    return "not_triggered", None


def _p_description_missing(s: CardSnapshot) -> _Pred:
    if not (s.description or "").strip():
        return "triggered", {"description_present": False}
    return "not_triggered", None


def _p_description_too_short(s: CardSnapshot) -> _Pred:
    text = (s.description or "").strip()
    if not text:
        return "not_triggered", None   # absence handled by description_missing
    n = len(text)
    if n < s.constraints.description_min_len:
        return "triggered", {"description_length": n,
                             "description_min_len": s.constraints.description_min_len}
    return "not_triggered", None


def _p_content_completeness_low(s: CardSnapshot) -> _Pred:
    total = len(s.attributes)
    attr_rate = (sum(1 for a in s.attributes if a.is_filled) / total) if total else 0.0
    components = [
        1.0 if (s.title or "").strip() else 0.0,
        1.0 if (s.description or "").strip() else 0.0,
        attr_rate,
        1.0 if s.media.image_count >= s.constraints.media_min_images else 0.0,
    ]
    completeness = round(sum(components) / len(components), 4)
    if completeness < s.constraints.content_completeness_threshold:
        return "triggered", {"content_completeness": completeness,
                             "content_completeness_threshold": s.constraints.content_completeness_threshold}
    return "not_triggered", None


def _p_media_below_minimum(s: CardSnapshot) -> _Pred:
    if s.media.image_count < s.constraints.media_min_images:
        return "triggered", {"image_count": s.media.image_count,
                             "media_min_images": s.constraints.media_min_images}
    return "not_triggered", None


# ── closed, stable-ordered registry ──────────────────────────────────────────

RULE_REGISTRY: Tuple[Rule, ...] = (
    Rule("required_attributes_missing", "Attributes", "critical", "filter_exclusion",
         "static_card", ("category_schema", "attributes"), _p_required_attributes_missing),
    Rule("wrong_category_placement", "Discoverability", "critical", "filter_exclusion",
         "static_card", ("category_path", "expected_category_path"), _p_wrong_category_placement),
    Rule("title_too_short", "Title", "high", "discoverability_loss",
         "static_card", ("title", "constraints"), _p_title_too_short),
    Rule("title_too_long", "Title", "medium", "ranking_loss",
         "static_card", ("title", "constraints"), _p_title_too_long),
    Rule("attributes_incomplete", "Attributes", "high", "ranking_loss",
         "static_card", ("attributes", "constraints"), _p_attributes_incomplete),
    Rule("filter_attributes_missing", "Attributes", "high", "filter_exclusion",
         "static_card", ("category_schema", "attributes"), _p_filter_attributes_missing),
    Rule("variant_attributes_missing", "Attributes", "high", "discoverability_loss",
         "static_card", ("category_schema", "variants"), _p_variant_attributes_missing),
    Rule("attribute_values_invalid", "Attributes", "medium", "filter_exclusion",
         "static_card", ("attributes",), _p_attribute_values_invalid),
    Rule("description_missing", "Description", "high", "indexing_gap",
         "static_card", ("description",), _p_description_missing),
    Rule("description_too_short", "Description", "medium", "ranking_loss",
         "static_card", ("description", "constraints"), _p_description_too_short),
    Rule("content_completeness_low", "Content Quality", "high", "ranking_loss",
         "static_card", ("title", "description", "attributes", "media", "constraints"),
         _p_content_completeness_low),
    Rule("media_below_minimum", "Content Quality", "high", "conversion_loss",
         "static_card", ("media", "constraints"), _p_media_below_minimum),
)
