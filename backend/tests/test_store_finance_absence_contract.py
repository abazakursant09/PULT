"""Expand the store read contract without changing finance calculations."""
import pytest
from pydantic import ValidationError
from schemas.finance import StoreFinanceSummaryOut


def summary(**overrides):
    return dict(store_id="synthetic", revenue=100, net_profit=30, unassigned_revenue=0,
                source="csv", completeness="complete", missing_fields=[], conflict=False,
                conflict_candidates=None) | overrides


def test_explicit_absence_serializes_without_invented_zero_or_source():
    out = StoreFinanceSummaryOut(**summary(revenue=None, net_profit=None, source=None,
                                          completeness="no_data", missing_fields=["revenue"]))
    assert out.model_dump()["revenue"] is None
    assert out.source is None and out.net_profit is None


@pytest.mark.parametrize("overrides", [
    {"revenue": 0}, {"net_profit": 0}, {"source": "csv"},
    {"conflict": True}, {"conflict_candidates": {"api": 100, "csv": 0}},
    {"revenue": 0, "net_profit": 0, "source": "csv"},
])
def test_no_data_rejects_known_values_or_conflict(overrides):
    fields = summary(revenue=None, net_profit=None, source=None, completeness="no_data") | overrides
    with pytest.raises(ValidationError):
        StoreFinanceSummaryOut(**fields)


@pytest.mark.parametrize("field", ["source", "revenue"])
def test_absence_requires_explicit_no_data_state(field):
    with pytest.raises(ValidationError):
        StoreFinanceSummaryOut(**summary(**{field: None}))


@pytest.mark.parametrize("value", [0, -50, 100])
def test_existing_csv_numbers_including_real_zero_remain_valid(value):
    out = StoreFinanceSummaryOut(**summary(revenue=value))
    assert out.revenue == value and out.net_profit == 30 and out.source == "csv"


def test_known_api_revenue_with_unknown_profit_remains_valid():
    out = StoreFinanceSummaryOut(**summary(source="api", net_profit=None,
                                          completeness="incomplete", missing_fields=["cogs"]))
    assert out.revenue == 100 and out.net_profit is None


@pytest.mark.parametrize("value", [0, -50, 100])
def test_no_selected_source_data_preserves_independent_csv_reconciliation(value):
    out = StoreFinanceSummaryOut(**summary(revenue=None, net_profit=None, source=None,
        completeness="no_data", unassigned_revenue=value))
    assert out.unassigned_revenue == value
    assert out.revenue is None and out.net_profit is None and out.source is None
