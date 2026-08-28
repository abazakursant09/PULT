import pytest
from fastapi import HTTPException

from config import Settings
from routers import payments


def test_billing_flag_defaults_off():
    assert Settings().billing_enabled is False


def test_billing_gate_is_neutral_404_when_off(monkeypatch):
    monkeypatch.setattr(payments.settings, "billing_enabled", False)
    with pytest.raises(HTTPException) as exc:
        payments._require_billing_enabled()
    assert exc.value.status_code == 404
    assert exc.value.detail == "Not found"


def test_billing_gate_passes_only_when_explicitly_on(monkeypatch):
    monkeypatch.setattr(payments.settings, "billing_enabled", True)
    assert payments._require_billing_enabled() is None


def test_every_payment_endpoint_carries_the_gate():
    assert payments.router.routes
    for route in payments.router.routes:
        calls = {dependency.call for dependency in route.dependant.dependencies}
        assert payments._require_billing_enabled in calls, route.path
