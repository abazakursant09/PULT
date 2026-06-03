from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from config import settings

engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

async def init_db():
    """
    Bring the schema to Alembic head on startup (dev and prod alike).

    Alembic is the single source of schema truth (schema_governance.md). The old
    create_all + hand-maintained ALTER list is GONE: it could not evolve existing
    tables and silently masked drift, which is exactly how review_responses ended
    up missing columns and 500'ing at runtime. Now: new model -> new migration ->
    startup applies it automatically, no manual step.

    Multi-worker production should migrate at deploy time and set AUTO_MIGRATE=0
    to avoid concurrent startup upgrades; the runner is otherwise idempotent.
    """
    from db_migrations import run_migrations
    await run_migrations()
