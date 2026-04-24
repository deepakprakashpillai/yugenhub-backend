from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime, timezone


class SchedulerConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    agency_id: str
    event_scan_enabled: bool = True
    event_reminder_hours_before: int = 24
    deliverable_scan_enabled: bool = True
    deliverable_reminder_days_before: int = 3
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
