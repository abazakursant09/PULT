"""
Sentry backend setup. Call init_sentry() in main.py before app creation.

Graceful by design: no SENTRY_DSN → no-op; sentry-sdk missing → warn and continue.
The application always runs whether or not error tracking is enabled.
"""
import logging

from config import settings

log = logging.getLogger(__name__)


def init_sentry() -> None:
    dsn = settings.sentry_dsn
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=dsn,
            environment=settings.app_env,
            traces_sample_rate=0.1,
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
            ignore_errors=[KeyboardInterrupt],
        )
        log.info("Sentry initialized (env=%s)", settings.app_env)
    except ImportError:
        log.warning("sentry-sdk not installed; error tracking disabled")
