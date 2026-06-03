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
