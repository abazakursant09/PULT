from datetime import datetime
from pydantic import BaseModel


class MonitorEventOut(BaseModel):
    id:              str
    title:           str
    description:     str
    source:          str
    severity:        str
    affected_module: str
    action_required: str
    created_at:      datetime

    model_config = {"from_attributes": True}
