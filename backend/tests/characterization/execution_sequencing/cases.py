"""Characterization fixtures for logic.execution_sequencing (Sprint 72 — branch depth).
Observe-only. Reaches: type-config sequencing, dynamic stage elevation
(margin_crisis w/ high_ad_spend; seo w/ stock/margin), paralysis compression,
recurring-confidence bump, role_label known/unknown, summary-line gated output.
"""
from logic.execution_sequencing import (
    build_execution_sequence, role_label, sequencing_summary_line,
)
from characterization._engine import call, insight, jsonable


def _warn(key, title="t", **kw):
    return insight(key=key, type="warning", title=title, product_name="P", **kw)


def build_cases():
    c = {}
    # no active warnings -> []
    c["empty"] = call(build_execution_sequence, [], [], None)
    c["no_warnings"] = call(build_execution_sequence,
                           [insight(key="sales_growth:wildberries:A", type="info")], [], None)
    # mixed portfolio -> stage adjustments (margin->2 via ad; seo->2 via margin/stock)
    mixed = [
        _warn("high_ad_spend:wildberries:A"),
        _warn("margin_crisis:wildberries:B"),
        _warn("seo_opportunity:wildberries:C"),
        _warn("low_stock:wildberries:D"),
        _warn("high_rating:wildberries:E"),
        _warn("sales_growth:wildberries:F"),   # excluded
    ]
    c["mixed"] = call(build_execution_sequence, mixed, [], None)
    # recurring confidence bump
    c["recurring"] = call(build_execution_sequence, [
        _warn("high_ad_spend:wildberries:A", signal_lifecycle_stage="recurring"),
        _warn("margin_crisis:wildberries:B", signal_lifecycle_stage="recurring"),
    ], [], None)
    # paralysis: 3+ high-friction (margin_crisis) + fatigue > 0.6
    c["paralysis"] = call(build_execution_sequence, [
        _warn("margin_crisis:wildberries:A"),
        _warn("margin_crisis:wildberries:B"),
        _warn("margin_crisis:wildberries:C"),
    ], [], None, fatigue_score=0.7)

    # role_label: all known roles + unknown
    c["role_label"] = [role_label(r) for r in
                       ("fast_stabilization", "structural_fix", "parallel_track", "isolated", "???")]

    # sequencing_summary_line: gated (>=2 with stage1-unlock + stage2) vs None
    seq = build_execution_sequence([
        _warn("high_ad_spend:wildberries:A"),
        _warn("margin_crisis:wildberries:B"),
    ], [], None)
    c["summary_line.present"] = call(sequencing_summary_line, seq)
    c["summary_line.too_short"] = call(sequencing_summary_line, [])
    # two stage-1 actions, no stage-2 -> summary returns None (no chain)
    seq_nochain = build_execution_sequence([
        _warn("high_ad_spend:wildberries:A"), _warn("low_stock:wildberries:B"),
    ], [], None)
    c["summary_line.no_chain"] = call(sequencing_summary_line, seq_nochain)
    # category absent from _TYPE_CONFIG -> skipped -> []
    c["cat_not_in_config"] = call(build_execution_sequence,
                                 [_warn("unknown_type:wildberries:A")], [], None)
    return c
