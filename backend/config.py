import logging
import sys

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

_INSECURE_VALUES = {"change_me", "change_me_use_openssl_rand_hex_32",
                    "dev-secret-key-change-in-production", ""}
# SECURITY-2A — a weak/default SECRET_KEY is tolerated ONLY in these explicit local environments.
# Every other value of APP_ENV (production, staging, beta, or an unknown/misspelled string) is treated
# as non-development and is fail-closed: an unrecognised APP_ENV must never silently become development.
_DEV_ENVS = {"development", "test", "local"}
# Minimum acceptable SECRET_KEY length (chars). `openssl rand -hex 32` yields 64; 32 is the floor. The
# key value itself is NEVER logged or included in any error message — only its length is inspected.
_MIN_SECRET_LEN = 32


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
    # PULT-LAUNCH-2.5F-B: operator-only switch for the advisory observations→protection-evaluation
    # bridge. OFF by default and NOT seller-controlled, no endpoint. The bridge reads proven API
    # price/currency/promotion observations for the protection evaluation ONLY when BOTH this AND
    # api_data_sync_enabled are True; with either false the evaluation runs its unchanged CSV advisory
    # path and issues ZERO observation SELECTs. It is INDEPENDENT of automation_enabled and never
    # unlocks an executable action (promo_price_proven / commission_official_tariff /
    # provider_capability_confirmed stay hard-False regardless).
    protection_use_observations: bool = False

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

    # ── SECURITY-2C-2 — PostgreSQL-atomic auth throttle (multi-worker / restart-safe) ─────────────
    # Three independent dimensions per protected action. LOGIN counts FAILURES only (a success
    # compensates its own increment). identity+IP is the strictest local pair; identity-global is set
    # high so an attacker cannot lock a seller out; IP-global is NAT-aware for password spraying.
    auth_throttle_window_seconds: int = 900          # 15-min sliding window
    auth_throttle_block_seconds: int = 900           # 15-min block once the limit is hit
    auth_throttle_login_pair_limit: int = 5          # (identity+IP)  local, strictest
    auth_throttle_login_identity_limit: int = 20     # identity global — distributed credential stuffing
    auth_throttle_login_ip_limit: int = 50           # IP global — password spraying (NAT-tolerant)
    # register / forgot / resend / reset — every request counted (no success compensation).
    auth_throttle_register_ip_limit: int = 10
    auth_throttle_register_identity_limit: int = 5
    auth_throttle_email_ip_limit: int = 15           # forgot-password + resend-verification per IP
    auth_throttle_email_identity_limit: int = 5      # forgot-password + resend-verification per email
    auth_throttle_reset_ip_limit: int = 20           # reset-password confirm per IP
    # SECURITY-2C-4A — durable TOTP-guess throttle. mfa_login = POST /login/mfa (attacker holds the
    # password); mfa_manage = enable + disable (a cookie-auth attacker guessing TOTP). Separate actions
    # so a management attack cannot lock login and vice-versa. Identity = server-verified user_id.
    auth_throttle_mfa_login_pair_limit: int = 5
    auth_throttle_mfa_login_identity_limit: int = 20
    auth_throttle_mfa_login_ip_limit: int = 50
    auth_throttle_mfa_manage_pair_limit: int = 5     # management is rare → tighter globals than login
    auth_throttle_mfa_manage_identity_limit: int = 10
    auth_throttle_mfa_manage_ip_limit: int = 30
    # opportunistic bounded cleanup: at most one sweep per this interval, deleting a small batch of
    # fully-expired (window + block elapsed) buckets so a random-email attack cannot grow the table.
    auth_throttle_cleanup_interval_seconds: int = 300
    auth_throttle_cleanup_batch: int = 200

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
# SECURITY-2A — resolve the environment once, case-insensitively. An unknown/misspelled APP_ENV is
# NOT a dev env, so it inherits the non-dev fail-closed path below (never a silent downgrade).
_env = (settings.app_env or "").strip().lower()
_is_dev_env = _env in _DEV_ENVS
# A SECRET_KEY is weak if it is a known default/empty OR shorter than the floor. Length only — the value
# is never inspected beyond its length and never printed.
_secret_weak = settings.secret_key in _INSECURE_VALUES or len(settings.secret_key) < _MIN_SECRET_LEN

if not _is_dev_env:
    errors: list[str] = []
    # Secret perimeter — enforced in EVERY non-development environment (production, staging, beta, …).
    if _secret_weak:
        errors.append(
            "SECRET_KEY is missing, a known insecure default, or shorter than "
            f"{_MIN_SECRET_LEN} chars — set a strong random value (openssl rand -hex 32)")
    if not settings.cred_enc_key:
        errors.append("CRED_ENC_KEY is not set — required to encrypt marketplace tokens at rest")
    # Operational launch checks are production-specific (a staging/beta box may legitimately differ on
    # database / SMTP / host); the secret checks above already cover every non-dev environment.
    if settings.app_env == "production":
        if "sqlite" in settings.database_url:
            errors.append("DATABASE_URL points to SQLite — use PostgreSQL in production")
        if not settings.smtp_host:
            errors.append("SMTP_HOST is not set — email verification / password reset cannot be delivered")
        if "localhost" in settings.frontend_url or "127.0.0.1" in settings.frontend_url:
            # A localhost FRONTEND_URL makes every verification / password-reset link a dead 404,
            # so no seller can complete signup. This is a launch showstopper, not an advisory — fail loud.
            errors.append("FRONTEND_URL points to localhost — verification / reset links would 404 for every seller")
    if errors:
        for e in errors:
            logger.critical("[ПУЛЬТ] CONFIG ERROR (app_env=%s): %s", settings.app_env, e)
        sys.exit(
            "[ПУЛЬТ] Startup aborted — this is a non-development environment. "
            "Fix the config errors below (no secret value is shown):\n  - "
            + "\n  - ".join(errors)
        )
    # Non-fatal advisories — the app runs, but these should be set.
    if not settings.sentry_dsn:
        logger.warning("⚠️  SENTRY_DSN not set — error tracking disabled.")
    if not settings.yookassa_secret_key:
        logger.warning("⚠️  YOOKASSA_SECRET_KEY not set — payments will fail until configured.")

# ── Development / test warnings (a weak default is tolerated ONLY here) ───────
if _is_dev_env and _secret_weak:
    logger.warning(
        "⚠️  SECRET_KEY uses an insecure/short default — allowed only in development/test. "
        "Set a strong value before any deploy: openssl rand -hex 32"
    )
if "sqlite" in settings.database_url and not _is_dev_env:
    logger.warning(
        "⚠️  SQLite is not suitable outside development. "
        "Set DATABASE_URL to postgresql+asyncpg://..."
    )
