import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, DateTime, Integer, Numeric, Index, ForeignKey, UniqueConstraint,
    CheckConstraint,
)
from database import Base

# Normalized operation types. `other` is deliberate: an unrecognised WB operation is kept (with its
# official code in provider_operation_code) rather than dropped or forced into the wrong bucket.
OPERATION_TYPES = (
    "order", "sale", "return", "cancellation",
    "commission", "logistics", "penalty", "deduction", "other",
)

# The provider FEED a row came from (PULT-LAUNCH-1.4.5H). This is what stops one real sale from being
# counted twice inside ONE marketplace — e.g. WB reports the same sale in both the sales feed and the
# finance report, and only ONE of them is the authoritative money source. The source-policy money
# reader selects exactly one dataset per metric; 'legacy' is every row written before this field
# existed and NEVER enters a user total automatically.
PROVIDER_DATASETS = (
    "orders", "sales", "finance", "returns", "fbo_postings", "fbs_postings", "legacy",
)


class MarketplaceOperation(Base):
    """One normalized marketplace EVENT from the API (PULT-LAUNCH-1.4.5E2).

    Orders, sales, returns, cancellations and each financial component are all events with the same
    shape but different `operation_type`, so they share one table. They are NOT put in
    ImportedFinanceRow because that table is summed as realized money — an order is not revenue, a
    return is a signed reversal, and a financial breakdown has several components per report row.
    Forcing them there would either invent revenue or lose the event's type.

    Money is Numeric (never float): the sign is meaningful and must survive, and an unknown amount is
    NULL — never 0. No buyer PII (name / phone / address) and no raw payload is stored: only the
    normalized event and the official operation code needed to keep an unknown WB operation from
    being lost.

    Nothing here enters a user-facing total yet — the source policy (1.4.5H) decides how API and CSV
    combine. Until then these rows are stored and isolated.
    """

    __tablename__ = "marketplace_operations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    marketplace_account_id = Column(
        String(36), ForeignKey("marketplace_accounts.id", ondelete="CASCADE"), nullable=False)
    marketplace_store_id = Column(
        String(36), ForeignKey("marketplace_stores.id", ondelete="SET NULL"), nullable=True)
    product_id = Column(
        String(36), ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    marketplace = Column(String(20), nullable=False)
    source = Column(String(10), nullable=False, default="api", server_default="api")

    # The stable external id of THIS operation (WB: srid for an order, saleID for a sale/return,
    # rrd_id for a finance row). Never a page number, array index, fetched_at, random uuid or title.
    external_operation_id = Column(String(64), nullable=False)
    # Links a downstream operation back to its order (WB srid) when the API carries it.
    external_parent_id = Column(String(64), nullable=True)
    operation_type = Column(String(20), nullable=False)
    # The provider feed this row came from (orders|sales|finance|returns|fbo_postings|fbs_postings|
    # legacy). Distinguishes the SAME sale seen in two feeds so money is counted from one only.
    provider_dataset = Column(String(20), nullable=False, default="legacy", server_default="legacy")
    # The official WB operation name/code, kept verbatim so a new/unknown operation is preserved
    # under operation_type='other' instead of being lost. Safe: an operation name, not PII.
    provider_operation_code = Column(String(120), nullable=True)

    occurred_at = Column(DateTime, nullable=True)
    status = Column(String(20), nullable=True)
    quantity = Column(Integer, nullable=True)
    amount = Column(Numeric(18, 2), nullable=True)     # signed; NULL when WB does not report it
    currency = Column(String(3), nullable=True)         # only when the official response confirms it

    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        # One event per (cabinet, external id, type). A single srid may yield an order, a sale and a
        # return — each a distinct operation_type, so all three coexist; a repeated page UPDATEs the
        # matching row instead of inserting a duplicate. A finance row's components each carry their
        # own type, so they coexist too.
        UniqueConstraint("marketplace_account_id", "source", "external_operation_id",
                         "operation_type", name="uq_mp_operation"),
        CheckConstraint("source IN ('api','csv')", name="ck_mp_operation_source"),
        CheckConstraint(
            "operation_type IN ('order','sale','return','cancellation','commission','logistics',"
            "'penalty','deduction','other')", name="ck_mp_operation_type"),
        CheckConstraint(
            "provider_dataset IN ('orders','sales','finance','returns','fbo_postings',"
            "'fbs_postings','legacy')", name="ck_mp_operation_dataset"),
        Index("ix_mp_operation_account_time", "marketplace_account_id", "occurred_at"),
        Index("ix_mp_operation_store_time", "marketplace_store_id", "occurred_at"),
        # The money reader filters by store + dataset + time, so index that path.
        Index("ix_mp_operation_store_dataset_time", "marketplace_store_id", "provider_dataset",
              "occurred_at"),
        Index("ix_mp_operation_product", "product_id"),
        Index("ix_mp_operation_external", "external_operation_id"),
    )
