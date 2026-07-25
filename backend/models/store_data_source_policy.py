import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, ForeignKey, Index, UniqueConstraint, CheckConstraint
from database import Base

# The metric families a seller can source independently (PULT-LAUNCH-1.4.5H). Finer than a sync
# data_type on purpose: one 'finance' data_type splits into revenue/fees/logistics/penalties/
# deductions, and a seller may legitimately want API price but CSV revenue (API has no cost of goods).
METRIC_TYPES = (
    "catalog", "card_content", "price", "stock", "orders",
    "revenue", "marketplace_fees", "logistics", "penalties", "deductions",
    "returns", "cogs", "ad_spend",
)

PREFERENCES = ("auto", "api", "csv")


class StoreDataSourcePolicy(Base):
    """Which source feeds ONE metric for ONE store (PULT-LAUNCH-1.4.5H).

    The row exists only when a seller diverges from the default. An ABSENT row means the effective
    preference is **csv** — the platform's current behaviour — so turning API sync on changes nothing
    until a seller opts in. (The product default flips to 'auto' only once the mapping UI ships in
    1.4.5I; the DB default of "absent ⇒ csv" is deliberately conservative.)

    Never stores a computed value: coverage is derived from ApiSyncState and a conflict is computed at
    read time. The only thing that must persist is the seller's DECISION, and a resolved conflict is
    just a preference that is no longer 'auto'. So one row per (store, metric_type) is enough — a new
    sync never overwrites an explicit api/csv choice.
    """

    __tablename__ = "store_data_source_policies"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Ownership flows Store → Account → Workspace; deleting the store removes its policy.
    marketplace_store_id = Column(
        String(36), ForeignKey("marketplace_stores.id", ondelete="CASCADE"), nullable=False)
    metric_type = Column(String(20), nullable=False)
    preference = Column(String(4), nullable=False, default="auto", server_default="auto")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("marketplace_store_id", "metric_type", name="uq_store_source_policy"),
        CheckConstraint("preference IN ('auto','api','csv')", name="ck_source_policy_preference"),
        CheckConstraint(
            "metric_type IN ('catalog','card_content','price','stock','orders','revenue',"
            "'marketplace_fees','logistics','penalties','deductions','returns','cogs','ad_spend')",
            name="ck_source_policy_metric_type"),
        Index("ix_source_policy_store", "marketplace_store_id"),
    )
