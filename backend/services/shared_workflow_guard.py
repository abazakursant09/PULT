"""First-test admission only; not account authorization or an erasure executor."""
from fastapi import HTTPException

from config import settings


WRITE_HOLD_DETAIL = (
    "В тестовом режиме доступны CSV, аналитика, экспорт и чтение. "
    "Изменение связанных записей временно отключено."
)


def shared_workflow_writes_held() -> bool:
    """Only a deliberate, typed operator False releases the former write path."""
    return getattr(settings, "shared_workflows_read_only", True) is not False


def require_shared_workflow_write() -> None:
    if shared_workflow_writes_held():
        raise HTTPException(status_code=403, detail=WRITE_HOLD_DETAIL)
