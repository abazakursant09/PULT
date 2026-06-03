import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, DateTime, Boolean, Text, Index, UniqueConstraint
from database import Base


class PromoCode(Base):
    __tablename__ = "promo_codes"

    id                  = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    code                = Column(String(50), nullable=False, unique=True, index=True)
    # percent | fixed | extended_trial | blogger_free
    type                = Column(String(20), nullable=False)
    value               = Column(Float, nullable=False)          # % or rub or days
    description         = Column(Text,   nullable=True)
    # Which plan(s) this applies to (comma-sep: master,profi,maximum or "all")
    applicable_plans    = Column(String(100), nullable=False, default="all")
    max_activations     = Column(Integer, nullable=True)         # null = unlimited
    current_activations = Column(Integer, nullable=False, default=0)
    is_active           = Column(Boolean, nullable=False, default=True)
    # Blogger attribution
    blogger_name        = Column(String(255), nullable=True)
    expires_at          = Column(DateTime, nullable=True)
    created_at          = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_promo_active", "is_active"),
    )


class PromoCodeActivation(Base):
    __tablename__ = "promo_code_activations"

    id           = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    promo_id     = Column(String(36), nullable=False)
    user_id      = Column(String(36), nullable=False)
    plan         = Column(String(50), nullable=True)
    activated_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("promo_id", "user_id", name="uq_promo_user"),
        Index("ix_promo_act_promo",  "promo_id"),
        Index("ix_promo_act_user",   "user_id"),
    )
