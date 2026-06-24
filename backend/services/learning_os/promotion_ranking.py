"""
Learning OS v2 — observed ranking of margin alternatives at promotion time.

The Insight→Decision bridge MUST NOT learn (architecture guard). So the bridge
exposes an injected `action_order` and this learning-aware wrapper computes that
order from PROVEN, marketplace-isolated outcomes (rank_action_keys_by_observed)
and hands it down.

SORT-ONLY: every declared alternative is still promoted (none dropped). The order
reflects observed counts for THIS marketplace only — never another marketplace as
fallback, never a score/probability/forecast. Sub-sample / no history → the bridge's
deterministic action-space order is preserved.
"""
from __future__ import annotations

from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from services.insight_decision_bridge import (
    promote_insight_alternatives, emit_candidates, InsightPromotionDTO, PromoteResult,
)
from services.learning_os.registry import rank_action_keys_by_observed


async def promote_alternatives_observed_ranked(
    db: AsyncSession, *, user_id: str, insight: InsightPromotionDTO,
    context_group: Optional[str] = None, min_sample: int = 10,
) -> List[PromoteResult]:
    """Promote the insight's declared alternatives, ordered by observed outcomes for
    the insight's marketplace. Same promotions as promote_insight_alternatives — only
    the order changes when there is enough proven history."""
    candidates = emit_candidates(insight.insight_key)
    action_order: Optional[List[str]] = None
    if len(candidates) > 1:
        action_order = await rank_action_keys_by_observed(
            db, user_id=user_id, marketplace=candidates[0].marketplace,
            action_keys=[c.action_key for c in candidates], min_sample=min_sample)
    return await promote_insight_alternatives(
        db, user_id=user_id, insight=insight, context_group=context_group,
        action_order=action_order)
