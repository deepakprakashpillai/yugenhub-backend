from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import uuid

# Alert type string constants — use strings (not Literal) to allow new types without migrations
TASK_ASSIGNED = "task_assigned"
TASK_DEADLINE = "task_deadline"
PROJECT_CONFIRMATION = "project_confirmation"
PROJECT_STAGE_CHANGED = "project_stage_changed"
INVOICE_SENT = "invoice_sent"
INVOICE_DUE_SOON = "invoice_due_soon"
INVOICE_OVERDUE = "invoice_overdue"
APPROVAL_REQUESTED = "approval_requested"
DELIVERABLE_UPLOADED = "deliverable_uploaded"
CUSTOM = "custom"

ALL_ALERT_TYPES = [
    TASK_ASSIGNED,
    TASK_DEADLINE,
    PROJECT_CONFIRMATION,
    PROJECT_STAGE_CHANGED,
    INVOICE_SENT,
    INVOICE_DUE_SOON,
    INVOICE_OVERDUE,
    APPROVAL_REQUESTED,
    DELIVERABLE_UPLOADED,
    CUSTOM,
]

ALERT_TYPE_LABELS = {
    TASK_ASSIGNED: "Task Assigned",
    TASK_DEADLINE: "Task Deadline",
    PROJECT_CONFIRMATION: "Project Confirmation",
    PROJECT_STAGE_CHANGED: "Project Stage Changed",
    INVOICE_SENT: "Invoice Sent",
    INVOICE_DUE_SOON: "Invoice Due Soon",
    INVOICE_OVERDUE: "Invoice Overdue",
    APPROVAL_REQUESTED: "Approval Requested",
    DELIVERABLE_UPLOADED: "Deliverable Uploaded",
    CUSTOM: "Custom",
}


class CommunicationMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agency_id: str
    recipient_type: str = "client"  # "client" now; future-proof for "associate"
    recipient_id: str
    recipient_name: str              # snapshot at generation time
    recipient_phone: str             # resolved wa number snapshot
    message_body: str                # preserves line breaks/spacing
    alert_type: str                  # one of ALL_ALERT_TYPES
    status: str = "pending"          # "pending" | "sent" | "queued_for_send" | "failed" | "cancelled"
    source: Dict[str, Any] = Field(default_factory=dict)  # {"kind": "task"|"project"|..., "id": "..."}
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
