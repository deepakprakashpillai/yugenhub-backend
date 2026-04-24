from pydantic import BaseModel, Field
from datetime import datetime, timezone
import uuid


ALERT_TYPE_VARIABLES: dict[str, list[str]] = {
    "project_confirmation":  ["client_name", "project_code", "vertical", "event_count", "deliverable_count", "first_event_date", "agency_name"],
    "deliverable_uploaded":  ["client_name", "project_code", "deliverable_name", "agency_name"],
    "approval_requested":    ["client_name", "project_code", "deliverable_name", "agency_name"],
    "event_assigned":        ["associate_name", "project_code", "event_type", "event_date", "venue_name", "agency_name"],
    "deliverable_assigned":  ["associate_name", "project_code", "deliverable_type", "due_date", "agency_name"],
    "event_reminder":        ["associate_name", "project_code", "event_type", "event_date", "venue_name", "agency_name"],
    "deliverable_reminder":  ["associate_name", "project_code", "deliverable_type", "due_date", "agency_name"],
    "deliverable_overdue":   ["associate_name", "project_code", "deliverable_type", "due_date", "agency_name"],
}

DEFAULT_TEMPLATES: dict[str, str] = {
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
    "deliverable_uploaded": (
        "Hi {{client_name}},\n\n"
        "We've uploaded new files for *{{project_code}}*:\n"
        "📁 *{{deliverable_name}}*\n\n"
        "Check them out on your client portal.\n\n"
        "— {{agency_name}}"
    ),
    "approval_requested": (
        "Hi {{client_name}},\n\n"
        "A deliverable from your project *{{project_code}}* is ready for your review and approval:\n"
        "📁 *{{deliverable_name}}*\n\n"
        "Please log in to the client portal to review and share your feedback.\n\n"
        "— {{agency_name}}"
    ),
    "event_assigned": (
        "Hi {{associate_name}},\n\n"
        "You've been assigned to *{{event_type}}* for project *{{project_code}}*.\n"
        "📅 Date: {{event_date}}\n"
        "📍 Venue: {{venue_name}}\n\n"
        "— {{agency_name}}"
    ),
    "deliverable_assigned": (
        "Hi {{associate_name}},\n\n"
        "You've been assigned a deliverable on project *{{project_code}}*:\n"
        "📦 *{{deliverable_type}}*\n"
        "📅 Due: {{due_date}}\n\n"
        "— {{agency_name}}"
    ),
    "event_reminder": (
        "Hi {{associate_name}},\n\n"
        "Reminder: *{{event_type}}* for project *{{project_code}}* is coming up soon.\n"
        "📅 Date: {{event_date}}\n"
        "📍 Venue: {{venue_name}}\n\n"
        "— {{agency_name}}"
    ),
    "deliverable_reminder": (
        "Hi {{associate_name}},\n\n"
        "Reminder: *{{deliverable_type}}* for project *{{project_code}}* is due soon.\n"
        "📅 Due: {{due_date}}\n\n"
        "— {{agency_name}}"
    ),
    "deliverable_overdue": (
        "Hi {{associate_name}},\n\n"
        "*{{deliverable_type}}* for project *{{project_code}}* is overdue.\n"
        "📅 Was due: {{due_date}}\n\n"
        "Please update your progress or reach out if you need help.\n\n"
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
