from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime, timezone


class SchedulerConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    agency_id: str
    task_deadline_enabled: bool = True
    task_deadline_hours_before: int = 24
    invoice_scan_enabled: bool = True
    invoice_due_soon_days_before: int = 3
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
