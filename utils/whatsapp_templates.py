"""WhatsApp message body renderers — one function per alert type.

Each function returns a plain string with line breaks preserved.
The body is stored as-is and URL-encoded by the sender when building wa.me links.
"""

from datetime import datetime
from typing import Optional


def _fmt_date(dt) -> str:
    if not dt:
        return ""
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return dt
    return dt.strftime("%d %b %Y")


# ─── Client templates ─────────────────────────────────────────────────────────

def project_confirmation(
    client_name: str,
    project_code: str,
    vertical: str,
    event_count: int,
    deliverable_count: int,
    first_event_date=None,
    agency_name: str = "",
) -> str:
    lines = [
        f"Hi {client_name},",
        "",
        f"Great news! Your project *{project_code}* ({vertical}) has been confirmed. 🎉",
        "",
        "Here's a quick summary:",
        f"• 📅 Events: {event_count}",
        f"• 📦 Deliverables: {deliverable_count}",
    ]
    if first_event_date:
        lines.append(f"• 🗓 First event: {_fmt_date(first_event_date)}")
    lines.extend([
        "",
        "We'll keep you updated as things progress.",
    ])
    if agency_name:
        lines.extend(["", f"— {agency_name}"])
    return "\n".join(lines)


def deliverable_uploaded(
    client_name: str,
    project_code: str,
    deliverable_name: str,
    agency_name: str = "",
) -> str:
    lines = [
        f"Hi {client_name},",
        "",
        f"We've uploaded new files for *{project_code}*:",
        f"📁 *{deliverable_name}*",
        "",
        "Check them out on your client portal.",
    ]
    if agency_name:
        lines.extend(["", f"— {agency_name}"])
    return "\n".join(lines)


def approval_requested(
    client_name: str,
    project_code: str,
    deliverable_name: str,
    agency_name: str = "",
) -> str:
    lines = [
        f"Hi {client_name},",
        "",
        f"A deliverable from your project *{project_code}* is ready for your review and approval:",
        f"📁 *{deliverable_name}*",
        "",
        "Please log in to the client portal to review and share your feedback.",
    ]
    if agency_name:
        lines.extend(["", f"— {agency_name}"])
    return "\n".join(lines)


# ─── Associate templates ──────────────────────────────────────────────────────

def event_assigned(
    associate_name: str,
    project_code: str,
    event_type: str,
    event_date=None,
    venue_name: str = "",
    agency_name: str = "",
) -> str:
    lines = [
        f"Hi {associate_name},",
        "",
        f"You've been assigned to *{event_type}* for project *{project_code}*.",
    ]
    if event_date:
        lines.append(f"📅 Date: {_fmt_date(event_date)}")
    if venue_name:
        lines.append(f"📍 Venue: {venue_name}")
    if agency_name:
        lines.extend(["", f"— {agency_name}"])
    return "\n".join(lines)


def deliverable_assigned(
    associate_name: str,
    project_code: str,
    deliverable_type: str,
    due_date=None,
    agency_name: str = "",
) -> str:
    lines = [
        f"Hi {associate_name},",
        "",
        f"You've been assigned a deliverable on project *{project_code}*:",
        f"📦 *{deliverable_type}*",
    ]
    if due_date:
        lines.append(f"📅 Due: {_fmt_date(due_date)}")
    if agency_name:
        lines.extend(["", f"— {agency_name}"])
    return "\n".join(lines)


def event_reminder(
    associate_name: str,
    project_code: str,
    event_type: str,
    event_date=None,
    venue_name: str = "",
    agency_name: str = "",
) -> str:
    lines = [
        f"Hi {associate_name},",
        "",
        f"Reminder: *{event_type}* for project *{project_code}* is coming up soon.",
    ]
    if event_date:
        lines.append(f"📅 Date: {_fmt_date(event_date)}")
    if venue_name:
        lines.append(f"📍 Venue: {venue_name}")
    if agency_name:
        lines.extend(["", f"— {agency_name}"])
    return "\n".join(lines)


def deliverable_reminder(
    associate_name: str,
    project_code: str,
    deliverable_type: str,
    due_date=None,
    agency_name: str = "",
) -> str:
    lines = [
        f"Hi {associate_name},",
        "",
        f"Reminder: *{deliverable_type}* for project *{project_code}* is due soon.",
    ]
    if due_date:
        lines.append(f"📅 Due: {_fmt_date(due_date)}")
    if agency_name:
        lines.extend(["", f"— {agency_name}"])
    return "\n".join(lines)


def deliverable_overdue(
    associate_name: str,
    project_code: str,
    deliverable_type: str,
    due_date=None,
    agency_name: str = "",
) -> str:
    lines = [
        f"Hi {associate_name},",
        "",
        f"*{deliverable_type}* for project *{project_code}* is overdue.",
    ]
    if due_date:
        lines.append(f"📅 Was due: {_fmt_date(due_date)}")
    lines.extend([
        "",
        "Please update your progress or reach out if you need help.",
    ])
    if agency_name:
        lines.extend(["", f"— {agency_name}"])
    return "\n".join(lines)
