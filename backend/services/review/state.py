"""Derived review workspace state — computed in the read layer, never stored.

The ReviewResponse row carries a raw `status` (pending / drafted / approved / published), a
`safety_category` (SAFE / ATTENTION / RISK, AR2) and a `failure_reason` (set only when a publish
attempt failed, AR3). The seller workspace shows one of seven states, resolved by a fixed
precedence so a conflict (e.g. a RISK review that was approved and published) has one answer:

    Published > Failed > AwaitingModeration > Approved > Drafted > NeedsAttention > New

Processing is a transient state a caller may set while a sync is in flight; it is never persisted
here, so it is not derived from a stored row.
"""
from __future__ import annotations

from typing import Optional

NEW = "New"
PROCESSING = "Processing"
DRAFTED = "Drafted"
NEEDS_ATTENTION = "NeedsAttention"
APPROVED = "Approved"
AWAITING_MODERATION = "AwaitingModeration"
PUBLISHED = "Published"
FAILED = "Failed"

_NON_SAFE = {"RISK", "ATTENTION"}


def derive_state(status: Optional[str], safety_category: Optional[str], failure_reason: Optional[str]) -> str:
    """Resolve the workspace state from a row's raw fields, by precedence."""
    if status == "published":
        return PUBLISHED
    # A publish was attempted and failed — surfaced regardless of the row's other fields, unless
    # it later succeeded (handled above).
    if failure_reason:
        return FAILED
    # The reply reached the marketplace but is not visible yet: some marketplaces moderate a seller's
    # answer before showing it (Yandex). It is emphatically NOT published — claiming otherwise would
    # tell the seller their answer is live when nobody can see it — and it must never be re-sent.
    if status == "awaiting_moderation":
        return AWAITING_MODERATION
    if status == "approved":
        return APPROVED
    if status == "drafted":
        return DRAFTED
    # Not yet drafted/approved/published/failed: a non-SAFE review needs the seller's hand.
    if safety_category in _NON_SAFE:
        return NEEDS_ATTENTION
    return NEW
