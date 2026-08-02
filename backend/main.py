import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from config import settings
from csrf import OriginCsrfMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from services.sentry_setup import init_sentry
init_sentry()

from database import init_db
from routers import auth, products, reviews, pricing, monitor, finance, legal, startup, assistant, mfa, notifications, success_stories, telegram_settings, supplier_verification, suppliers_catalog, logistics, deals, supplier_reviews, promo, referrals, marking, ideas, payments, ai_image_service, csv_import, seo_projects, action_engine, rebuild_tracker, seo_intelligence, creative, events, connections, marketplace_accounts, store_catalog, source_policy, execution, automation, advertising, seo_execution, product_graph, decisions, analytics, learning, seo, advertising_engine, review_engine, growth_engine, legal_engine, decision_outcome, decision_feed, decision_apply, promotion_activation, today, presentation
from routers.ai_image_service import queue_worker as ai_queue_worker
from tasks.health_monitor import run_health_monitor
from tasks.seed_catalog import seed_catalog
from tasks.seed_promos import seed_promos
from tasks.scheduler import run_scheduler
from tasks.intelligence_loop import run_intelligence_loop
import models  # ensure all tables are registered before init_db


# ── Security headers middleware ───────────────────────────────────────────────

_HEADER_DEV_ENVS = {"development", "test", "local"}


def _is_dev_env() -> bool:
    return (settings.app_env or "").strip().lower() in _HEADER_DEV_ENVS


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        is_dev = _is_dev_env()
        response.headers["X-Frame-Options"]        = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"]       = "1; mode=block"
        response.headers["Referrer-Policy"]         = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]      = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        # SECURITY-2B-3 — CSP for API RESPONSES (the frontend HTML CSP is set by Next, next.config.js).
        # Prod: API returns JSON only → lock it right down. Dev: Swagger /docs (dev-only) needs inline.
        if is_dev:
            _fe = settings.frontend_url.rstrip("/")
            _api = settings.api_url.rstrip("/")
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                f"img-src 'self' data:; connect-src 'self' {_api} {_fe} http://localhost:8000; "
                "font-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none';"
            )
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
                "object-src 'none'; form-action 'none'"
            )
            # HSTS only for a real HTTPS deployment; never asserted over local http.
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        # Never let a shared proxy / browser cache an API response — they carry Set-Cookie or per-seller
        # / financial JSON. Public reference endpoints lose CDN caching, an acceptable trade for safety.
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
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
# In production the allow-list is ONLY the configured frontend origin. The localhost origins
# were included unconditionally, and with allow_credentials=True that let a page a victim opened
# on their own localhost:3000 make credentialed cross-origin calls to the prod API. Outside
# production they stay, so the local dev workflow is unchanged.
_allowed_origins: list[str] = [] if settings.app_env == "production" else list(_DEV_ORIGINS)
if settings.frontend_url and settings.frontend_url not in _allowed_origins:
    _allowed_origins.append(settings.frontend_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    # SECURITY-2B-2 — only the methods/headers the browser client actually uses. Authorization is gone
    # (the session is a cookie now); an unknown Origin still fails the allow_origins allowlist above.
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)

# SECURITY-2B-2 — server-side Origin/Referer CSRF guard for cookie-authenticated mutations. Added after
# CORS so it is the OUTER layer for real requests; the CORS preflight OPTIONS is never a state method,
# so it passes through untouched.
app.add_middleware(OriginCsrfMiddleware)

app.include_router(auth.router,      prefix="/api/auth",     tags=["auth"])
app.include_router(mfa.router,       prefix="/api/auth/mfa", tags=["mfa"])
app.include_router(products.router,  prefix="/api/products", tags=["products"])
# review_engine BEFORE reviews: its explicit /reviews/{overview,signals,...}
# must win over reviews' catch-all GET /reviews/{product_id}.
app.include_router(review_engine.router, prefix="/api",      tags=["review-engine"])
app.include_router(reviews.router,   prefix="/api",          tags=["reviews"])
app.include_router(pricing.router,   prefix="/api",          tags=["pricing"])
app.include_router(monitor.router,   prefix="/api",          tags=["monitor"])
app.include_router(finance.router,   prefix="/api",          tags=["finance"])
app.include_router(legal.router,     prefix="/api",          tags=["legal"])
app.include_router(startup.router,   prefix="/api",          tags=["startup"])
app.include_router(assistant.router, prefix="/api",          tags=["assistant"])
app.include_router(notifications.router,       prefix="/api", tags=["notifications"])
app.include_router(success_stories.router,    prefix="/api", tags=["success-stories"])
app.include_router(telegram_settings.router,     prefix="/api", tags=["telegram"])
app.include_router(supplier_verification.router, prefix="/api", tags=["suppliers"])
# OAuth router DISABLED (P7.1) — was an account-takeover stub; not mounted.
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
app.include_router(marketplace_accounts.router,  prefix="/api",         tags=["marketplace-accounts"])
# Read-only store catalog (1.4.5B): products and import history of ONE store.
app.include_router(store_catalog.router,          prefix="/api",         tags=["store-catalog"])
# Source policy (1.4.5H): per (store, metric) API-vs-CSV preference. Backend contract for 1.4.5I.
app.include_router(source_policy.router,          prefix="/api",         tags=["source-policy"])
app.include_router(execution.router,             prefix="/api",         tags=["execution"])
app.include_router(automation.router,            prefix="/api",         tags=["automation"])
app.include_router(advertising.router,           prefix="/api",         tags=["advertising"])
app.include_router(seo_execution.router,          prefix="/api",         tags=["seo-execution"])
# ── Product Graph (Doctrine §3/§7) — read-only ────────────────────────────────
app.include_router(product_graph.router,         prefix="/api/product-graph", tags=["product-graph"])
# ── Decisions apply bridge (Slice D) ──────────────────────────────────────────
app.include_router(decisions.router,             prefix="/api",         tags=["decisions"])
# ── Decision-effect aggregation (Slice 5, read-only) ──────────────────────────
app.include_router(analytics.router,             prefix="/api",         tags=["analytics"])
# ── Learning ranked-alternatives read API (L6, read-only) ─────────────────────
app.include_router(learning.router,              prefix="/api",         tags=["learning"])
# ── SEO Engine read/trigger API (A7) ──────────────────────────────────────────
app.include_router(seo.router,                   prefix="/api",         tags=["seo"])
# ── Advertising Engine read/trigger API (A7) ──────────────────────────────────
app.include_router(advertising_engine.router,    prefix="/api",         tags=["advertising-engine"])
app.include_router(growth_engine.router,          prefix="/api",         tags=["growth-engine"])
app.include_router(legal_engine.router,           prefix="/api",         tags=["legal-engine"])
app.include_router(decision_outcome.router,       prefix="/api",         tags=["decision-outcome"])
app.include_router(decision_feed.router,          prefix="/api",         tags=["decision-feed"])
app.include_router(decision_apply.router,         prefix="/api",         tags=["decision-apply"])
app.include_router(promotion_activation.router,   prefix="/api",         tags=["promotion-activation"])
app.include_router(today.router,                  prefix="/api",         tags=["today"])
# ── Presentation Intelligence read API (P4, additive, read-only) ──────────────
app.include_router(presentation.router,           prefix="/api",         tags=["presentation"])


@app.get("/api/health", tags=["system"])
async def health_check():
    return {"status": "ok", "service": "Бизнес-Пульт API v1.0"}
