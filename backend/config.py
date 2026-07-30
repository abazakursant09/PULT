import logging
import sys

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

_INSECURE_VALUES = {"change_me", "change_me_use_openssl_rand_hex_32",
                    "dev-secret-key-change-in-production", ""}


class Settings(BaseSettings):
    # ── App ──────────────────────────────────────────────────────────────────
    app_env: str = "development"

    # ── Security ─────────────────────────────────────────────────────────────
    secret_key: str = "dev-secret-key-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./business_pult.db"
    redis_url: str = "redis://localhost:6379"
    # Apply `alembic upgrade head` on startup. Default on (dev + single-process
    # prod). Set AUTO_MIGRATE=0 for multi-worker prod that migrates at deploy.
    auto_migrate: bool = True

    # ── URLs ─────────────────────────────────────────────────────────────────
    frontend_url: str = "http://localhost:3000"
    api_url: str = "http://localhost:8000"

    # ── Telegram ─────────────────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_admin_chat_id: str = ""

    # ── VK ───────────────────────────────────────────────────────────────────
    vk_api_token: str = ""

    # ── Supplier verification ─────────────────────────────────────────────────
    dgis_api_key: str = ""
    fns_api_key: str = ""

    # ── Nano Banana AI ────────────────────────────────────────────────────────
    nano_banana_api_key: str = ""

    # ── Email (transactional: verification + password reset) ──────────────────
    # If smtp_host is empty the email service logs the message instead of sending
    # (development). In production set these so verification/reset links are
    # delivered by email — tokens are NEVER returned in API responses (P7.1).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True

    # ── Error tracking (Sentry) ───────────────────────────────────────────────
    # If set, backend errors are reported to Sentry. If empty, tracking is a
    # graceful no-op — the app runs normally either way.
    sentry_dsn: str = ""

    # ── YooKassa ──────────────────────────────────────────────────────────────
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    yookassa_return_url: str = ""

    # ── Marketplace Execution Layer (ME-1) ────────────────────────────────────
    # Fernet key (urlsafe-base64, 32 bytes) used to encrypt marketplace API
    # tokens at rest. If empty, a key is derived from secret_key for development
    # only (NOT production — set CRED_ENC_KEY explicitly).
    cred_enc_key: str = ""
    wb_feedbacks_base: str = "https://feedbacks-api.wildberries.ru"
    wb_prices_base: str = "https://discounts-prices-api.wildberries.ru"
    wb_content_base: str = "https://content-api.wildberries.ru"
    wb_advert_base: str = "https://advert-api.wildberries.ru"
    wb_statistics_base: str = "https://statistics-api.wildberries.ru"
    wb_analytics_base: str = "https://seller-analytics-api.wildberries.ru"
    wb_finance_base: str = "https://finance-api.wildberries.ru"
    # PULT-LAUNCH-2.5D-WB-B — Календарь акций lives on its OWN host (dp-calendar-api), not
    # discounts-prices-api; the promotion reads use it. Same "Цены и скидки" token category.
    wb_calendar_base: str = "https://dp-calendar-api.wildberries.ru"
    ozon_seller_base: str = "https://api-seller.ozon.ru"
    ozon_performance_base: str = "https://api-performance.ozon.ru"
    yandex_partner_base: str = "https://api.partner.market.yandex.ru"
    marketplace_http_timeout: float = 15.0
    # PULT-LAUNCH-1.4.5E: master switch for API data ingestion (WB provider + scheduler). OFF by
    # default and NOT seller-controlled. While false, run_api_sync makes ZERO marketplace calls, so
    # a verified connection stores no data yet — the honest state until the source policy (1.4.5H)
    # decides how API and CSV data combine. Nothing tells a seller "данные синхронизированы" here.
    api_data_sync_enabled: bool = False
    # Freshness TTL (hours) for API SNAPSHOT metrics (price/stock/catalog/rating). A snapshot may be
    # chosen as an API source only if its last successful sync is within this window; otherwise the
    # source resolver falls back to CSV and flags the API data 'stale'. Period metrics
    # (revenue/fees/returns) use ApiSyncState.coverage_complete instead, never this TTL. Operator
    # setting, not seller-facing (PULT-LAUNCH-1.4.5H).
    api_snapshot_freshness_hours: int = 48
    # Master switch for the L4 automation scheduler. Off by default — L4 actions
    # only fire when this is on AND a per-user AutomationRule is enabled.
    automation_enabled: bool = False
    # PULT-LAUNCH-2.5E-2: master switch for observation-history retention (change-only price/promotion
    # observations). OFF by default and NOT seller-controlled. It is INDEPENDENT of api_data_sync_enabled
    # and automation_enabled and, on its own, starts NO work: no cleanup service, no DELETE, no scheduler
    # tick. It only gates the retention sweep. While false, nothing prunes any row.
    observation_retention_enabled: bool = False
    # PULT-LAUNCH-2.5E-3: operator-only dry-run switch for the retention sweep. Default True (fail-safe):
    # even if observation_retention_enabled is accidentally turned on, the sweep only COUNTS candidates
    # and deletes NOTHING until an operator explicitly sets this False. NOT seller-controlled, no endpoint.
    observation_retention_dry_run: bool = True

    # How many products one connection's review auto-sync processes per scheduler cycle. A conservative
    # technical default (NOT an official WB/Ozon rate limit) that bounds the request burst; the cursor
    # advances by this many products each 15-min sync so a large store is covered over several cycles
    # without ever firing all its fetches at once. Tunable via env after live validation, no migration.
    review_sync_product_batch_size: int = 20

    # Shared secret for internal/cron-only control endpoints (e.g. the
    # measurement close-due trigger). Empty by default → those endpoints are
    # fail-closed (reject every caller) until an operator sets INTERNAL_API_KEY.
    internal_api_key: str = ""

    # Number of trusted reverse proxies in front of the app. The client IP used by
    # every rate limiter and the registration IP cap is read this many hops from the
    # RIGHT of X-Forwarded-For — the entries our own proxies appended, which a client
    # cannot forge. 0 (the default) means "no trusted proxy": X-Forwarded-For is
    # ignored entirely and the direct peer address is used. This fails safe — an
    # attacker who sets X-Forwarded-For gains nothing until an operator declares how
    # many real proxies exist (behind one Caddy/nginx, set TRUSTED_PROXY_COUNT=1).
    trusted_proxy_count: int = 0

    model_config = {"env_file": ".env"}


