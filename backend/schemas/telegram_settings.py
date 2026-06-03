from typing import Optional
from pydantic import BaseModel, field_validator


class TelegramSettingsOut(BaseModel):
    notify_bad_review:      bool
    notify_offer_change:    bool
    notify_price_drop:      bool
    notify_negative_review: bool
    notify_trial_end:       bool
    notify_insights:        bool
    # Intelligence Loop (Stage 26)
    notify_seo_opportunity:  bool
    notify_sales_growth:     bool
    notify_retention:        bool
    retention_inactive_days: int
    # Rebuild Tracker (Stage 27-29)
    notify_weekly_report:    bool
    notify_ab_results:       bool
    # Scheduled reports
    daily_report:            bool
    daily_report_time:       str
    weekly_summary:          bool
    weekly_summary_day:      str
    weekly_summary_time:     str

    model_config = {"from_attributes": True}


class TelegramSettingsUpdate(BaseModel):
    notify_bad_review:      Optional[bool] = None
    notify_offer_change:    Optional[bool] = None
    notify_price_drop:      Optional[bool] = None
    notify_negative_review: Optional[bool] = None
    notify_trial_end:       Optional[bool] = None
    notify_insights:        Optional[bool] = None
    # Intelligence Loop (Stage 26)
    notify_seo_opportunity:  Optional[bool] = None
    notify_sales_growth:     Optional[bool] = None
    notify_retention:        Optional[bool] = None
    retention_inactive_days: Optional[int]  = None
    # Rebuild Tracker (Stage 27-29)
    notify_weekly_report:    Optional[bool] = None
    notify_ab_results:       Optional[bool] = None
    # Scheduled reports
    daily_report:            Optional[bool] = None
    daily_report_time:       Optional[str]  = None
    weekly_summary:          Optional[bool] = None
    weekly_summary_day:      Optional[str]  = None
    weekly_summary_time:     Optional[str]  = None

    @field_validator("daily_report_time", "weekly_summary_time", mode="before")
    @classmethod
    def validate_time(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        parts = str(v).split(":")
        if len(parts) != 2:
            raise ValueError("Формат времени: HH:MM")
        h, m = parts
        if not (h.isdigit() and m.isdigit() and 0 <= int(h) <= 23 and 0 <= int(m) <= 59):
            raise ValueError("Недопустимое время")
        return v


class UpdateTelegramChatId(BaseModel):
    telegram_chat_id: Optional[str] = None
