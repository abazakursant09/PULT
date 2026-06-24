"""
A2.2-pre-a — campaign identity resolver (read seam, no integration).

Marketplace-isolated resolve: listing/sku/nmID → real observed campaign identity
(WB only today) or an HONEST unavailable reason. No guessed/inferred campaign,
never auto-picks among multiple, and non-WB marketplaces never touch the WB client.
"""
import asyncio

import pytest

from services.marketplace import campaign_identity as ci
from services.marketplace.campaign_identity import (
    resolve_campaign_identity, CampaignIdentity, CampaignUnavailable,
    NO_ADAPTER, NOT_IMPLEMENTED, NO_SCOPE, NO_CAMPAIGN_FOR_LISTING,
    AMBIGUOUS_MULTIPLE, INVALID_LISTING_IDENTITY,
)


def _run(c):
    return asyncio.run(c)


class _FakeWB:
    """Records calls; returns a scripted advert list."""
    def __init__(self, adverts):
        self._adverts = adverts
        self.calls = 0

    async def list_adverts_for_nm(self, *, token, nm_id):
        self.calls += 1
        self.last = {"token": token, "nm_id": nm_id}
        return list(self._adverts)


class _ExplodingWB:
    """Any call is a marketplace-isolation violation."""
    async def list_adverts_for_nm(self, *, token, nm_id):
        raise AssertionError("non-WB resolve must NOT call the WB client")


@pytest.fixture
def fake_wb(monkeypatch):
    def _install(adverts):
        fake = _FakeWB(adverts)
        monkeypatch.setattr(ci, "wb_client", fake)
        return fake
    return _install


# ── WB success: exactly one campaign ─────────────────────────────────────────

def test_wb_single_campaign_returns_identity(fake_wb):
    fake_wb([{"campaign_id": 777, "campaign_type": 8, "campaign_state": 9}])
    r = _run(resolve_campaign_identity("wb", sku="12345", token="t"))
    assert isinstance(r, CampaignIdentity)
    assert r.marketplace == "wb"
    assert r.campaign_id == 777 and r.campaign_type == 8 and r.campaign_state == 9
    assert r.source == "wb_advert_api"


def test_wb_uses_explicit_nm_id(fake_wb):
    fake = fake_wb([{"campaign_id": 1, "campaign_type": None, "campaign_state": None}])
    r = _run(resolve_campaign_identity("wb", nm_id=999, token="t"))
    assert isinstance(r, CampaignIdentity)
    assert fake.last["nm_id"] == 999          # nm identity passed through, not fabricated
    assert r.campaign_type is None            # missing fields stay None, never guessed


# ── WB zero campaigns ────────────────────────────────────────────────────────

def test_wb_zero_campaigns_unavailable(fake_wb):
    fake_wb([])
    r = _run(resolve_campaign_identity("wb", sku="12345", token="t"))
    assert isinstance(r, CampaignUnavailable) and r.reason == NO_CAMPAIGN_FOR_LISTING


# ── WB multiple campaigns → never auto-pick ──────────────────────────────────

def test_wb_multiple_campaigns_ambiguous(fake_wb):
    fake_wb([{"campaign_id": 1}, {"campaign_id": 2}])
    r = _run(resolve_campaign_identity("wb", sku="12345", token="t"))
    assert isinstance(r, CampaignUnavailable) and r.reason == AMBIGUOUS_MULTIPLE


# ── WB scope / identity guards ───────────────────────────────────────────────

def test_wb_no_token_no_scope(fake_wb):
    fake = fake_wb([{"campaign_id": 1}])
    r = _run(resolve_campaign_identity("wb", sku="12345", token=None))
    assert isinstance(r, CampaignUnavailable) and r.reason == NO_SCOPE
    assert fake.calls == 0                     # never reached the adapter without scope


def test_wb_invalid_listing_identity(fake_wb):
    fake = fake_wb([{"campaign_id": 1}])
    r = _run(resolve_campaign_identity("wb", sku="not-a-number", token="t"))
    assert isinstance(r, CampaignUnavailable) and r.reason == INVALID_LISTING_IDENTITY
    assert fake.calls == 0


# ── non-WB marketplaces: honest reasons, no WB call ──────────────────────────

