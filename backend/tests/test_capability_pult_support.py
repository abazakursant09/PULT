"""
L1.2 — marketplace API capability vs actual PULT support.

The capability registry must not conflate "the marketplace exposes this through its API" with
"PULT has built the integration". availability() now requires BOTH: the marketplace verdict allows
it AND pult_supported is true. The marketplace-API fact stays separately visible (marketplace_api /
verdict) so an honest UI can say "маркетплейс поддерживает, PULT — пока нет".

Ground truth for reviews: only Wildberries has a PULT provider/executor path; Ozon and Yandex have
the marketplace API but no PULT integration.
"""
from services import capability_registry as cap


# ── required truth: review_reply availability reflects PULT reality ──────────

def test_review_reply_wildberries_available():
    a = cap.availability("review_reply", "wildberries")
    assert a["available"] is True
    assert a["marketplace_api"] is True
    assert a["pult_supported"] is True


def test_review_reply_ozon_available_for_premium_only():
    # R-OZ3: Ozon review reply is now PULT-supported, but the marketplace gates it behind the
    # seller's premium_plus tariff. Available only when the seller holds that tariff.
    ok = cap.availability("review_reply", "ozon", tariffs={"premium_plus"})
    assert ok["available"] is True
    assert ok["marketplace_api"] is True and ok["pult_supported"] is True
    # a non-premium seller is blocked by the marketplace tariff, not by PULT support
    blocked = cap.availability("review_reply", "ozon")           # no tariffs
    assert blocked["available"] is False
    assert blocked["status"] == "tariff" and blocked["pult_supported"] is True


def test_review_reply_yandex_market_not_available():
    a = cap.availability("review_reply", "yandex_market")
    assert a["available"] is False
    assert a["marketplace_api"] is True
    assert a["pult_supported"] is False
    assert a["status"] == "pult"


# ── the same separation holds for review sync ────────────────────────────────

def test_review_sync_support_matches_reply():
    assert cap.availability("reviews", "wildberries")["available"] is True
    # R-OZ3: Ozon sync PULT-supported; available for a premium_plus seller, tariff-blocked otherwise.
    assert cap.availability("reviews", "ozon", tariffs={"premium_plus"})["available"] is True
    assert cap.availability("reviews", "ozon")["available"] is False        # no tariff
    assert cap.availability("reviews", "yandex_market")["available"] is False


# ── the marketplace-API fact is NOT erased — it stays queryable ───────────────

def test_marketplace_api_fact_preserved_separately():
    # verdict() still reports the raw marketplace-API truth for Ozon/Yandex reviews.
    assert cap.verdict("review_reply", "ozon") == "api"
    assert cap.verdict("review_reply", "yandex_market") == "api"
    # and availability keeps it visible even while marking it unavailable for PULT
    a = cap.availability("review_reply", "yandex_market")
    assert a["verdict"] == "api" and a["marketplace_api"] is True


# ── regression: capabilities PULT already ships stay available ───────────────

def test_pult_supported_defaults_true_for_untagged_capabilities():
    # campaign_control (wb) has no pult_supported flag → defaults True → still available,
    # so the new gate does not silently break every other capability.
    a = cap.availability("campaign_control", "wildberries")
    assert a["available"] is True
    assert a["pult_supported"] is True


def test_canonical_and_short_marketplace_names_agree():
    assert cap.availability("review_reply", "wb")["available"] \
        == cap.availability("review_reply", "wildberries")["available"]
    assert cap.availability("review_reply", "yandex")["available"] \
        == cap.availability("review_reply", "yandex_market")["available"]
