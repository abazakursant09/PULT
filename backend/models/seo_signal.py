"""
SEO Signal (SEO Engine data foundation, A2) — seller-facing decision record.

The PULT-doctrine view of an SEO problem: what / why / meaning / what_to_do /
expected_effect, plus the canonical recommended action. `insight_key`
(`seo_<problem_type>:<marketplace>:<sku>`) anchors a future promote into the
existing Decision Spine (insight→decision bridge), and `marketplace` is the same
context dimension Learning OS uses — so SEO plugs into both without new infra.

Plain table mirror, no signal-building logic. Marketplace-agnostic. A2 stores
signals append-only (no `updated_at`); `status` is set at insert. The lifecycle
transitions (dismiss / promote / resolve) are a later sprint — see report risks.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Text, Float, DateTime, Index
from database import Base


class SeoSignal(Base):
    __tablename__ = "seo_signal"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    audit_id    = Column(String(36), nullable=False)     # soft ref → seo_audit.id
    problem_id  = Column(String(36), nullable=True)      # soft ref → seo_problem.id
    user_id     = Column(String(36), nullable=False)     # soft ref → users.id
    listing_id  = Column(String(36), nullable=True)      # soft ref → product_listings.id
    marketplace = Column(String(20), nullable=True)      # context dimension (Learning OS) + provenance
    sku         = Column(String(255), nullable=True)

    signal_key             = Column(String(64), nullable=False)  # canonical seo_<problem_type>
    insight_key            = Column(String(64), nullable=True)   # seo_<ptype>:<mp>:<sku> (Decision bridge anchor)
    problem_type           = Column(String(40), nullable=False)
    recommended_action_key = Column(String(64), nullable=True)   # canonical primary (Action Catalog)
    alternative_action_keys = Column(Text, nullable=True)        # JSON list of canonical alternatives

    # PULT doctrine, 5 parts (deterministic text, no metrics)
    what          = Column(Text, nullable=True)
    why           = Column(Text, nullable=True)
    meaning       = Column(Text, nullable=True)
    what_to_do    = Column(Text, nullable=True)
    expected_effect = Column(Text, nullable=True)

    priority_level        = Column(String(10), nullable=True)    # critical|high|medium|low
    expected_effect_type  = Column(String(40), nullable=True)    # qualitative
    effect_band           = Column(String(10), nullable=True)    # high|medium|low
    confidence            = Column(Float, nullable=True)

    status      = Column(String(20), nullable=False, default="active",
                         server_default="active")  # draft|active|dismissed|promoted_to_decision|resolved
    decision_id = Column(String(36), nullable=True)   # soft ref → decisions.id (set on promote, later)

    created_at  = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_seo_signal_user_listing", "user_id", "listing_id"),
        Index("ix_seo_signal_insight", "insight_key"),
        Index("ix_seo_signal_audit", "audit_id"),
        Index("ix_seo_signal_status", "status"),
    )
