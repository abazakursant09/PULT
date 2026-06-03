"""Characterization fixtures for logic.simulation (Sprint 72 — branch depth).
Observe-only. Reaches every rule_category scenario builder, every margin
pressure_source branch, marketplace constraint notes, uncertainty bands, SEO
rebuild-memory boost/penalty, and scenario_context_for_telegram branches.
Random scenario_id is normalized to "<volatile>" by the engine.
"""
from logic.simulation import generate_scenarios_for_insight, scenario_context_for_telegram
from characterization._engine import call, Duck

_REBUILD_WIN = {"P": Duck(winner=True, delta_ctr_percent=8.0)}
_REBUILD_FAIL = {"P": Duck(winner=False, delta_ctr_percent=-3.0)}


def _gen(key, cat, mp, conf, meta, rb=None, nc=None):
    return call(generate_scenarios_for_insight, key, cat, mp, conf, meta, rb, nc)


def build_cases():
    c = {}
    # high_ad_spend — marketplaces + uncertainty bands
    c["ad.wb"] = _gen("high_ad_spend:wildberries:A", "high_ad_spend", "wildberries", 70,
                     {"days_active": 20, "ad_ratio_pct": 34.0, "margin_pct": 4.0})
    c["ad.ozon"] = _gen("high_ad_spend:ozon:A", "high_ad_spend", "ozon", 70,
                       {"days_active": 20, "ad_ratio_pct": 34.0, "margin_pct": 4.0})
    c["ad.young_lt7"] = _gen("high_ad_spend:wildberries:A", "high_ad_spend", "wildberries", 70,
                            {"days_active": 5})
    c["ad.mid_lt14"] = _gen("high_ad_spend:wildberries:A", "high_ad_spend", "wildberries", 70,
                           {"days_active": 10})

    # margin_crisis — all four pressure sources
    for src in ("ad_driven", "logistics", "commission", "structural"):
        c[f"margin.{src}"] = _gen("margin_crisis:wildberries:A", "margin_crisis", "wildberries", 70,
                                 {"days_active": 20, "pressure_source": src, "margin_pct": 3.0})

    # seo_opportunity — no memory / positive rebuild / negative rebuild
    c["seo.no_memory"] = _gen("seo_opportunity:wildberries:A", "seo_opportunity", "wildberries", 70,
                             {"days_active": 20, "product_name": "P"})
    c["seo.rebuild_win"] = _gen("seo_opportunity:wildberries:A", "seo_opportunity", "wildberries", 70,
                               {"days_active": 20, "product_name": "P"}, _REBUILD_WIN)
    c["seo.rebuild_fail"] = _gen("seo_opportunity:wildberries:A", "seo_opportunity", "wildberries", 70,
                                {"days_active": 20, "product_name": "P"}, _REBUILD_FAIL)

    # low_stock — marketplaces
    c["stock.wb"] = _gen("low_stock:wildberries:A", "low_stock", "wildberries", 70,
                        {"days_active": 20, "stock": 5, "days_left": 3})
    c["stock.ym"] = _gen("low_stock:yandex_market:A", "low_stock", "yandex_market", 70,
                        {"days_active": 20, "stock": 5, "days_left": 3})

    # sales_growth — with/without low stock + history bonus via notif_counts
    c["growth.plain"] = _gen("sales_growth:wildberries:A", "sales_growth", "wildberries", 70,
                            {"days_active": 20, "growth_pct": 25})
    c["growth.low_stock"] = _gen("sales_growth:wildberries:A", "sales_growth", "wildberries", 70,
                                {"days_active": 20, "growth_pct": 25, "has_low_stock": True})
    c["growth.history3x"] = _gen("sales_growth:wildberries:A", "sales_growth", "wildberries", 70,
                                {"days_active": 20, "growth_pct": 25}, None,
                                {"sales_growth:wildberries:A": 3})

    # unknown category -> []
    c["unknown_category"] = _gen("x:wildberries:A", "unknown", "wildberries", 70, {"days_active": 20})

    # scenario_context_for_telegram — category × past_cnt × marketplace × rebuild
    c["ctx.ad_history"] = call(scenario_context_for_telegram, "high_ad_spend", "wildberries", 2)
    c["ctx.ad_mp_note"] = call(scenario_context_for_telegram, "high_ad_spend", "wildberries", 0)
    c["ctx.margin_history"] = call(scenario_context_for_telegram, "margin_crisis", "wildberries", 2)
    c["ctx.growth_ozon"] = call(scenario_context_for_telegram, "sales_growth", "ozon", 0)
    c["ctx.growth_history"] = call(scenario_context_for_telegram, "sales_growth", "wildberries", 2)
    c["ctx.seo_rebuild"] = call(scenario_context_for_telegram, "seo_opportunity", "wildberries", 0,
                               Duck(winner=True, delta_ctr_percent=6.0))
    c["ctx.stock_mp"] = call(scenario_context_for_telegram, "low_stock", "yandex_market", 0)
    c["ctx.empty"] = call(scenario_context_for_telegram, "high_rating", "wildberries", 0)
    # ozon marketplace-note branches + past_cnt>=2 uncertainty band
    c["margin.structural_ozon"] = _gen("margin_crisis:ozon:A", "margin_crisis", "ozon", 70,
                                       {"days_active": 20, "pressure_source": "structural", "margin_pct": 3.0})
    c["seo.ozon"] = _gen("seo_opportunity:ozon:A", "seo_opportunity", "ozon", 70,
                        {"days_active": 20, "product_name": "P"})
    c["growth.ozon"] = _gen("sales_growth:ozon:A", "sales_growth", "ozon", 70,
                           {"days_active": 20, "growth_pct": 25})
    c["ad.history2x"] = _gen("high_ad_spend:wildberries:A", "high_ad_spend", "wildberries", 70,
                            {"days_active": 20}, None, {"high_ad_spend:wildberries:A": 2})
    c["ad.ym"] = _gen("high_ad_spend:yandex_market:A", "high_ad_spend", "yandex_market", 70,
                     {"days_active": 20})
    c["growth.ym_aggr"] = _gen("sales_growth:yandex_market:A", "sales_growth", "yandex_market", 70,
                              {"days_active": 20, "growth_pct": 25})
    return c
