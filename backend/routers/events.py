"""
Operational event tracking — /api/events/*
Lightweight fire-and-forget behavioral intelligence.
Never raises to the caller; all errors are swallowed.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user, get_current_user_optional
from models.user import User
from models.user_event import UserEvent

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Allowed event types (ignore unknown keys silently) ────────────────────────

_ALLOWED = {
    "copilot_cta_clicked",
    "copilot_dismissed",
    "copilot_snoozed",
    "insight_opened",
    "insight_resolved",
    "insight_snoozed",
    "seo_rebuild_started",
    "seo_rebuild_completed",
    "creative_variant_applied",
    "import_started",
    "import_completed",
    "import_failed",
    "import_timeout_seen",
    "retry_clicked",
    "dashboard_opened",
    "action_engine_opened",
    "action_engine_error_seen",
    "rebuild_failed",
    "rebuild_retry",
    "upload_timeout_seen",
    # ── Funnel / CRO events (Analytics Recovery Sprint) ──
    "landing_page_viewed",
    "section_viewed",
    "landing_hero_cta_clicked",
    "landing_demo_clicked",
    "registration_started",
    "registration_completed",
    "trial_started",
    "first_insight_shown",
    "subscription_started",
}

# Events accepted WITHOUT authentication. Landing events are anonymous by
# nature; registration events fire pre-auth (no token yet on the email path).
# All other events require a logged-in user. Anonymous rows are keyed by
# visitor_id so the funnel can be stitched to the user later.
_ANON_ALLOWED = {
    "landing_page_viewed",
    "section_viewed",
    "landing_hero_cta_clicked",
    "landing_demo_clicked",
    "registration_started",
    "registration_completed",
    "trial_started",
}

# ── Schemas ───────────────────────────────────────────────────────────────────

class TrackRequest(BaseModel):
    event_type:  str
    event_scope: str = "unknown"
    entity_id:   Optional[str] = None
    visitor_id:  Optional[str] = None
    metadata:    Optional[dict[str, Any]] = None


class DigestResponse(BaseModel):
    period_days:        int
    opened_insights:    int
    resolved_insights:  int
    dismissed_insights: int
    ignored_insights:   int
    rebuild_completions: int
    import_failures:    int
    retry_frequency:    float
    resolution_rate:    float
    dismiss_rate:       float
    retry_rate:         float


# ── Track endpoint ────────────────────────────────────────────────────────────

@router.post("/events/track", status_code=204)
async def track_event(
    body: TrackRequest,
    user: Optional[User] = Depends(get_current_user_optional),
    db:   AsyncSession   = Depends(get_db),
) -> Response:
    if body.event_type not in _ALLOWED:
        return Response(status_code=204)

    # Resolve identity. Authed → real user_id. Anonymous → only whitelisted
    # pre-auth events, keyed by visitor_id (UUID, ≤36 chars) for later stitching.
    if user is not None:
        uid = str(user.id)
    elif body.event_type in _ANON_ALLOWED:
        uid = (body.visitor_id or "anon")[:36]
    else:
        return Response(status_code=204)  # auth-only event without auth → drop

    try:
        meta_obj = dict(body.metadata or {})
        if body.visitor_id:
            meta_obj["visitor_id"] = body.visitor_id
        meta = json.dumps(meta_obj, ensure_ascii=False) if meta_obj else None
        db.add(UserEvent(
            user_id       = uid,
            event_type    = body.event_type,
            event_scope   = body.event_scope[:64],
            entity_id     = body.entity_id,
            metadata_json = meta,
        ))
        await db.commit()
    except Exception as exc:
        logger.warning("event_track_failed", extra={"user_id": uid, "event": body.event_type, "error": str(exc)})

    # Sprint 81: a new UserEvent automatically reaches the constitutional substrate
    # through the runtime_binding adapter (deterministic, read-only, fail-closed).
    # Never mutates the DB; never affects the 204 response.
    try:
        from runtime_binding import activate_from_track
        activate_from_track(body.event_type, body.entity_id, body.metadata)
    except Exception:
        pass

    return Response(status_code=204)


# ── Digest endpoint ───────────────────────────────────────────────────────────

@router.get("/events/digest", response_model=DigestResponse)
async def events_digest(
    days: int          = 7,
    user: User         = Depends(get_current_user),
    db:   AsyncSession = Depends(get_db),
) -> DigestResponse:
    uid   = str(user.id)
    since = datetime.utcnow() - timedelta(days=max(1, min(days, 90)))

    rows = await db.execute(
        select(UserEvent.event_type, func.count(UserEvent.id).label("cnt"))
        .where(UserEvent.user_id == uid, UserEvent.created_at >= since)
        .group_by(UserEvent.event_type)
    )
    counts: dict[str, int] = {r.event_type: r.cnt for r in rows}

    def c(*keys: str) -> int:
        return sum(counts.get(k, 0) for k in keys)

    opened    = c("insight_opened")
    resolved  = c("insight_resolved")
    dismissed = c("copilot_dismissed", "insight_snoozed", "copilot_snoozed")
    failed    = c("import_failed", "rebuild_failed", "action_engine_error_seen")
    retries   = c("retry_clicked", "rebuild_retry")
    total     = sum(counts.values()) or 1

    return DigestResponse(
        period_days        = days,
        opened_insights    = opened,
        resolved_insights  = resolved,
        dismissed_insights = dismissed,
        ignored_insights   = max(0, opened - resolved - dismissed),
        rebuild_completions = c("seo_rebuild_completed"),
        import_failures    = c("import_failed"),
        retry_frequency    = round(retries / total, 3),
        resolution_rate    = round(resolved / opened, 3) if opened else 0.0,
        dismiss_rate       = round(dismissed / (opened or 1), 3),
        retry_rate         = round(retries / (failed or 1), 3),
    )
