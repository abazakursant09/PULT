"""
Capability registry loader — источник истины §6.1 (источниковость полей) и
§19 (честность возможностей).

Отвечает на вопрос: доступен ли пункт доктрины для (маркетплейс, тариф) и почему
нет. UI обязан показывать причину недоступности ("недоступно из-за API/тарифа/
нет данных/нет интеграции"), а НЕ пустой блок и НЕ имитацию данных.

Pure + кэш: JSON читается один раз. Никакого доступа к БД.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "data" / "capability_registry.json"

# Тарифные гейты: какой флаг тарифа селлера снимает ограничение.
# Значение entry[mp]["tariff"] → требуемый флаг в наборе tariffs.
_TARIFF_GATES = {"premium_plus", "premium", "jam", "medium"}

# Причины недоступности (стабильные коды для UI/телеметрии).
UNAVAILABLE_API = "api"          # маркетплейс не отдаёт через API
UNAVAILABLE_TARIFF = "tariff"    # нужен платный тариф/подписка
UNAVAILABLE_PULT = "pult"        # маркетплейс отдаёт, но PULT ещё не реализовал интеграцию
AVAILABLE = "available"

# Полное имя маркетплейса → короткий ключ реестра. availability()/verdict() принимают оба
# написания, чтобы вызывающая сторона (или тест) могла передать 'wildberries' или 'wb'.
_CANON_MP = {
    "wildberries": "wb", "wb": "wb",
    "ozon": "ozon",
    "yandex_market": "yandex", "yandex": "yandex", "ym": "yandex",
    "megamarket": "megamarket", "mm": "megamarket",
}


def _canon_mp(marketplace: str) -> str:
    return _CANON_MP.get((marketplace or "").lower(), (marketplace or "").lower())


@lru_cache(maxsize=1)
def _load() -> dict:
    with _REGISTRY_PATH.open(encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _index() -> dict[str, dict]:
    return {c["key"]: c for c in _load()["capabilities"]}


def all_keys() -> list[str]:
    return list(_index().keys())


def get(key: str) -> Optional[dict]:
    """Полная запись по ключу пункта доктрины, либо None."""
    return _index().get(key)


def verdict(key: str, marketplace: str) -> Optional[str]:
    """Вердикт пункта для маркетплейса: 'api' | 'compute' | 'impossible' | None."""
    cap = get(key)
    if not cap:
        return None
    mp = cap.get(_canon_mp(marketplace))
    return mp.get("verdict") if mp else None


def availability(key: str, marketplace: str, tariffs: Optional[set[str]] = None) -> dict:
    """
    Доступность пункта для (маркетплейс, тариф селлера) — для §19.

    Возвращает: {available: bool, status, verdict, reason, source, endpoint, note}.
    status: 'available' | 'api' (нет в API) | 'tariff' (нужен тариф).
    """
    tariffs = tariffs or set()
    cap = get(key)
    if not cap:
        return {"available": False, "status": UNAVAILABLE_API, "reason": "unknown_key",
                "verdict": None, "key": key, "marketplace_api": False, "pult_supported": False}

    mp = cap.get(_canon_mp(marketplace)) or {}
    v = mp.get("verdict", "impossible")
    # The marketplace exposing the capability through its API (verdict api/compute) is a SEPARATE
    # fact from whether PULT has actually built the integration. `pult_supported` defaults to True
    # so every capability PULT already ships is unaffected; it is set False only where the
    # marketplace has the API but PULT has no provider/executor path (e.g. Ozon/Yandex reviews).
    marketplace_api = v in ("api", "compute")
    pult_supported = bool(mp.get("pult_supported", True))
    out = {
        "key": key, "label": cap.get("label"), "marketplace": marketplace,
        "verdict": v, "endpoint": mp.get("endpoint"),
        "marketplace_api": marketplace_api, "pult_supported": pult_supported,
        "source": cap.get("source") or mp.get("source"),
        "note": mp.get("note") or cap.get("note"),
        "scope": cap.get("scope"),
    }

    if v == "impossible":
        out.update(available=False, status=UNAVAILABLE_API,
                   reason="недоступно через API маркетплейса")
        return out

    required = mp.get("tariff")
    if required and required in _TARIFF_GATES and required not in tariffs:
        out.update(available=False, status=UNAVAILABLE_TARIFF, required_tariff=required,
                   reason=f"требуется тариф: {required}")
        return out

    # The marketplace API exists (and any tariff gate is satisfied), but PULT has not built the
    # integration → NOT available to the seller. The marketplace-API fact stays visible in
    # `marketplace_api`/`verdict`, so a UI can honestly say "маркетплейс поддерживает, PULT — пока нет".
    if not pult_supported:
        out.update(available=False, status=UNAVAILABLE_PULT,
                   reason="PULT пока не поддерживает эту возможность для этого маркетплейса")
        return out

    # api / compute, тариф (если нужен) и интеграция PULT есть → доступно
    out.update(available=True, status=AVAILABLE, reason=None)
    return out


def card_capabilities(marketplace: str, tariffs: Optional[set[str]] = None) -> list[dict]:
    """Все пункты для карточки листинга на маркетплейсе — готово под §6.1 UI."""
    return [availability(k, marketplace, tariffs) for k in all_keys()]


def freshness(marketplace: str) -> dict:
    return _load().get("freshness", {}).get(marketplace, {})


def rate_limit(marketplace: str) -> Optional[str]:
    return _load().get("rate_limits", {}).get(marketplace)
