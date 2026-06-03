import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from services.sentry_setup import init_sentry
init_sentry()

from database import init_db
from routers import auth, products, reviews, pricing, monitor, finance, legal, startup, assistant, chat, mfa, notifications, success_stories, telegram_settings, supplier_verification, oauth, suppliers_catalog, logistics, deals, supplier_reviews, promo, referrals, marking, ideas, payments, ai_image_service, csv_import, seo_projects, action_engine, rebuild_tracker, seo_intelligence, creative, events, connections, execution, automation, advertising
from routers.ai_image_service import queue_worker as ai_queue_worker
from tasks.health_monitor import run_health_monitor
from tasks.seed_catalog import seed_catalog
from tasks.seed_promos import seed_promos
from tasks.scheduler import run_scheduler
from tasks.intelligence_loop import run_intelligence_loop
import models  # ensure all tables are registered before init_db


# ── Security headers middleware ───────────────────────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"]        = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"]       = "1; mode=block"
        response.headers["Referrer-Policy"]         = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]      = "camera=(), microphone=(), geolocation=()"
        _fe = settings.frontend_url.rstrip("/")
        _api = settings.api_url.rstrip("/")
        _ws = _fe.replace("https://", "wss://").replace("http://", "ws://")
        # Always include localhost for development tooling
        _connect = (
            f"connect-src 'self' {_api} {_fe} {_ws} "
            f"http://localhost:8000 http://localhost:3000 ws://localhost:3000"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            f"{_connect}; "
            "font-src 'self' data:; "
            "frame-ancestors 'none';"
        )
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        return response


# ── App ───────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await seed_catalog()
    await seed_promos()
    monitor_task     = asyncio.create_task(run_health_monitor())
    scheduler_task   = asyncio.create_task(run_scheduler())
    ai_worker_task   = asyncio.create_task(ai_queue_worker())
    intel_loop_task  = asyncio.create_task(run_intelligence_loop())
    yield
    monitor_task.cancel()
    scheduler_task.cancel()
    ai_worker_task.cancel()
    intel_loop_task.cancel()
    for task in (monitor_task, scheduler_task, ai_worker_task, intel_loop_task):
        try:
            await task
        except asyncio.CancelledError:
            pass


_expose_docs = settings.app_env != "production"
app = FastAPI(
    title="Бизнес-Пульт API",
    description="API конкурентной разведки для селлеров маркетплейсов",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs"        if _expose_docs else None,
    redoc_url="/redoc"      if _expose_docs else None,
    openapi_url="/openapi.json" if _expose_docs else None,
)

# Security headers must be added before CORS so they apply to all responses
app.add_middleware(SecurityHeadersMiddleware)

_DEV_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
]
_allowed_origins = list(_DEV_ORIGINS)
if settings.frontend_url and settings.frontend_url not in _allowed_origins:
    _allowed_origins.append(settings.frontend_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router,      prefix="/api/auth",     tags=["auth"])
app.include_router(mfa.router,       prefix="/api/auth/mfa", tags=["mfa"])
app.include_router(products.router,  prefix="/api/products", tags=["products"])
app.include_router(reviews.router,   prefix="/api",          tags=["reviews"])
app.include_router(pricing.router,   prefix="/api",          tags=["pricing"])
app.include_router(monitor.router,   prefix="/api",          tags=["monitor"])
app.include_router(finance.router,   prefix="/api",          tags=["finance"])
app.include_router(legal.router,     prefix="/api",          tags=["legal"])
app.include_router(startup.router,   prefix="/api",          tags=["startup"])
app.include_router(assistant.router, prefix="/api",          tags=["assistant"])
app.include_router(chat.router,          prefix="/api",          tags=["chat"])
app.include_router(notifications.router,       prefix="/api", tags=["notifications"])
app.include_router(success_stories.router,    prefix="/api", tags=["success-stories"])
app.include_router(telegram_settings.router,     prefix="/api", tags=["telegram"])
app.include_router(supplier_verification.router, prefix="/api", tags=["suppliers"])
app.include_router(oauth.router,                 prefix="/api/auth",    tags=["oauth"])
app.include_router(suppliers_catalog.router,     prefix="/api",         tags=["catalog"])
app.include_router(logistics.router,             prefix="/api",         tags=["logistics"])
app.include_router(deals.router,                 prefix="/api",         tags=["deals"])
app.include_router(supplier_reviews.router,      prefix="/api",         tags=["reviews"])
app.include_router(promo.router,                 prefix="/api",         tags=["promo"])
app.include_router(referrals.router,             prefix="/api",         tags=["referrals"])
app.include_router(marking.router,               prefix="/api",         tags=["marking"])
app.include_router(ideas.router,                 prefix="/api/ideas",   tags=["ideas"])
app.include_router(payments.router,              prefix="/api",         tags=["payments"])
app.include_router(ai_image_service.router,      prefix="/api",         tags=["ai-image"])
app.include_router(csv_import.router,            prefix="/api",         tags=["import"])
app.include_router(seo_projects.router,          prefix="/api",         tags=["seo-projects"])
app.include_router(action_engine.router,         prefix="/api",         tags=["action-engine"])
app.include_router(rebuild_tracker.router,       prefix="/api",         tags=["rebuild-tracker"])
app.include_router(seo_intelligence.router,      prefix="/api",         tags=["seo-intelligence"])
app.include_router(creative.router,              prefix="/api",         tags=["creative"])
app.include_router(events.router,               prefix="/api",         tags=["events"])
# ── Marketplace Execution Layer (ME-1) ────────────────────────────────────────
app.include_router(connections.router,           prefix="/api",         tags=["connections"])
app.include_router(execution.router,             prefix="/api",         tags=["execution"])
app.include_router(automation.router,            prefix="/api",         tags=["automation"])
app.include_router(advertising.router,           prefix="/api",         tags=["advertising"])


@app.get("/api/health", tags=["system"])
async def health_check():
    return {"status": "ok", "service": "Бизнес-Пульт API v1.0"}
