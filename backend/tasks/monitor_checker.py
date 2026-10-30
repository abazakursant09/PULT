from sqlalchemy.ext.asyncio import AsyncSession

from models.monitor_event import MonitorEvent


async def run_check(db: AsyncSession) -> list[MonitorEvent]:
    """Retired synthetic checker: keep queued/direct calls inert.

    No verified event provider exists. Do not invent news or read, delete or
    write historical events. Existing rows require a separate provenance review.
    """
    return []
