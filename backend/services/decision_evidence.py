"""
Decision evidence read model (E1).

One read-only entry point that answers "why does this Decision exist?" by
composing the existing learning pieces into a single record:

  resolve_context_group_for_insight  -> the business context the ranking ran in
  get_ranked_alternatives            -> outcome-memory stats + structured reason

It selects the row for the requested action_key and returns a DecisionEvidence.
No writes, no promotion, no execution, no ranking changes — it only reads and
explains what the ranking already produced.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from services.decision_memory import resolve_context_group_for_insight
from services.ranked_alternatives import get_ranked_alternatives

_SOURCE = "decision_memory"


@dataclass(frozen=True)
class DecisionEvidence:
    """Why a Decision exists: the action, its reason, and the outcome-memory
    stats (in the resolved business context) that ranked it."""
    action_key: str
    reason: str
    context_group: str
    confirmed: int
    refuted: int
    sample: int
    confirmed_rate: Optional[float]
    weighted_rate: Optional[float]
    fallback: bool
    source: str = _SOURCE

    def to_dict(self) -> dict:
        return asdict(self)


async def get_decision_evidence(
    db: AsyncSession, *, user_id: str, insight_key: str, action_key: str,
    listing_id: Optional[str] = None,
) -> Optional[DecisionEvidence]:
    """
    Evidence for one (insight_key, action_key) decision. Resolves the context the
    SAME way GET /alternatives does — resolve_context_group_for_insight with the
    same listing_id — then returns the ranked alternative row for action_key.
    Because it uses the identical context resolution and the identical ranking,
    the returned reason/counts/rates/fallback and context_group are guaranteed to
    match the alternatives surface for the same (insight_key, action_key,
    listing_id). Returns None when the action isn't part of the insight's
    alternatives (malformed or unknown insight/action). Read-only.
    """
    context_group = await resolve_context_group_for_insight(
        db, user_id=user_id, insight_key=insight_key, listing_id=listing_id)
    alternatives = await get_ranked_alternatives(
        db, user_id=user_id, insight_key=insight_key, context_group=context_group)
    match = next((a for a in alternatives if a["action_key"] == action_key), None)
    if match is None:
        return None
    return DecisionEvidence(
        action_key=match["action_key"],
        reason=match["reason"],
        context_group=context_group,
        confirmed=int(match.get("confirmed", 0) or 0),
        refuted=int(match.get("refuted", 0) or 0),
        sample=int(match.get("sample", 0) or 0),
        confirmed_rate=match.get("confirmed_rate"),
        weighted_rate=match.get("weighted_rate"),
        fallback=bool(match.get("fallback", True)),
        source=_SOURCE,
    )
