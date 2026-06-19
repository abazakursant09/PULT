"""
Mock-сид графа Товар → Листинг → Решение (Doctrine §3/§7).

Берёт плоские mock-листинги с WB/Ozon/Yandex, прогоняет через matcher (§3.1),
пишет PhysicalProduct + ProductListing + пару Decision — чтобы граф ОЖИЛ:
- один товар с двумя листингами (WB+Ozon) схлопнут по barcode
- один — по seller_sku
- один — привязан fuzzy по названию (confirmed=False, ждёт подтверждения)
- один orphan-атом (нет ключей)

Идемпотентно по user: перед сидом сносит spine этого юзера (decisions →
listings → physical_products), затем пишет заново.

Run:
  python -m scripts.seed_mock_graph                       # юзер по умолчанию
  python -m scripts.seed_mock_graph --email you@mail.ru
  python -m scripts.seed_mock_graph --dry-run             # только matcher-отчёт
"""
from __future__ import annotations

import argparse
import asyncio
import uuid

from sqlalchemy import select, delete

from database import AsyncSessionLocal
from models.user import User
from models.physical_product import PhysicalProduct
from models.product_listing import ProductListing
from models.decision import Decision
from services.listing_matcher import match_listings

DEFAULT_EMAIL = "testuser@pult.ru"

# Плоские листинги «как из импорта». Намеренно: один EAN на WB+Ozon, общий SKU,
# почти-одинаковое имя без ключей, и orphan.
MOCK_LISTINGS: list[dict] = [
    {"marketplace": "wb",     "external_id": "WB-101", "title": "Термокружка Stanley 0.47л чёрная",
     "barcode": "4607177453210", "seller_sku": "MUG-STAN-047", "brand": "Stanley"},
    {"marketplace": "ozon",   "external_id": "OZ-201", "title": "Stanley термокружка 470мл (чёрный)",
     "barcode": "4607177453210", "seller_sku": "MUG-STAN-047-OZ", "brand": "Stanley"},

    {"marketplace": "wb",     "external_id": "WB-102", "title": "Носки мужские хлопок 5 пар",
     "barcode": None, "seller_sku": "SOCKS-M-5", "brand": "БезБренда"},
    {"marketplace": "yandex", "external_id": "YM-301", "title": "Носки мужские, 5 пар, набор",
     "barcode": None, "seller_sku": "socks-m-5", "brand": "БезБренда"},

    {"marketplace": "ozon",   "external_id": "OZ-202", "title": "Коврик для йоги 6мм фиолетовый",
     "barcode": None, "seller_sku": None, "brand": "FitLine"},
    {"marketplace": "wb",     "external_id": "WB-103", "title": "Коврик для йоги 6 мм, фиолетовый",
     "barcode": None, "seller_sku": None, "brand": "FitLine"},

    {"marketplace": "wb",     "external_id": "WB-104", "title": "Лампа настольная LED 12W",
     "barcode": "4601546098712", "seller_sku": "LAMP-LED-12", "brand": "Era"},
    {"marketplace": "ozon",   "external_id": "OZ-203", "title": "Лампа настольная LED 12 Вт Era",
     "barcode": None, "seller_sku": None, "brand": "Era"},
]


async def _resolve_user(db, email: str) -> str:
    uid = (await db.execute(select(User.id).where(User.email == email))).scalar_one_or_none()
    if uid is None:
        raise SystemExit(f"Нет юзера {email}. Укажи --email существующего.")
    return uid


async def _wipe_spine(db, uid: str) -> None:
    await db.execute(delete(Decision).where(Decision.user_id == uid))
    await db.execute(delete(ProductListing).where(ProductListing.user_id == uid))
    await db.execute(delete(PhysicalProduct).where(PhysicalProduct.user_id == uid))


def _report(groups) -> None:
    print(f"=== matcher: {len(groups)} атом(ов) из {sum(len(g.listings) for g in groups)} листингов ===")
    for g in groups:
        print(f"\n* {g.title}  [barcode={g.barcode or '-'} sku={g.seller_sku or '-'}]")
        for l in g.listings:
            flag = "OK" if l.confirmed else "MANUAL CHECK"
            print(f"    - {l.marketplace}:{l.external_id}  {l.match_method} {l.match_confidence:.2f}  [{flag}]")


async def _run(email: str, dry_run: bool) -> None:
    groups = match_listings(MOCK_LISTINGS)
    _report(groups)
    if dry_run:
        print("\n[DRY RUN] записи в БД не делались.")
        return

    async with AsyncSessionLocal() as db:
        uid = await _resolve_user(db, email)
        await _wipe_spine(db, uid)

        n_pp = n_ls = n_dec = 0
        for g in groups:
            pp_id = str(uuid.uuid4())
            db.add(PhysicalProduct(
                id=pp_id, user_id=uid, title=g.title,
                barcode=g.barcode, seller_sku=g.seller_sku, brand=g.brand,
            ))
            n_pp += 1
            first_listing_id = None
            for l in g.listings:
                ls_id = str(uuid.uuid4())
                first_listing_id = first_listing_id or ls_id
                db.add(ProductListing(
                    id=ls_id, physical_product_id=pp_id, user_id=uid,
                    marketplace=l.marketplace, external_id=l.external_id, title=l.title,
                    match_method=l.match_method, match_confidence=l.match_confidence,
                    confirmed=l.confirmed,
                ))
                n_ls += 1

            # Decision на атомах с >1 листингом: разнобой цены/контента между МП —
            # типовая гипотеза роста конверсии (§8.1). Mock, severity=warn.
            if len(g.listings) > 1:
                db.add(Decision(
                    id=str(uuid.uuid4()), user_id=uid,
                    physical_product_id=pp_id, listing_id=first_listing_id,
                    problem="Контент листингов расходится между маркетплейсами",
                    cause="Разные title/фото на WB и Ozon у одного товара",
                    effect="Ниже CTR на отстающем МП → недобор продаж",
                    action="Выровнять карточку по лучшему листингу",
                    action_key=None, pnl_impact=1500.0, pnl_level="level1",
                    severity="warn", source="compute", status="open",
                ))
                n_dec += 1

        await db.commit()

    print(f"\n=== записано для {email} ===")
    print(f"  physical_products: {n_pp}")
    print(f"  product_listings:  {n_ls}")
    print(f"  decisions:         {n_dec}")
    print("  граф ожил → открой Товары / Кабинет.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default=DEFAULT_EMAIL)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(_run(args.email, args.dry_run))
