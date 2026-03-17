from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone

import uuid


class PortalAnalyticsEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str
    portal_token: str
    event_type: str  # "visit" | "file_download" | "deliverable_view"
    deliverable_id: Optional[str] = None
    file_id: Optional[str] = None
    file_name: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
