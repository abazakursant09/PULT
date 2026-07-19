"""
CSV parser for WB / Ozon / Yandex Market exports.

Supports:
  - Delimiter auto-detection: , ; \\t
  - UTF-8 with BOM
  - Marketplace auto-detection from column headers
  - Import type auto-detection (finance vs products)
  - Human-readable validation errors in Russian
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# ── Column alias maps ─────────────────────────────────────────────────────────

FINANCE_COLUMNS: dict[str, list[str]] = {
    "date":       ["дата", "date", "период"],
    "sku":        ["артикул wb", "wb артикул", "артикул ozon", "ozon sku", "sku ozon",
                   "артикул яндекс", "offer_id", "артикул", "sku", "номенклатура"],
    "title":      ["название", "наименование", "товар", "наименование товара"],
    "revenue":    ["выручка", "сумма продаж", "оборот", "revenue"],
    "commission": ["комиссия wb", "комиссия ozon", "комиссия яндекс", "комиссия маркетплейса",
                   "комиссия", "вознаграждение", "сервисный сбор"],
    "logistics":  ["логистика", "доставка", "стоимость доставки", "стоимость логистики",
                   "транспортные расходы"],
    "ad_spend":   ["реклама", "рекламный бюджет", "расходы на рекламу",
                   "расходы на продвижение", "расходы на буст", "продвижение"],
    "net_profit": ["чистая прибыль", "прибыль", "profit", "доход"],
    "quantity":   ["количество", "кол-во", "qty", "штук", "продано"],
}

PRODUCT_COLUMNS: dict[str, list[str]] = {
    "sku":           ["артикул wb", "wb артикул", "sku ozon", "ozon sku",
                      "артикул ozon", "offer_id", "артикул", "sku"],
    "title":         ["название", "наименование", "товар"],
    "price":         ["цена", "цена продажи", "розничная цена", "price"],
    "stock":         ["остаток", "склад", "в наличии", "fbo остаток", "fbs остаток", "stock"],
    "rating":        ["рейтинг", "rating"],
    "reviews_count": ["отзывы", "количество отзывов", "reviews", "отзывов"],
}

CARD_CONTENT_COLUMNS: dict[str, list[str]] = {
    "sku":             ["артикул wb", "wb артикул", "артикул ozon", "sku ozon", "ozon sku",
                        "offer_id", "vendor_code", "nm_id", "barcode", "штрихкод", "артикул", "sku",
                        "номенклатура"],
    "title":           ["название", "наименование", "наименование товара", "title", "товар"],
    "description":     ["описание", "текст описания", "описание товара", "description"],
    "brand":           ["бренд", "производитель", "торговая марка", "brand"],
    "category":        ["категория", "предмет", "тип товара", "category", "категория товара"],
    "characteristics": ["характеристики", "атрибуты", "характеристики товара", "characteristics",
                        "attributes"],
    "image_count":     ["количество фото", "кол-во фото", "фото", "изображения", "картинки",
                        "image count", "images count"],
    "image_urls":      ["ссылки на фото", "url фото", "ссылки на изображения", "image urls",
                        "images", "фото url"],
}

RETURNS_COLUMNS: dict[str, list[str]] = {
    "date":          ["дата", "date", "период", "дата возврата", "дата оформления возврата"],
    "sku":           ["артикул wb", "wb артикул", "артикул ozon", "sku ozon", "ozon sku",
                      "offer_id", "артикул", "sku", "номенклатура"],
    "returns_qty":   ["количество возвратов", "кол-во возвратов", "возвраты", "возвращено",
                      "штук возвращено", "returns", "return qty", "количество", "кол-во"],
    "return_amount": ["сумма возврата", "сумма возвратов", "стоимость возврата",
                      "сумма к возврату", "return amount", "возврат сумма"],
    "reason":        ["причина возврата", "причина", "return reason", "reason"],
}

# ── Marketplace / type detection signatures ───────────────────────────────────

_WB_SIGS    = {"артикул wb", "wb артикул", "wb-артикул", "вайлдберриз",
               "wb артикул поставщика", "артикул поставщика wb"}
_OZON_SIGS  = {"sku ozon", "ozon sku", "артикул ozon", "fbo", "озон", "ozon"}
_YM_SIGS    = {"offer_id", "яндекс маркет", "маркет", "ym артикул",
               "яндекс", "yandex market"}
_FIN_SIGS   = {"выручка", "комиссия", "прибыль", "чистая прибыль", "revenue", "оборот"}
_PROD_SIGS  = {"остаток", "рейтинг", "отзывы", "цена продажи", "stock", "rating"}
_RET_SIGS   = {"возврат", "возвраты", "сумма возврата", "причина возврата", "return", "возвращено"}
_CARD_SIGS  = {"описание", "характеристики", "description", "characteristics", "атрибуты",
               "текст описания"}


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ParseResult:
    marketplace:           Optional[str]   = None   # wb | ozon | ym | None
    import_type:           Optional[str]   = None   # finance | products | None
    total_rows:            int             = 0
    valid_rows:            int             = 0
    skipped_rows:          int             = 0
    headers:               list[str]       = field(default_factory=list)
    mapped_columns:        dict[str, str]  = field(default_factory=dict)   # field → csv header
    unmapped_required:     list[str]       = field(default_factory=list)   # required fields missing
    preview_rows:          list[dict]      = field(default_factory=list)   # first 5
    parsed_data:           list[dict]      = field(default_factory=list)   # all valid rows
    warnings:              list[str]       = field(default_factory=list)   # non-fatal
    errors:                list[str]       = field(default_factory=list)   # fatal / blocking
    file_hash:             str             = ""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalise(s: str) -> str:
    return s.strip().lower().replace("\xa0", " ").replace("  ", " ")


def _detect_delimiter(sample: str) -> str:
    counts = {d: sample.count(d) for d in (",", ";", "\t")}
    return max(counts, key=counts.get)  # type: ignore[arg-type]


def _detect_marketplace(headers_lower: list[str]) -> Optional[str]:
    hset = set(headers_lower)
    for h in headers_lower:
        if h in _WB_SIGS:   return "wb"
        if h in _OZON_SIGS: return "ozon"
        if h in _YM_SIGS:   return "ym"
    # partial match
    if any(any(sig in h for sig in _WB_SIGS)   for h in headers_lower): return "wb"
    if any(any(sig in h for sig in _OZON_SIGS) for h in headers_lower): return "ozon"
    if any(any(sig in h for sig in _YM_SIGS)   for h in headers_lower): return "ym"
    _ = hset  # used in logic above
    return None


def _detect_import_type(headers_lower: list[str]) -> Optional[str]:
    hset = set(headers_lower)
    fin_score  = sum(1 for h in hset if any(s in h for s in _FIN_SIGS))
    prod_score = sum(1 for h in hset if any(s in h for s in _PROD_SIGS))
    ret_score  = sum(1 for h in hset if any(s in h for s in _RET_SIGS))
    card_score = sum(1 for h in hset if any(s in h for s in _CARD_SIGS))
    # Card-content headers carry a distinctive signature (описание + характеристики / description)
    # that finance/products/returns never do; when present it wins so a card export is not
    # mistaken for products.
    if card_score > fin_score and card_score > prod_score and card_score > ret_score:
        return "card_content"
    # Returns headers carry a distinctive signature («возврат» / return) that finance/products
    # never do; when present it wins so a returns export is not mistaken for finance.
    if ret_score > fin_score and ret_score > prod_score:  return "returns"
    if fin_score > prod_score:   return "finance"
    if prod_score > fin_score:   return "products"
    if fin_score > 0:            return "finance"
    if prod_score > 0:           return "products"
    if ret_score > 0:            return "returns"
    if card_score > 0:           return "card_content"
    return None


def _build_column_mapping(
    headers_lower: list[str],
    column_map: dict[str, list[str]],
) -> dict[str, str]:
    """Returns { field_name → csv_header_original } for matched columns."""
    mapping: dict[str, str] = {}
    for field_name, aliases in column_map.items():
        for i, h in enumerate(headers_lower):
            if h in aliases or any(alias in h for alias in aliases):
                # store the original (non-lowercased) header
                mapping[field_name] = h
                break
    return mapping


# ── Value parsers ─────────────────────────────────────────────────────────────

def _parse_float(raw: str, field_label: str, row_num: int) -> tuple[float, Optional[str]]:
    v = re.sub(r"[₽%\s]", "", raw.replace(",", "."))
    if not v:
        return 0.0, None
    try:
        f = float(v)
        if f < 0:
            return 0.0, (
                f"Строка {row_num}: поле «{field_label}» содержит отрицательное значение "
                f"({raw!r}), заменено на 0"
            )
        return f, None
    except ValueError:
        return 0.0, f"Строка {row_num}: поле «{field_label}» содержит некорректное число ({raw!r})"


def _parse_int(raw: str, field_label: str, row_num: int) -> tuple[int, Optional[str]]:
    f, warn = _parse_float(raw, field_label, row_num)
    return int(f), warn


def _parse_date(raw: str, field_label: str, row_num: int) -> tuple[Optional[str], Optional[str]]:
    raw = raw.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d"), None
        except ValueError:
            continue
    # try year-month only
    for fmt in ("%m.%Y", "%Y-%m"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m") + "-01", None
        except ValueError:
            continue
    return None, (
        f"Строка {row_num}: поле «{field_label}» содержит неверную дату ({raw!r}). "
        f"Ожидается ДД.ММ.ГГГГ"
    )


# ── Row parsers ───────────────────────────────────────────────────────────────

def _parse_finance_row(
    row: dict[str, str],
    mapping: dict[str, str],   # field → csv_header_lower
    row_num: int,
) -> tuple[Optional[dict], list[str]]:
    warnings: list[str] = []
    out: dict = {}

    def get(field: str) -> str:
        return row.get(mapping.get(field, ""), "")

    # SKU — required
    sku = get("sku").strip()
    if not sku:
        return None, [f"Строка {row_num}: пропущена — отсутствует обязательное поле «Артикул»"]
    out["sku"] = sku

    # Title
    out["title"] = get("title").strip() or sku

    # Date
    date_raw = get("date").strip()
    if date_raw:
        date_val, warn = _parse_date(date_raw, "Дата", row_num)
        if warn:
            warnings.append(warn)
        out["date"] = date_val
    else:
        out["date"] = None

    # Numeric fields
    for dest, label in [
        ("revenue",    "Выручка"),
        ("commission", "Комиссия"),
        ("logistics",  "Логистика"),
        ("ad_spend",   "Реклама"),
        ("net_profit", "Чистая прибыль"),
    ]:
        val, warn = _parse_float(get(dest), label, row_num)
        if warn:
            warnings.append(warn)
        out[dest] = val

    qty_val, warn = _parse_int(get("quantity"), "Количество", row_num)
    if warn:
        warnings.append(warn)
    out["quantity"] = qty_val

    return out, warnings


def _parse_product_row(
    row: dict[str, str],
    mapping: dict[str, str],
    row_num: int,
) -> tuple[Optional[dict], list[str]]:
    warnings: list[str] = []
    out: dict = {}

    def get(field: str) -> str:
        return row.get(mapping.get(field, ""), "")

    sku = get("sku").strip()
    if not sku:
        return None, [f"Строка {row_num}: пропущена — отсутствует обязательное поле «Артикул/SKU»"]
    out["sku"] = sku
    out["title"] = get("title").strip() or sku

    price_raw = get("price")
    if price_raw.strip():
        price_val, warn = _parse_float(price_raw, "Цена", row_num)
        if warn: warnings.append(warn)
        out["price"] = price_val
    else:
        out["price"] = None

    stock_raw = get("stock")
    if stock_raw.strip():
        stock_val, warn = _parse_int(stock_raw, "Остаток", row_num)
        if warn: warnings.append(warn)
        out["stock"] = stock_val
    else:
        out["stock"] = None

    rating_raw = get("rating")
    if rating_raw.strip():
        rating_val, warn = _parse_float(rating_raw, "Рейтинг", row_num)
        if warn: warnings.append(warn)
        out["rating"] = min(rating_val, 5.0)
    else:
        out["rating"] = None

    rev_raw = get("reviews_count")
    if rev_raw.strip():
        rev_val, warn = _parse_int(rev_raw, "Отзывы", row_num)
        if warn: warnings.append(warn)
        out["reviews_count"] = rev_val
    else:
        out["reviews_count"] = None

    return out, warnings


def _parse_returns_row(
    row: dict[str, str],
    mapping: dict[str, str],
    row_num: int,
) -> tuple[Optional[dict], list[str]]:
    warnings: list[str] = []
    out: dict = {}

    def get(field: str) -> str:
        return row.get(mapping.get(field, ""), "")

    sku = get("sku").strip()
    if not sku:
        return None, [f"Строка {row_num}: пропущена — отсутствует обязательное поле «Артикул/SKU»"]
    out["sku"] = sku

    date_raw = get("date").strip()
    if date_raw:
        date_val, warn = _parse_date(date_raw, "Дата", row_num)
        if warn:
            warnings.append(warn)
        out["date"] = date_val
    else:
        out["date"] = None

    qty_val, warn = _parse_int(get("returns_qty"), "Количество возвратов", row_num)
    if warn:
        warnings.append(warn)
    out["returns_qty"] = qty_val

    amt_val, warn = _parse_float(get("return_amount"), "Сумма возврата", row_num)
    if warn:
        warnings.append(warn)
    out["return_amount"] = amt_val

    reason = get("reason").strip()
    out["reason"] = reason or None

    return out, warnings


def _parse_card_content_row(
    row: dict[str, str],
    mapping: dict[str, str],
    row_num: int,
) -> tuple[Optional[dict], list[str]]:
    import json as _json

    warnings: list[str] = []
    out: dict = {}

    def get(field: str) -> str:
        return row.get(mapping.get(field, ""), "")

    sku = get("sku").strip()
    if not sku:
        return None, [f"Строка {row_num}: пропущена — отсутствует обязательное поле «Артикул/SKU»"]
    out["sku"] = sku

    out["title"] = get("title").strip() or None
    out["description"] = get("description").strip() or None
    out["brand"] = get("brand").strip() or None
    out["category"] = get("category").strip() or None

    # Characteristics: captured as a JSON string. Accept an already-JSON value, else store the raw
    # reported text under a single key so nothing is lost or invented.
    chars_raw = get("characteristics").strip()
    if chars_raw:
        try:
            parsed = _json.loads(chars_raw)
            out["characteristics_json"] = _json.dumps(parsed, ensure_ascii=False)
        except (ValueError, TypeError):
            out["characteristics_json"] = _json.dumps({"raw": chars_raw}, ensure_ascii=False)
    else:
        out["characteristics_json"] = None

    img_raw = get("image_count").strip()
    if img_raw:
        img_val, warn = _parse_int(img_raw, "Количество фото", row_num)
        if warn:
            warnings.append(warn)
        out["image_count"] = img_val
    else:
        out["image_count"] = None

    urls_raw = get("image_urls").strip()
    if urls_raw:
        try:
            parsed = _json.loads(urls_raw)
            out["image_urls_json"] = _json.dumps(parsed, ensure_ascii=False)
        except (ValueError, TypeError):
            urls = [u.strip() for u in re.split(r"[;,\s]+", urls_raw) if u.strip()]
            out["image_urls_json"] = _json.dumps(urls, ensure_ascii=False)
    else:
        out["image_urls_json"] = None

    out["date"] = None

    return out, warnings


# ── Main entry point ──────────────────────────────────────────────────────────

def parse_csv(
    file_bytes: bytes,
    marketplace: Optional[str] = None,
    import_type: Optional[str] = None,
) -> ParseResult:
    result = ParseResult()

    # Hash
    result.file_hash = hashlib.sha256(file_bytes).hexdigest()

    # Decode with BOM support
    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = file_bytes.decode("cp1251")
        except UnicodeDecodeError:
            result.errors.append(
                "Не удалось декодировать файл. Убедитесь, что кодировка — UTF-8 или Windows-1251."
            )
            return result

    # Delimiter detection
    sample = text[:4096]
    delim  = _detect_delimiter(sample)

    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    try:
        raw_headers = reader.fieldnames or []
    except Exception:
        result.errors.append("Не удалось прочитать заголовки CSV.")
        return result

    if not raw_headers:
        result.errors.append("CSV не содержит заголовков колонок.")
        return result

    headers_lower = [_normalise(h) for h in raw_headers]
    result.headers = list(raw_headers)

    # Auto-detect marketplace / type
    result.marketplace  = marketplace  or _detect_marketplace(headers_lower)
    result.import_type  = import_type  or _detect_import_type(headers_lower)

    if not result.import_type:
        result.errors.append(
            "Не удалось определить тип данных (Финансы или Товары). "
            "Выберите тип вручную."
        )
        return result

    # Build column mapping using lower-cased headers as keys
    col_map = {
        "finance":      FINANCE_COLUMNS,
        "products":     PRODUCT_COLUMNS,
        "returns":      RETURNS_COLUMNS,
        "card_content": CARD_CONTENT_COLUMNS,
    }.get(result.import_type, PRODUCT_COLUMNS)
    mapping = _build_column_mapping(headers_lower, col_map)

    # Map back to lower header keys so row lookup works
    # DictReader uses original header strings — build mapping from lower → original
    lower_to_original = {_normalise(h): h for h in raw_headers}
    mapped_by_original = {
        field_name: lower_to_original.get(lower_hdr, lower_hdr)
        for field_name, lower_hdr in mapping.items()
    }
    result.mapped_columns = {f: mapped_by_original.get(f, "") for f in mapping}

    # Check required fields
    required = ["sku"] if result.import_type == "products" else ["sku"]
    for req in required:
        if req not in mapping:
            result.unmapped_required.append(req)

    if result.unmapped_required:
        # FATAL, not a warning. Every row needs `sku`; without that column every row is
        # skipped, and a warning still leaves the import confirmable — which is how a seller
        # could reach a green "Импортировано 0 строк" screen believing their report landed.
        # Name the column, because "проверьте сопоставление" alone does not say what is wrong.
        result.errors.append(
            f"В файле нет обязательной колонки: {', '.join(result.unmapped_required)}. "
            f"Добавьте её в отчёт или скачайте шаблон."
        )

    # Parse rows
    row_num = 1
    for raw_row in reader:
        row_num += 1
        result.total_rows += 1

        # Build a row dict keyed by lower-case header
        row_lower: dict[str, str] = {
            _normalise(k): (v or "") for k, v in raw_row.items() if k
        }

        if result.import_type == "finance":
            parsed, warnings = _parse_finance_row(row_lower, mapping, row_num)
        elif result.import_type == "returns":
            parsed, warnings = _parse_returns_row(row_lower, mapping, row_num)
        elif result.import_type == "card_content":
            parsed, warnings = _parse_card_content_row(row_lower, mapping, row_num)
        else:
            parsed, warnings = _parse_product_row(row_lower, mapping, row_num)

        result.warnings.extend(warnings)

        if parsed is None:
            result.skipped_rows += 1
            continue

        result.valid_rows += 1
        result.parsed_data.append(parsed)
        if len(result.preview_rows) < 5:
            result.preview_rows.append(parsed)

    if result.total_rows == 0:
        result.errors.append("CSV не содержит строк данных (только заголовок).")
    elif result.valid_rows == 0 and not result.errors:
        # The file had rows and the required columns, yet nothing survived parsing. Importing
        # this would write nothing while reporting success, so it is an error the seller can act
        # on rather than a silent no-op.
        result.errors.append(
            f"Ни одна строка не распознана (проверено {result.total_rows}). "
            f"Проверьте формат файла и заполненность колонок."
        )

    # Cap warnings to avoid huge responses
    if len(result.warnings) > 50:
        trimmed = len(result.warnings) - 50
        result.warnings = result.warnings[:50]
        result.warnings.append(f"... и ещё {trimmed} предупреждений.")

    return result


# ── Template CSV generator ────────────────────────────────────────────────────

_TEMPLATES: dict[tuple[str, str], str] = {
    ("wb", "finance"): (
        "Дата,Артикул WB,Название,Выручка,Комиссия,Логистика,Реклама,Чистая прибыль,Количество\n"
        "01.01.2025,12345678,Крем для рук,10000,1500,800,500,3200,25\n"
        "02.01.2025,12345678,Крем для рук,8500,1275,680,425,2720,21\n"
    ),
    ("wb", "products"): (
        "Артикул WB,Название,Цена,Остаток,Рейтинг,Отзывы\n"
        "12345678,Крем для рук,499,150,4.8,320\n"
        "87654321,Шампунь 500мл,299,80,4.6,140\n"
    ),
    ("ozon", "finance"): (
        "Дата,SKU Ozon,Название,Выручка,Комиссия,Доставка,Реклама,Прибыль,Количество\n"
        "01.01.2025,987654321,Крем для рук,10000,1800,600,400,3200,25\n"
    ),
    ("ozon", "products"): (
        "SKU Ozon,Название,Цена,Остаток,Рейтинг,Отзывы\n"
        "987654321,Крем для рук,499,120,4.7,280\n"
    ),
    ("ym", "finance"): (
        "Дата,Артикул Яндекс,Название,Выручка,Комиссия,Доставка,Реклама,Прибыль,Количество\n"
        "01.01.2025,offer-001,Крем для рук,8000,1200,550,350,2700,20\n"
    ),
    ("ym", "products"): (
        "Артикул Яндекс,Название,Цена,Остаток,Рейтинг,Отзывы\n"
        "offer-001,Крем для рук,499,60,4.5,90\n"
    ),
    ("wb", "returns"): (
        "Дата возврата,Артикул WB,Количество возвратов,Сумма возврата,Причина возврата\n"
        "05.01.2025,12345678,3,1497,Не подошёл размер\n"
        "07.01.2025,87654321,1,299,Брак\n"
    ),
    ("ozon", "returns"): (
        "Дата возврата,SKU Ozon,Количество возвратов,Сумма возврата,Причина возврата\n"
        "05.01.2025,987654321,2,998,Не подошёл товар\n"
    ),
    ("ym", "returns"): (
        "Дата возврата,Артикул Яндекс,Количество возвратов,Сумма возврата,Причина возврата\n"
        "05.01.2025,offer-001,1,499,Передумал\n"
    ),
    ("wb", "card_content"): (
        "Артикул WB,Название,Описание,Бренд,Категория,Характеристики,Количество фото,Ссылки на фото\n"
        "12345678,Крем для рук,Увлажняющий крем 75 мл,AquaCare,Красота,\"{\"\"Объём\"\":\"\"75 мл\"\"}\",5,\"https://a.jpg;https://b.jpg\"\n"
    ),
    ("ozon", "card_content"): (
        "SKU Ozon,Название,Описание,Бренд,Категория,Характеристики,Количество фото\n"
        "987654321,Крем для рук,Питательный крем,AquaCare,Уход,\"Объём: 75 мл; Тип: крем\",4\n"
    ),
    ("ym", "card_content"): (
        "Артикул Яндекс,Название,Описание,Бренд,Категория,Характеристики,Количество фото\n"
        "offer-001,Крем для рук,Крем для сухой кожи,AquaCare,Косметика,\"Объём: 75 мл\",3\n"
    ),
}


def get_template(marketplace: str, import_type: str) -> Optional[str]:
    return _TEMPLATES.get((marketplace, import_type))
