from pydantic import BaseModel, Field
from datetime import datetime, timezone
import uuid


ALERT_TYPE_VARIABLES: dict[str, list[str]] = {
    "task_assigned":            ["client_name", "task_title", "project_code", "due_date", "agency_name"],
    "task_deadline":            ["client_name", "task_title", "project_code", "due_date", "agency_name"],
    "task_assigned_associate":  ["associate_name", "task_title", "project_code", "due_date", "agency_name"],
    "task_deadline_associate":  ["associate_name", "task_title", "project_code", "due_date", "agency_name"],
    "project_confirmation":     ["client_name", "project_code", "vertical", "event_count", "deliverable_count", "first_event_date", "agency_name"],
    "project_stage_changed":    ["client_name", "project_code", "new_status", "agency_name"],
    "invoice_sent":             ["client_name", "invoice_no", "amount", "currency", "due_date", "agency_name"],
    "invoice_due_soon":         ["client_name", "invoice_no", "amount", "currency", "due_date", "agency_name"],
    "invoice_overdue":          ["client_name", "invoice_no", "amount", "currency", "due_date", "agency_name"],
    "approval_requested":       ["client_name", "project_code", "deliverable_name", "agency_name"],
    "deliverable_uploaded":     ["client_name", "project_code", "deliverable_name", "agency_name"],
}

DEFAULT_TEMPLATES: dict[str, str] = {
    "task_assigned_associate": (
        "Hi {{associate_name}},\n\n"
        "You've been assigned a task on project *{{project_code}}*:\n"
        "📋 *{{task_title}}*\n"
        "📅 Due: {{due_date}}\n\n"
        "— {{agency_name}}"
    ),
    "task_deadline_associate": (
        "Reminder: *{{task_title}}* ({{project_code}}) is due soon.\n"
        "📅 Due: {{due_date}}\n\n"
        "— {{agency_name}}"
    ),
    "task_assigned": (
        "Hi {{client_name}},\n\n"
        "A new task has been assigned for your project *{{project_code}}*:\n"
        "📋 *{{task_title}}*\n"
        "📅 Due: {{due_date}}\n\n"
        "— {{agency_name}}"
    ),
    "task_deadline": (
        "Hi {{client_name}},\n\n"
        "A reminder that the following task for *{{project_code}}* is due soon:\n"
        "📋 *{{task_title}}*\n"
        "📅 Due: {{due_date}}\n\n"
        "— {{agency_name}}"
    ),
    "project_confirmation": (
        "Hi {{client_name}},\n\n"
        "Great news! Your project *{{project_code}}* ({{vertical}}) has been confirmed. 🎉\n\n"
        "Here's a quick summary:\n"
        "• 📅 Events: {{event_count}}\n"
        "• 📦 Deliverables: {{deliverable_count}}\n"
        "• 🗓 First event: {{first_event_date}}\n\n"
        "We'll keep you updated as things progress.\n\n"
        "— {{agency_name}}"
    ),
    "project_stage_changed": (
        "Hi {{client_name}},\n\n"
        "Your project *{{project_code}}* has moved to a new stage: *{{new_status}}*.\n\n"
        "We'll be in touch with updates soon.\n\n"
        "— {{agency_name}}"
    ),
    "invoice_sent": (
        "Hi {{client_name}},\n\n"
        "Your invoice *{{invoice_no}}* for *{{currency}} {{amount}}* has been issued.\n"
        "📅 Due by: {{due_date}}\n\n"
        "Please let us know if you have any questions.\n\n"
        "— {{agency_name}}"
    ),
    "invoice_due_soon": (
        "Hi {{client_name}},\n\n"
        "A gentle reminder that invoice *{{invoice_no}}* (*{{currency}} {{amount}}*) is due on *{{due_date}}*.\n\n"
        "Please reach out if you need any assistance.\n\n"
        "— {{agency_name}}"
    ),
    "invoice_overdue": (
        "Hi {{client_name}},\n\n"
        "Invoice *{{invoice_no}}* (*{{currency}} {{amount}}*) was due on *{{due_date}}* and appears to be outstanding.\n\n"
        "Kindly arrange payment at your earliest convenience. Thank you!\n\n"
        "— {{agency_name}}"
    ),
    "approval_requested": (
        "Hi {{client_name}},\n\n"
        "A deliverable from your project *{{project_code}}* is ready for your review and approval:\n"
        "📁 *{{deliverable_name}}*\n\n"
        "Please log in to the client portal to review and share your feedback.\n\n"
        "— {{agency_name}}"
    ),
    "deliverable_uploaded": (
        "Hi {{client_name}},\n\n"
        "We've uploaded new files for *{{project_code}}*:\n"
        "📁 *{{deliverable_name}}*\n\n"
        "Check them out on your client portal.\n\n"
        "— {{agency_name}}"
    ),
}


class CommunicationTemplate(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agency_id: str
    alert_type: str
    body_template: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_by: str
