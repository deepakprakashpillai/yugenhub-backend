from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import uuid

# Alert type string constants
PROJECT_CONFIRMATION = "project_confirmation"
DELIVERABLE_UPLOADED = "deliverable_uploaded"
APPROVAL_REQUESTED = "approval_requested"
EVENT_ASSIGNED = "event_assigned"
DELIVERABLE_ASSIGNED = "deliverable_assigned"
EVENT_REMINDER = "event_reminder"
DELIVERABLE_REMINDER = "deliverable_reminder"
DELIVERABLE_OVERDUE = "deliverable_overdue"
CUSTOM = "custom"

ALL_ALERT_TYPES = [
    PROJECT_CONFIRMATION,
    DELIVERABLE_UPLOADED,
    APPROVAL_REQUESTED,
    EVENT_ASSIGNED,
    DELIVERABLE_ASSIGNED,
    EVENT_REMINDER,
    DELIVERABLE_REMINDER,
    DELIVERABLE_OVERDUE,
    CUSTOM,
]

ALERT_TYPE_LABELS = {
    PROJECT_CONFIRMATION: "Project Confirmation",
    DELIVERABLE_UPLOADED: "Deliverable Uploaded",
    APPROVAL_REQUESTED: "Approval Requested",
    EVENT_ASSIGNED: "Event Assigned (Team)",
    DELIVERABLE_ASSIGNED: "Deliverable Assigned (Team)",
    EVENT_REMINDER: "Event Reminder (Team)",
    DELIVERABLE_REMINDER: "Deliverable Reminder (Team)",
    DELIVERABLE_OVERDUE: "Deliverable Overdue (Team)",
    CUSTOM: "Custom",
}


class CommunicationMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agency_id: str
    recipient_type: str = "client"  # "client" | "associate"
    recipient_id: str
    recipient_name: str              # snapshot at generation time
    recipient_phone: str             # resolved wa number snapshot
    message_body: str                # preserves line breaks/spacing
    alert_type: str                  # one of ALL_ALERT_TYPES
    status: str = "pending"          # "pending" | "sent" | "queued_for_send" | "failed" | "cancelled"
    source: Dict[str, Any] = Field(default_factory=dict)  # {"kind": "event"|"task"|..., "id": "..."}
    send_channel: Optional[str] = None   # "manual" | "automation"
    created_by: Optional[str] = None     # user id if manually composed; None if auto-generated
    edited: bool = False                 # True if operator edited body before send
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sent_at: Optional[datetime] = None
    last_error: Optional[str] = None

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        extra="ignore",
    )
