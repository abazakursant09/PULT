from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import text
from config import settings

engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

async def init_db():
    # ── Production: schema lifecycle is migration-driven (Sprint 69) ──────────
    # Prod schema is applied via `alembic upgrade head` at deploy time.
    # We must NOT run create_all in production: it cannot evolve an existing
    # schema (no ALTER) and would silently mask drift. See
    # docs/governance/schema_governance.md.
    if settings.app_env == "production":
        return

    # ── Development / staging: keep zero-friction local bootstrap ─────────────
    # Alembic remains the source of truth; alternatively run `alembic upgrade
    # head`. create_all + the legacy in-place column patches below keep existing
    # local SQLite databases working without a manual migration step.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Safe migrations for existing databases (SQLite ignores duplicate columns)
        for stmt in [
            "ALTER TABLE users ADD COLUMN plan VARCHAR(50) NOT NULL DEFAULT 'master'",
            "ALTER TABLE users ADD COLUMN chat_violations INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN chat_blocked BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE legal_cases ADD COLUMN review_id VARCHAR(36)",
            # Stage 9: email verification and password reset (DEFAULT 1 keeps existing users verified)
            "ALTER TABLE users ADD COLUMN is_verified BOOLEAN NOT NULL DEFAULT 1",
            "ALTER TABLE users ADD COLUMN verification_token VARCHAR(255)",
            "ALTER TABLE users ADD COLUMN reset_token VARCHAR(255)",
            "ALTER TABLE users ADD COLUMN reset_token_expires DATETIME",
            # Stage 11: Telegram integration
            "ALTER TABLE users ADD COLUMN telegram_chat_id VARCHAR(100)",
            # Stage 25: Referral system + soft delete
            "ALTER TABLE users ADD COLUMN referral_code VARCHAR(20)",
            "ALTER TABLE users ADD COLUMN referred_by_id VARCHAR(36)",
            "ALTER TABLE users ADD COLUMN deleted_at DATETIME",
            "ALTER TABLE users ADD COLUMN was_referrer BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN was_referred BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN is_restored BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN registered_ip VARCHAR(45)",
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_referral_code ON users (referral_code)",
            # Stage 28: YooKassa payments
            "ALTER TABLE users ADD COLUMN subscription_end_date DATETIME",
            # Stage 26: Telegram Intelligence Loop
            "ALTER TABLE telegram_settings ADD COLUMN notify_insights BOOLEAN NOT NULL DEFAULT 1",
            "ALTER TABLE telegram_settings ADD COLUMN notify_seo_opportunity BOOLEAN NOT NULL DEFAULT 1",
            "ALTER TABLE telegram_settings ADD COLUMN notify_sales_growth BOOLEAN NOT NULL DEFAULT 1",
            "ALTER TABLE telegram_settings ADD COLUMN notify_retention BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE telegram_settings ADD COLUMN retention_inactive_days INTEGER NOT NULL DEFAULT 3",
            # Stage 27-29: Rebuild Tracker + Weekly Intelligence Report
            "ALTER TABLE telegram_settings ADD COLUMN notify_weekly_report BOOLEAN NOT NULL DEFAULT 1",
            "ALTER TABLE telegram_settings ADD COLUMN notify_ab_results BOOLEAN NOT NULL DEFAULT 1",
            # Stage 31: SEO Intelligence indexes
            "CREATE INDEX IF NOT EXISTS ix_seo_rebuild_user_mp_cat ON seo_rebuilds (user_id, marketplace, category)",
            "CREATE INDEX IF NOT EXISTS ix_seo_rebuild_user_created ON seo_rebuilds (user_id, created_at)",
            # Stage 31 Part 2: Creative Variants
            "CREATE INDEX IF NOT EXISTS ix_creative_variants_user ON creative_variants (user_id)",
            "CREATE INDEX IF NOT EXISTS ix_creative_variants_session ON creative_variants (session_id)",
            # Product Spine Step 1: canonical product_id on import tables (FK enforced via migration on prod)
            "ALTER TABLE imported_product_rows ADD COLUMN product_id VARCHAR(36)",
            "ALTER TABLE imported_finance_rows ADD COLUMN product_id VARCHAR(36)",
            "CREATE INDEX IF NOT EXISTS ix_imp_product_product_id ON imported_product_rows (product_id)",
            "CREATE INDEX IF NOT EXISTS ix_imp_finance_product_id ON imported_finance_rows (product_id)",
        ]:
            try:
                await conn.execute(text(stmt))
            except Exception:
                pass