settings = Settings()

# Auto-derive yookassa_return_url if not set explicitly
if not settings.yookassa_return_url:
    settings.yookassa_return_url = f"{settings.frontend_url.rstrip('/')}/payment/result"

# ── Production hard-fail (fail fast when a critical secret is missing) ─────────
# Critical = the Advisory MVP cannot run safely without it:
#   SECRET_KEY   — signs auth JWTs
#   DATABASE_URL — must be a real (PostgreSQL) database, not SQLite
#   CRED_ENC_KEY — encrypts stored marketplace tokens at rest (else RuntimeError)
#   SMTP_HOST    — delivers email verification / password-reset links (P7.1)
if settings.app_env == "production":
    errors: list[str] = []
    if settings.secret_key in _INSECURE_VALUES:
        errors.append("SECRET_KEY is not set or uses insecure default (openssl rand -hex 32)")
    if "sqlite" in settings.database_url:
        errors.append("DATABASE_URL points to SQLite — use PostgreSQL in production")
    if not settings.cred_enc_key:
        errors.append("CRED_ENC_KEY is not set — required to encrypt marketplace tokens at rest")
    if not settings.smtp_host:
        errors.append("SMTP_HOST is not set — email verification / password reset cannot be delivered")
    if "localhost" in settings.frontend_url or "127.0.0.1" in settings.frontend_url:
        # A localhost FRONTEND_URL makes every verification / password-reset link a dead 404,
        # so no seller can complete signup. This is a launch showstopper, not an advisory — fail loud.
        errors.append("FRONTEND_URL points to localhost — verification / reset links would 404 for every seller")
    if errors:
        for e in errors:
            logger.critical("[ПУЛЬТ] PRODUCTION CONFIG ERROR: %s", e)
        sys.exit(
            "[ПУЛЬТ] Startup aborted. Fix production config errors:\n  - "
            + "\n  - ".join(errors)
        )
    # Non-fatal production advisories — the app runs, but these should be set.
    if not settings.sentry_dsn:
        logger.warning("⚠️  SENTRY_DSN not set — error tracking disabled in production.")
    if not settings.yookassa_secret_key:
        logger.warning("⚠️  YOOKASSA_SECRET_KEY not set — payments will fail until configured.")

# ── Development / staging warnings ───────────────────────────────────────────
if settings.secret_key in _INSECURE_VALUES:
    logger.warning(
        "⚠️  SECRET_KEY uses insecure default. "
        "Set a strong value: openssl rand -hex 32"
    )
if "sqlite" in settings.database_url and settings.app_env != "development":
    logger.warning(
        "⚠️  SQLite is not suitable for production. "
        "Set DATABASE_URL to postgresql+asyncpg://..."
    )
