"""
Stub verification services for FNS, 2GIS, and China USCC.
All checks are deterministic stubs — no real API calls.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
import re


@dataclass
class VerificationResult:
    ok:      bool
    source:  str        # fns | 2gis | uscc | manual
    message: str
    reason:  str = ""   # populated on failure


# ── FNS (ФНС Russia) ─────────────────────────────────────────────────────────

def verify_fns(inn: str | None) -> VerificationResult:
    """
    Stub: validate INN format (10 digits for legal entities, 12 for individuals).
    A real integration would call data.egrul.nalog.ru or SPARK.
    """
    if not inn:
        return VerificationResult(ok=False, source="fns", message="ИНН не указан", reason="inn_missing")

    inn = str(inn).strip()
    if not inn.isdigit():
        return VerificationResult(ok=False, source="fns",
                                  message="ИНН содержит недопустимые символы",
                                  reason="inn_format")
    if len(inn) not in (10, 12):
        return VerificationResult(ok=False, source="fns",
                                  message=f"ИНН должен содержать 10 или 12 цифр (получено {len(inn)})",
                                  reason="inn_length")

    # Stub check: first two digits are region code (01–99); 00 is invalid
    if inn[:2] == "00":
        return VerificationResult(ok=False, source="fns",
                                  message="ИНН не найден в реестре ФНС",
                                  reason="inn_not_found")

    return VerificationResult(ok=True, source="fns",
                              message="Компания действует (по данным ФНС)")


# ── 2GIS ─────────────────────────────────────────────────────────────────────

def verify_2gis(address: str | None) -> VerificationResult:
    """
    Stub: check if address looks resolvable.
    A real integration would call api.2gis.com/3.0/items/geocode.
    """
    if not address or len(address.strip()) < 10:
        return VerificationResult(ok=False, source="2gis",
                                  message="Адрес слишком короткий или не указан",
                                  reason="address_missing")

    addr = address.strip().lower()

    # Heuristic: must contain at least a city/street indicator
    has_street = bool(re.search(r'\bул\.?|\bпр\.?|\bпроспект|\bулица|\bпереулок|\bбульвар|\bшоссе|\bнаб\.?', addr))
    has_city   = bool(re.search(r'\bмосква|\bсанкт-петербург|\bспб\b|\bновосибирск|\bекатеринбург|г\.\s*\w', addr))

    if has_street or has_city:
        return VerificationResult(ok=True, source="2gis",
                                  message="Адрес найден в базе 2ГИС")

    return VerificationResult(ok=False, source="2gis",
                              message="Адрес не найден автоматически — требуется ручная проверка",
                              reason="manual_required")


# ── China USCC ───────────────────────────────────────────────────────────────

_USCC_RE = re.compile(r'^[0-9A-HJ-NP-RT-Y]{18}$')

def verify_uscc(
    uscc:           str | None,
    business_scope: str | None,
    founded_year:   int | None,
) -> VerificationResult:
    """
    Stub: validate Chinese Unified Social Credit Code.
    Rules:
      1. Must be exactly 18 alphanumeric chars (excluding I, O, S, V, Z)
      2. business_scope should mention 'manufacturing' / '制造' / 'production'
      3. Company age >= 1 year (current_year - founded_year >= 1)
    """
    if not uscc:
        return VerificationResult(ok=False, source="uscc",
                                  message="USCC не указан", reason="uscc_missing")

    uscc = uscc.strip().upper()

    if not _USCC_RE.match(uscc):
        return VerificationResult(ok=False, source="uscc",
                                  message="Неверный формат USCC (18 символов, латиница/цифры, без I/O/S/V/Z)",
                                  reason="uscc_format")

    # Business scope check
    if business_scope:
        scope = business_scope.lower()
        has_manufacturing = any(kw in scope for kw in (
            "manufacturing", "manufacture", "production", "制造", "生产", "加工",
        ))
        if not has_manufacturing:
            return VerificationResult(ok=False, source="uscc",
                                      message="Business Scope не содержит «manufacturing» / «production»",
                                      reason="scope_mismatch")
    else:
        return VerificationResult(ok=False, source="uscc",
                                  message="Business Scope не указан",
                                  reason="scope_missing")

    # Age check
    if founded_year is not None:
        age = datetime.utcnow().year - founded_year
        if age < 1:
            return VerificationResult(ok=False, source="uscc",
                                      message=f"Компания существует менее 1 года (основана в {founded_year})",
                                      reason="company_too_new")

    return VerificationResult(ok=True, source="uscc",
                              message="USCC верифицирован, производитель действует")


# ── Orchestrator ─────────────────────────────────────────────────────────────

def run_verification(
    country:        str,
    inn:            str | None,
    legal_address:  str | None,
    uscc:           str | None,
    business_scope: str | None,
    founded_year:   int | None,
) -> VerificationResult:
    """Run the appropriate verification chain based on country."""
    if country == "china":
        return verify_uscc(uscc, business_scope, founded_year)

    # Russia: FNS first, then 2GIS on address
    fns = verify_fns(inn)
    if not fns.ok:
        return fns

    gis = verify_2gis(legal_address)
    if not gis.ok and gis.reason == "manual_required":
        # Partial success: FNS passed, 2GIS needs manual review
        return VerificationResult(ok=True, source="manual",
                                  message="ФНС: компания найдена. Адрес требует ручной проверки.")
    if not gis.ok:
        return gis

    return VerificationResult(ok=True, source="fns",
                              message="Компания верифицирована по данным ФНС и 2ГИС")
