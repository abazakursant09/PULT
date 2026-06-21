"""
Engine Effect Observation (Decision Outcome data foundation, A2).

The append-only record of what was ACTUALLY OBSERVED after a decision was executed —
the proof layer of the loop. A2 is SCHEMA ONLY: no measurement run, no close
bridge, no aggregation.

Doctrine — PROVEN result only:
  * effect_band is QUALITATIVE: improved | unchanged | worsened | not_evaluated.
  * NO score, NO forecast, NO ROI prediction, NO money projection — only a band
    plus the raw observed metric stored in `evidence` once measurement actually
    runs (later sprint). Before that, effect_band stays not_evaluated.
  * `not_evaluated` is honest absence of proof, never "no effect" / "success".

Strictly append-only: one row per (link, metric, window) observation, never
rewritten — no `updated_at`. `baseline_captured_at` / `measured_at` are DATA
fields (when the before/after snapshots were taken), not mutation stamps.
Marketplace-agnostic: soft refs only.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, Text, DateTime, Index
from database import Base


class EngineEffectObservation(Base):
    __tablename__ = "engine_effect_observation"

    id                  = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    link_id             = Column(String(36), nullable=False)   # soft ref → engine_signal_decision_link.id
    user_id             = Column(String(36), nullable=False)   # soft ref → users.id
    insight_key         = Column(String(80), nullable=False)   # denormalized for fast lookup

    metric_key          = Column(String(40), nullable=False)   # what is being observed (no number here)
    window_days         = Column(Integer, nullable=True)       # observation window length
    baseline_captured_at = Column(DateTime, nullable=True)     # when the "before" snapshot was taken
    measured_at         = Column(DateTime, nullable=True)      # when the "after" snapshot was taken

    # qualitative ONLY — no score, no forecast, no money
    effect_band         = Column(String(15), nullable=False, default="not_evaluated",
                                 server_default="not_evaluated")  # improved|unchanged|worsened|not_evaluated
    evidence            = Column(Text, nullable=True)          # JSON observed facts (filled on real measurement)

    created_at          = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_engine_effect_link", "link_id"),
        Index("ix_engine_effect_insight", "insight_key"),
        Index("ix_engine_effect_band", "effect_band"),
    )
