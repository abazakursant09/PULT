"""AR-CONTROL — server-side gate for automated review publishing.

Single source of truth for "may this seller's Auto Reviews rule publish automatically right now".
Enforced on the BACKEND, re-read from the DB immediately before every automatic publish — a frontend
checkbox is never trusted. Complements (does not replace) services/marketplace/guard.py, which keeps
the SAFE-only / negative-never / idempotency belts inside the executor.

Default OFF: a rule with no consent, no connection, or enabled=False can never auto-publish.
"""
from __future__ import annotations

from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.automation_rule import AutomationRule
from models.marketplace_connection import MarketplaceConnection
from .reviews import get_review_provider

REVIEW_ACTION = "publish_review_response"
REQUIRED_SCOPE = "feedbacks"

# Consent text version currently in force. A rule whose stored consent_version is not supported is
# treated as no consent (the seller must re-consent to the current text). Bump this set when the
# consent text changes materially.
CONSENT_VERSION = "v1"
SUPPORTED_CONSENT_VERSIONS = {"v1"}


class AutoPublishBlocked(Exception):
    """Raised when automated publishing must not proceed. `reason` is a stable machine code."""

    def __init__(self, reason: str, message: str = ""):
        self.reason = reason
        self.message = message or reason
        super().__init__(self.message)


def marketplace_supports_reviews(marketplace: str) -> bool:
    """True iff PULT has a working review provider for this marketplace (WB/Ozon today, not Yandex)."""
    provider = get_review_provider(marketplace)
    return bool(provider and provider.supports_reviews())


def consent_active(rule: AutomationRule) -> bool:
    """A rule carries active, current, non-revoked consent."""
    return (
        rule.consent_at is not None
        and rule.consent_revoked_at is None
        and rule.consent_version in SUPPORTED_CONSENT_VERSIONS
    )


def check_connection_publishable(conn: Optional[MarketplaceConnection]) -> None:
    """Raise AutoPublishBlocked unless the connection is owned-and-usable for review publishing.

    Caller must have loaded `conn` scoped to the seller's user_id (so a non-None conn already means
    ownership). Passing None means "not owned / not found".
    """
    if conn is None:
        raise AutoPublishBlocked("CONNECTION_NOT_OWNED", "connection not found for this seller")
    if conn.status != "connected":
        raise AutoPublishBlocked("CONNECTION_NOT_CONNECTED", f"connection status is {conn.status}")
    if not marketplace_supports_reviews(conn.marketplace):
        raise AutoPublishBlocked("MARKETPLACE_UNSUPPORTED",
                                 f"Auto Reviews not supported on {conn.marketplace}")
    if REQUIRED_SCOPE not in (conn.scopes or []):
        raise AutoPublishBlocked("NO_FEEDBACKS_PERMISSION",
                                 "connection lacks the 'feedbacks' permission")


def automation_globally_enabled() -> bool:
    """The system-level kill switch. When False, the worker never auto-publishes for anyone."""
    return bool(settings.automation_enabled)


def assert_auto_state_runnable(mode: str, enabled: bool) -> None:
    """Reject a rule state that would read as running-auto while the kill switch blocks the worker.

    A rule in mode=auto that is enabled while the global kill switch is OFF would show the seller
    "on" though the worker can never publish it — a false status. Confirm-mode rules are unaffected
    (they never depend on the worker). Raises AutoPublishBlocked otherwise returns None.
    """
    if mode == "auto" and enabled and not automation_globally_enabled():
        raise AutoPublishBlocked("KILL_SWITCH", "automation is disabled by the system")


def check_enable_preconditions(rule: AutomationRule, conn: Optional[MarketplaceConnection]) -> None:
    """Raise AutoPublishBlocked unless the rule may be turned on (enabled=True).

    Enabling is independent of the current mode; a confirm-mode rule may be enabled too. The
    consent + connection requirements are identical to auto-publish so the two can never diverge.
    """
    if rule.action_type != REVIEW_ACTION:
        raise AutoPublishBlocked("WRONG_ACTION", "not a review-automation rule")
    if not rule.connection_id:
        raise AutoPublishBlocked("NO_CONNECTION", "rule is not bound to a connection")
    if not consent_active(rule):
        raise AutoPublishBlocked("NO_CONSENT", "seller consent is missing, revoked or outdated")
    check_connection_publishable(conn)


async def assert_auto_publish_allowed(
    db: AsyncSession, rule_id: str, user_id: str
) -> Tuple[AutomationRule, MarketplaceConnection]:
    """Re-read the rule + its connection from the DB and assert automated publishing is allowed.

    Returns (rule, connection) on success. Raises AutoPublishBlocked otherwise. This is the check the
    worker runs immediately before each publish, so a mid-run disable / revoke takes effect at once.
    """
    if not settings.automation_enabled:
        raise AutoPublishBlocked("KILL_SWITCH", "automation is disabled by the system")

    rule = (
        await db.execute(
            select(AutomationRule).where(
                AutomationRule.id == rule_id,
                AutomationRule.user_id == user_id,
            )
        )
    ).scalars().first()
    if rule is None:
        raise AutoPublishBlocked("RULE_MISSING", "automation rule not found")
    if rule.action_type != REVIEW_ACTION:
        raise AutoPublishBlocked("WRONG_ACTION", "not a review-automation rule")
    if not rule.enabled:
        raise AutoPublishBlocked("DISABLED", "rule is disabled")
    if rule.mode != "auto":
        raise AutoPublishBlocked("MODE_NOT_AUTO", "rule mode is not 'auto'")
    if not consent_active(rule):
        raise AutoPublishBlocked("NO_CONSENT", "seller consent is missing, revoked or outdated")
    if not rule.connection_id:
        raise AutoPublishBlocked("NO_CONNECTION", "rule is not bound to a connection")

    conn = (
        await db.execute(
            select(MarketplaceConnection).where(
                MarketplaceConnection.id == rule.connection_id,
                MarketplaceConnection.user_id == user_id,
            )
        )
    ).scalars().first()
    check_connection_publishable(conn)
    return rule, conn
