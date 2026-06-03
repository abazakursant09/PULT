import uuid
from sqlalchemy import Column, String, Boolean, Integer, ForeignKey, Index
from database import Base


class TelegramSettings(Base):
    __tablename__ = "telegram_settings"

    id      = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"),
                     unique=True, nullable=False)

    # Event types
    notify_bad_review      = Column(Boolean, nullable=False, default=True,  server_default='1')
    notify_offer_change    = Column(Boolean, nullable=False, default=True,  server_default='1')
    notify_price_drop      = Column(Boolean, nullable=False, default=True,  server_default='1')
    notify_negative_review = Column(Boolean, nullable=False, default=True,  server_default='1')
    notify_trial_end       = Column(Boolean, nullable=False, default=True,  server_default='1')

    # Action Engine insight alerts (Stage 26)
    notify_insights        = Column(Boolean, nullable=False, default=True,  server_default='1')

    # Telegram Intelligence Loop (Stage 26)
    notify_seo_opportunity  = Column(Boolean,  nullable=False, default=True,  server_default='1')
    notify_sales_growth     = Column(Boolean,  nullable=False, default=True,  server_default='1')
    notify_retention        = Column(Boolean,  nullable=False, default=False, server_default='0')
    retention_inactive_days = Column(Integer,  nullable=False, default=3,     server_default='3')

    # Rebuild Tracker reports (Stage 27-29)
    notify_weekly_report = Column(Boolean, nullable=False, default=True,  server_default='1')
    notify_ab_results    = Column(Boolean, nullable=False, default=True,  server_default='1')

    # Scheduled reports
    daily_report         = Column(Boolean,   nullable=False, default=False, server_default='0')
    daily_report_time    = Column(String(5),  nullable=False, default='09:00')
    weekly_summary       = Column(Boolean,   nullable=False, default=False, server_default='0')
    weekly_summary_day   = Column(String(10), nullable=False, default='monday')
    weekly_summary_time  = Column(String(5),  nullable=False, default='09:00')

    __table_args__ = (Index("ix_telegram_settings_user_id", "user_id"),)
