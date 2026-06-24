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

def test_ozon_not_implemented(monkeypatch):
    monkeypatch.setattr(ci, "wb_client", _ExplodingWB())
    r = _run(resolve_campaign_identity("ozon", sku="12345", token="t"))
    assert isinstance(r, CampaignUnavailable)
    assert r.marketplace == "ozon" and r.reason == NOT_IMPLEMENTED


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
