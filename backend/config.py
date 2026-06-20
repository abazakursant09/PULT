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
    ozon_seller_base: str = "https://api-seller.ozon.ru"
    ozon_performance_base: str = "https://api-performance.ozon.ru"
    marketplace_http_timeout: float = 15.0
    # Master switch for the L4 automation scheduler. Off by default — L4 actions
    # only fire when this is on AND a per-user AutomationRule is enabled.
    automation_enabled: bool = False

    # Shared secret for internal/cron-only control endpoints (e.g. the
    # measurement close-due trigger). Empty by default → those endpoints are
    # fail-closed (reject every caller) until an operator sets INTERNAL_API_KEY.
    internal_api_key: str = ""

    model_config = {"env_file": ".env"}


settings = Settings()

# Auto-derive yookassa_return_url if not set explicitly
if not settings.yookassa_return_url:
    settings.yookassa_return_url = f"{settings.frontend_url.rstrip('/')}/payment/result"

# ── Production hard-fail ──────────────────────────────────────────────────────
if settings.app_env == "production":
    errors: list[str] = []
    if settings.secret_key in _INSECURE_VALUES:
        errors.append("SECRET_KEY is not set or uses insecure default")
    if "sqlite" in settings.database_url:
        errors.append("DATABASE_URL points to SQLite — use PostgreSQL in production")
    if errors:
        for e in errors:
            logger.critical("[ПУЛЬТ] PRODUCTION CONFIG ERROR: %s", e)
        sys.exit(
            "[ПУЛЬТ] Startup aborted. Fix production config errors:\n  - "
            + "\n  - ".join(errors)
        )

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
