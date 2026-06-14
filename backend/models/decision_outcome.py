import uuid
import enum
from datetime import datetime
from sqlalchemy import (
    Column, String, Float, Integer, DateTime, ForeignKey, UniqueConstraint, Index,
)
from database import Base


class DecisionOutcomeLabel(str, enum.Enum):
    STILL_OPEN        = "still_open"
    CONFIRMED         = "confirmed"
    REFUTED           = "refuted"
    NOT_TAKEN         = "not_taken"
    INSUFFICIENT_DATA = "insufficient_data"


ALLOWED_LABELS = frozenset(label.value for label in DecisionOutcomeLabel)


class DecisionOutcome(Base):
    """
    Binds one Decision to OBSERVED metric facts within its window.

    DOCTRINE — observed state only, NOT causality. `confirmed` means the target
    metric moved favorably across the window; `refuted` means it did not. Neither
    asserts the decision *caused* the change. Attribution is a separate, later
    layer. This record never says "the decision caused it".

    Lifecycle: ONE row per decision (unique decision_id), updated in place
    (still_open → confirmed | refuted | not_taken | insufficient_data). The
    append-only fact ledger lives one level down in `observations`; this row
    only references those facts by id.
    """
    __tablename__ = "decision_outcomes"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    decision_id = Column(String(36), ForeignKey("decisions.id", ondelete="CASCADE"), nullable=False)
    metric_name = Column(String(40), nullable=False)

    # FK references to Observation facts — never duplicate raw values here.
    baseline_observation_id = Column(String(36), ForeignKey("observations.id", ondelete="SET NULL"), nullable=True)
    realized_observation_id = Column(String(36), ForeignKey("observations.id", ondelete="SET NULL"), nullable=True)

    expected_window_days = Column(Integer, nullable=False)
    outcome_label        = Column(String(20), nullable=False,
                                  default=DecisionOutcomeLabel.STILL_OPEN.value,
                                  server_default=DecisionOutcomeLabel.STILL_OPEN.value)
    realized_delta       = Column(Float, nullable=True)      # set only when realized_observation_id present
    measured_at          = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("decision_id", name="uq_decision_outcome_decision"),  # one outcome per decision
        Index("ix_decision_outcome_label", "outcome_label"),
    )
