import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Float, Text, ForeignKey, Index
from database import Base


class Decision(Base):
    """
    Decision object (Doctrine §7 core model) — first-class сущность.

    Финальная формула PULT: Проблема → Причина → Последствие → Решение →
    Действие, с ожидаемым PnL impact. Привязан к товару и/или листингу.
    """
    __tablename__ = "decisions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    physical_product_id = Column(String(36), ForeignKey("physical_products.id", ondelete="CASCADE"), nullable=True)
    listing_id = Column(String(36), ForeignKey("product_listings.id", ondelete="SET NULL"), nullable=True)

    # Структура решения
    problem = Column(String(500), nullable=False)
    cause   = Column(Text, nullable=True)
    effect  = Column(Text, nullable=True)          # последствие
    action  = Column(String(500), nullable=True)   # рекомендованное действие (человекочитаемо)
    action_key = Column(String(64), nullable=True) # ключ для executor (one-click §11), null если ручное
    insight_key = Column(String(64), nullable=True) # анкор Insight → Decision (bridge); null для seed/legacy

    # Экономика решения
    pnl_impact = Column(Float, nullable=True)       # ожидаемый эффект, ₽
    pnl_level  = Column(String(10), nullable=True)  # level1 (без COGS) | level2 (с COGS) §14.1
    severity   = Column(String(10), nullable=False, default="warn")  # loss | warn | gain

    # Происхождение и жизненный цикл
    source = Column(String(10), nullable=False, default="compute")   # api | compute | forecast §12
    status = Column(String(15), nullable=False, default="open", server_default="open")  # open|in_progress|resolved|dismissed

    created_at  = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_decision_user_status", "user_id", "status"),
        Index("ix_decision_phys", "physical_product_id"),
        Index("ix_decision_listing", "listing_id"),
        # One Decision per (seller, insight). NULLs distinct → seed/legacy rows ok.
        Index("uq_decision_user_insight", "user_id", "insight_key", unique=True),
    )
