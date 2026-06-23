"""
Action → Metric binding tests (Decision Outcome enabler).

Honesty gates: no binding may reference a non-existent action_key or a
non-existent canonical metric, and every currently-known action must be bound
(no silent unmapped action that would later be unmeasurable without notice).
"""
from services.marketplace import action_catalog, metric_catalog, action_metric_binding


def test_every_bound_action_key_is_real():
    actions = set(action_catalog.known_actions())
    for key in action_metric_binding.known_bindings():
        assert key in actions, f"binding references unknown action_key: {key}"


def test_every_bound_metric_is_real():
    metrics = set(metric_catalog.known_metrics())
    for key, metric in action_metric_binding.known_bindings().items():
        assert metric in metrics, f"{key} → dangling metric_name {metric}"


def test_all_known_actions_are_bound():
    # Full coverage: every executor action has a declared target metric.
    actions = set(action_catalog.known_actions())
    bound = set(action_metric_binding.known_bindings())
    missing = actions - bound
    assert not missing, f"actions with no metric binding: {sorted(missing)}"


def test_target_metric_lookup():
    assert action_metric_binding.target_metric("set_price") == "revenue"
    assert action_metric_binding.target_metric("update_card") == "ctr"
    assert action_metric_binding.target_metric("publish_review_response") == "rating"
    assert action_metric_binding.target_metric("ad_set_bid") == "ad_cost_ratio"


def test_reduce_discount_bound_to_net_profit():
    # A2 margin alternative — measured on profit, not revenue (action_catalog doctrine).
    assert action_metric_binding.target_metric("reduce_discount") == "net_profit"


def test_stop_auto_promotion_bound_to_net_profit():
    # A3 margin alternative — measured on profit, not ad spend alone.
    assert action_metric_binding.target_metric("stop_auto_promotion") == "net_profit"


def test_margin_actions_measure_honestly_intent_only():
    # Binding declares INTENT: the metric must be real (closes honestly), but the
    # binding never asserts the reader is currently available — uncloseable bindings
    # stay measurable-by-catalog and resolve to not_evaluated downstream, never faked.
    metrics = set(metric_catalog.known_metrics())
    for action in ("reduce_discount", "stop_auto_promotion"):
        metric = action_metric_binding.target_metric(action)
        assert metric in metrics                                     # real metric, not invented
        assert action_metric_binding.is_measurable(action) is True   # bound + real (intent), not a readability promise


def test_target_metric_unknown_returns_none():
    assert action_metric_binding.target_metric("does_not_exist") is None
    assert action_metric_binding.target_metric(None) is None
    assert action_metric_binding.target_metric("") is None


def test_is_measurable():
    assert action_metric_binding.is_measurable("set_price") is True
    assert action_metric_binding.is_measurable("unknown_action") is False
    assert action_metric_binding.is_measurable(None) is False