def test_ozon_without_connection_no_scope(monkeypatch):
    # No db/connection_id → cannot resolve the advert_performance credential.
    monkeypatch.setattr(ci, "wb_client", _ExplodingWB())
    r = _run(resolve_campaign_identity("ozon", sku="12345", token="t"))
    assert isinstance(r, CampaignUnavailable)
    assert r.marketplace == "ozon" and r.reason == NO_SCOPE


def test_yandex_no_adapter(monkeypatch):
    monkeypatch.setattr(ci, "wb_client", _ExplodingWB())
    r = _run(resolve_campaign_identity("yandex", sku="12345", token="t"))
    assert isinstance(r, CampaignUnavailable)
    assert r.marketplace == "yandex" and r.reason == NO_ADAPTER


def test_megamarket_no_adapter(monkeypatch):
    monkeypatch.setattr(ci, "wb_client", _ExplodingWB())
    r = _run(resolve_campaign_identity("megamarket", sku="12345", token="t"))
    assert isinstance(r, CampaignUnavailable)
    assert r.marketplace == "megamarket" and r.reason == NO_ADAPTER


def test_unknown_marketplace_no_adapter(monkeypatch):
    monkeypatch.setattr(ci, "wb_client", _ExplodingWB())
    r = _run(resolve_campaign_identity("aliexpress", sku="12345", token="t"))
    assert isinstance(r, CampaignUnavailable) and r.reason == NO_ADAPTER


# ── marketplace isolation (explicit) ─────────────────────────────────────────

def test_non_wb_never_calls_wb_client(monkeypatch):
    monkeypatch.setattr(ci, "wb_client", _ExplodingWB())
    for mp in ("ozon", "yandex", "megamarket", "unknown_mp"):
        r = _run(resolve_campaign_identity(mp, sku="12345", token="t"))
        assert isinstance(r, CampaignUnavailable)   # _ExplodingWB would have raised on any call


# ── Ozon real read (Performance bearer) ──────────────────────────────────────

class _FakeOzon:
    """Records calls; returns scripted normalized campaigns."""
    def __init__(self, campaigns):
        self._c = campaigns
        self.calls = 0
    async def list_campaigns_for_sku(self, *, token, sku=None):
        self.calls += 1
        self.last = {"token": token, "sku": sku}
        return list(self._c)


class _ExplodingOzon:
    async def list_campaigns_for_sku(self, *, token, sku=None):
        raise AssertionError("WB resolve must NOT call the Ozon client")


def _install_ozon(monkeypatch, campaigns):
    fake = _FakeOzon(campaigns)
    monkeypatch.setattr(ci, "ozon_client", fake)
    async def fake_bearer(_db, *, connection_id, **kw):
        return "BEARER"
    monkeypatch.setattr(ci.ozon_performance_auth, "acquire_bearer", fake_bearer)
    return fake


def _cmp(cid, sku, *, type=None, state=None, rel=True):
    d = {"campaign_id": cid, "campaign_type": type, "campaign_state": state,
         "relation_present": rel}
    if rel:
        d["sku"] = sku
    return d


def test_ozon_single_matching_campaign(monkeypatch):
    fake = _install_ozon(monkeypatch, [_cmp(101, "SKU-1", type="SKU", state="running"),
                                       _cmp(102, "SKU-9")])
    r = _run(resolve_campaign_identity("ozon", sku="SKU-1", db=object(), connection_id="c"))
    assert isinstance(r, CampaignIdentity)
    assert r.marketplace == "ozon" and r.campaign_id == 101
    assert r.campaign_type == "SKU" and r.campaign_state == "running"
    assert r.source == "ozon_performance_api"
    assert fake.last["token"] == "BEARER"        # used the Performance bearer, not a raw token


def test_ozon_zero_matching_no_campaign(monkeypatch):
    _install_ozon(monkeypatch, [_cmp(102, "SKU-9"), _cmp(103, "SKU-8")])
    r = _run(resolve_campaign_identity("ozon", sku="SKU-1", db=object(), connection_id="c"))
    assert isinstance(r, CampaignUnavailable) and r.reason == NO_CAMPAIGN_FOR_LISTING


