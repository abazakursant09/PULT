"""
Sentry backend setup.
Install: pip install sentry-sdk[fastapi]
Add to requirements.txt: sentry-sdk[fastapi]>=1.40.0
Call init_sentry() in main.py before app creation.
"""
import logging
import os

log = logging.getLogger(__name__)


def init_sentry() -> None:
    dsn = os.getenv("SENTRY_DSN", "")
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=dsn,
            environment=os.getenv("APP_ENV", "development"),
            traces_sample_rate=0.1,
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
            ignore_errors=[KeyboardInterrupt],
        )
        log.info("Sentry initialized (env=%s)", os.getenv("APP_ENV"))
    except ImportError:
        log.warning("sentry-sdk not installed; error tracking disabled")
