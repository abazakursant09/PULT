"""
Analytics router (Slice 5: read-only decision-effect aggregation).

Exposes pure DecisionOutcome statistics. Read-only: no writes, no execution, no
measurement. Scoped to the authenticated seller — the `user_id` is taken from
the session, NOT from the query string, to avoid cross-tenant reads (IDOR).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User
from services import decision_effect_aggregator as agg
from services import decision_recommendation_engine as rec
from services import decision_candidate_engine as cand
from services import decision_policy_engine as policy
from services import execution_orchestrator as orch

router = APIRouter()


@router.get("/analytics/decision-effects")
async def decision_effects(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    uid = current_user.id
    return {
        "decision_summary": await agg.get_decision_summary(db, uid),
        "action_performance": await agg.get_action_performance(db, uid),
        "insight_effectiveness": await agg.get_insight_effectiveness(db, uid),
    }


@router.get("/analytics/recommendations")
async def recommendations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    uid = current_user.id
    return {"recommendations": await rec.generate_recommendations(db, uid)}


@router.get("/analytics/decision-candidates")
async def decision_candidates(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    uid = current_user.id
    return {"decision_candidates": await cand.generate_decision_candidates(db, uid)}


@router.get("/analytics/decision-policy")
async def decision_policy(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    uid = current_user.id
    candidates = await cand.generate_decision_candidates(db, uid)
    return await policy.apply_decision_policy(db, uid, candidates)


@router.get("/analytics/execution-plan")
async def execution_plan(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await orch.build_execution_plan(db, current_user.id)