def test_ozon_multiple_matching_ambiguous(monkeypatch):
    _install_ozon(monkeypatch, [_cmp(201, "SKU-1"), _cmp(202, "SKU-1")])
    r = _run(resolve_campaign_identity("ozon", sku="SKU-1", db=object(), connection_id="c"))
    assert isinstance(r, CampaignUnavailable) and r.reason == AMBIGUOUS_MULTIPLE


def test_ozon_relation_unavailable_not_implemented(monkeypatch):
    # campaigns observed but none carry a listing relation → no guess, honest unavailable
    _install_ozon(monkeypatch, [_cmp(301, None, rel=False), _cmp(302, None, rel=False)])
    r = _run(resolve_campaign_identity("ozon", sku="SKU-1", db=object(), connection_id="c"))
    assert isinstance(r, CampaignUnavailable) and r.reason == NOT_IMPLEMENTED


def test_ozon_missing_credential_no_scope(monkeypatch):
    from services.marketplace.errors import ExecutionError
    monkeypatch.setattr(ci, "ozon_client", _ExplodingOzon())   # must not be reached
    async def boom_bearer(_db, *, connection_id, **kw):
        raise ExecutionError(ExecutionError.MISSING_SCOPE, "no advert_performance credential")
    monkeypatch.setattr(ci.ozon_performance_auth, "acquire_bearer", boom_bearer)
    r = _run(resolve_campaign_identity("ozon", sku="SKU-1", db=object(), connection_id="c"))
    assert isinstance(r, CampaignUnavailable) and r.reason == NO_SCOPE


def test_ozon_no_sku_invalid_identity(monkeypatch):
    _install_ozon(monkeypatch, [_cmp(101, "SKU-1")])
    r = _run(resolve_campaign_identity("ozon", db=object(), connection_id="c"))  # no sku
    assert isinstance(r, CampaignUnavailable) and r.reason == INVALID_LISTING_IDENTITY


# ── isolation both ways: WB resolve never calls the Ozon client ──────────────

def test_wb_never_calls_ozon_client(monkeypatch):
    monkeypatch.setattr(ci, "ozon_client", _ExplodingOzon())
    monkeypatch.setattr(ci, "wb_client", _FakeWB([{"campaign_id": 5}]))
    r = _run(resolve_campaign_identity("wb", sku="12345", token="t"))
    assert isinstance(r, CampaignIdentity) and r.marketplace == "wb"


def test_ozon_resolve_never_calls_wb_client(monkeypatch):
    monkeypatch.setattr(ci, "wb_client", _ExplodingWB())
    _install_ozon(monkeypatch, [_cmp(101, "SKU-1")])
    r = _run(resolve_campaign_identity("ozon", sku="SKU-1", db=object(), connection_id="c"))
    assert isinstance(r, CampaignIdentity) and r.marketplace == "ozon"


# ── Ozon adapter request shape + normalization (real method) ─────────────────

def test_ozon_adapter_request_shape_and_normalization(monkeypatch):
    from services.marketplace.ozon_client import ozon_client
    cap = []
    async def fake_request(method, path, *, token, auth_header):
        cap.append((method, path, token, auth_header))
        return {"list": [{"id": 101, "advObjectType": "SKU", "state": "running", "sku": "SKU-1"},
                         {"campaignId": 102, "type": "SEARCH", "status": "stopped"}]}
    monkeypatch.setattr(ozon_client._performance(), "request", fake_request)
    rows = _run(ozon_client.list_campaigns_for_sku(token="B", sku="SKU-1"))
    assert cap == [("GET", "/api/client/campaign", "Bearer B", "Authorization")]
    assert rows[0] == {"campaign_id": 101, "campaign_type": "SKU", "campaign_state": "running",
                       "sku": "SKU-1", "relation_present": True}
    # second campaign carries no relation field → relation_present False, sku None (never guessed)
    assert rows[1]["campaign_id"] == 102 and rows[1]["relation_present"] is False
    assert rows[1]["sku"] is None


# ── (9) no schema change: resolver/adapter add no model/table ─────────────────

def test_no_new_schema():
    # campaign identity is a pure read seam — no ORM model is defined by it.
    import services.marketplace.campaign_identity as mod
    from database import Base
    assert not any(getattr(v, "__tablename__", None) and isinstance(v, type)
                   and issubclass(v, Base) for v in vars(mod).values())
