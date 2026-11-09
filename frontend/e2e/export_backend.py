"""Local-only browser export fixture. Real auth/export routers, no scheduler or mail."""
import uuid
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config import settings
from database import Base, engine, AsyncSessionLocal
import models
from models.user import User
from models.imported_finance import ImportedFinanceRow
from models.decision import Decision
from routers import auth, data_export
from rate_limit import _limiter

assert settings.app_env == "test"
assert settings.database_url.startswith("sqlite+aiosqlite:///")
fixture_db = Path(settings.database_url.removeprefix("sqlite+aiosqlite:///"))
assert fixture_db.name.startswith("export-browser-")
assert fixture_db.parent.resolve() == (Path(__file__).parent / ".tmp").resolve()
assert not fixture_db.exists(), "fixture database must be new"


@asynccontextmanager
async def lifespan(app):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        for label in ("owner", "other"):
            uid = str(uuid.uuid4())
            db.add(User(id=uid, email=f"export-{label}@example.com", name=label,
                        hashed_password=auth.hash_password("SyntheticExport123!"), is_verified=True))
            await db.flush()
            db.add(ImportedFinanceRow(user_id=uid, import_id=str(uuid.uuid4()), marketplace="ozon",
                                     sku=f"{label}-SKU", revenue=123.5, quantity=2))
            db.add(Decision(user_id=uid, problem=f"{label}-saved-analysis", action="Проверить отчёт"))
        await db.commit()
    yield
    await engine.dispose()


app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:3104"],
                   allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
app.include_router(auth.router, prefix="/api/auth")
app.include_router(data_export.router, prefix="/api")


@app.middleware("http")
async def export_diagnostic(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/data/export/"):
        counts = [len(v) for k, v in _limiter._ts.items() if k.startswith("data-export:")]
        logging.warning("EXPORT_FIXTURE %s %s status=%s counts=%s", request.method,
                        request.url.path, response.status_code, counts)
    return response


@app.get("/health")
async def health():
    return {"fixture": "synthetic-export"}
