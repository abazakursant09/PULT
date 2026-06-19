"""
Listing → Physical Product matcher (Doctrine §3.1).

Превращает плоский список листингов с РАЗНЫХ маркетплейсов в граф
Товар → Листинг: листинги одного физ.товара (WB + Ozon + Yandex) схлопываются
под один PhysicalProduct. Это «оживляет» граф — без матчинга есть только
изолированные листинги, нет атома.

Каскад уверенности (§3.1):
    barcode (EAN)  → confidence 1.00, method "barcode"     — железно один товар
    seller_sku     → confidence 0.90, method "sku"         — свой SKU селлера
    name_fuzzy     → confidence = ratio, method "name_fuzzy"
    нет совпадений → новый атом (singleton), method "seed", confidence 1.00

confirmed = confidence >= CONFIRM_THRESHOLD. Ниже порога — попадает в граф, но
требует ручного подтверждения (confirmed=False), не применяется молча.

Чистый / детерминированный: НЕТ доступа к БД и к случайности. Вход — dict'ы,
выход — сгруппированные кандидаты. Тестируемо и переиспользуемо и сидом, и
живым import-пайплайном. Создание строк в БД — забота вызывающего.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

from services.product_resolver import normalize_marketplace, normalize_sku

# Порог авто-подтверждения. Совпадение слабее → confirmed=False (ручная проверка).
CONFIRM_THRESHOLD = 0.90
# Минимум для fuzzy-привязки к существующему атому. Ниже — новый атом.
FUZZY_ATTACH_MIN = 0.78


def _norm_barcode(value: Optional[str]) -> Optional[str]:
    s = "".join(ch for ch in (value or "") if ch.isdigit())
    return s or None


def _norm_title(value: Optional[str]) -> str:
    return " ".join((value or "").strip().lower().split())


def _name_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


@dataclass
class MatchedListing:
    marketplace: str
    external_id: str
    title: Optional[str]
    barcode: Optional[str]
    seller_sku: Optional[str]
    match_method: str           # barcode | sku | name_fuzzy | seed
    match_confidence: float     # 0..1
    confirmed: bool


@dataclass
class PhysicalProductGroup:
    """Кандидат на PhysicalProduct + его листинги. id присваивает вызывающий."""
    title: str
    barcode: Optional[str] = None
    seller_sku: Optional[str] = None
    brand: Optional[str] = None
    listings: list[MatchedListing] = field(default_factory=list)

    # ключи матчинга (нормализованные), не для записи в БД
    _norm_titles: list[str] = field(default_factory=list, repr=False)


def match_listings(raw: list[dict]) -> list[PhysicalProductGroup]:
    """
    raw: список листингов. Поля:
        marketplace, external_id (обяз.),
        title, barcode, seller_sku, brand (опц.)
    Возвращает группы (атомы) в детерминированном порядке появления.
    """
    groups: list[PhysicalProductGroup] = []
    by_barcode: dict[str, PhysicalProductGroup] = {}
    by_sku: dict[str, PhysicalProductGroup] = {}

    def _attach(g: PhysicalProductGroup, item: dict, method: str, conf: float) -> None:
        g.listings.append(MatchedListing(
            marketplace=normalize_marketplace(item.get("marketplace")),
            external_id=str(item.get("external_id")),
            title=item.get("title"),
            barcode=_norm_barcode(item.get("barcode")),
            seller_sku=normalize_sku(item.get("seller_sku")),
            match_method=method,
            match_confidence=round(conf, 4),
            confirmed=conf >= CONFIRM_THRESHOLD,
        ))
        nt = _norm_title(item.get("title"))
        if nt and nt not in g._norm_titles:
            g._norm_titles.append(nt)

    def _new_group(item: dict, method: str, conf: float) -> PhysicalProductGroup:
        bc = _norm_barcode(item.get("barcode"))
        sku = normalize_sku(item.get("seller_sku"))
        g = PhysicalProductGroup(
            title=(item.get("title") or sku or bc or "Без названия"),
            barcode=bc, seller_sku=sku, brand=item.get("brand"),
        )
        groups.append(g)
        if bc:
            by_barcode.setdefault(bc, g)
        if sku:
            by_sku.setdefault(sku, g)
        _attach(g, item, method, conf)
        return g

    for item in raw:
        bc = _norm_barcode(item.get("barcode"))
        sku = normalize_sku(item.get("seller_sku"))

        # 1) barcode — самый сильный ключ
        if bc and bc in by_barcode:
            _attach(by_barcode[bc], item, "barcode", 1.0)
            continue
        # 2) seller_sku
        if sku and sku in by_sku:
            _attach(by_sku[sku], item, "sku", 0.90)
            continue
        # 3) fuzzy по названию против уже известных атомов
        nt = _norm_title(item.get("title"))
        best_g: Optional[PhysicalProductGroup] = None
        best_r = 0.0
        if nt:
            for g in groups:
                r = max((_name_ratio(nt, t) for t in g._norm_titles), default=0.0)
                if r > best_r:
                    best_r, best_g = r, g
        if best_g is not None and best_r >= FUZZY_ATTACH_MIN:
            _attach(best_g, item, "name_fuzzy", best_r)
            # докинуть ключи в индекс, если их у группы ещё не было
            if bc:
                by_barcode.setdefault(bc, best_g)
            if sku:
                by_sku.setdefault(sku, best_g)
            continue
        # 4) ничего не подошло — новый атом
        _new_group(item, "seed", 1.0)

    return groups
