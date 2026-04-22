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


def task_assigned(
    client_name: str,
    task_title: str,
    project_code: str,
    due_date=None,
    agency_name: str = "",
) -> str:
    lines = [
        f"Hi {client_name},",
        "",
        f"A new task has been assigned for your project *{project_code}*:",
        f"📋 *{task_title}*",
    ]
    if due_date:
        lines.append(f"📅 Due: {_fmt_date(due_date)}")
    if agency_name:
        lines.extend(["", f"— {agency_name}"])
    return "\n".join(lines)


def task_deadline(
    client_name: str,
    task_title: str,
    project_code: str,
    due_date,
    agency_name: str = "",
) -> str:
    lines = [
        f"Hi {client_name},",
        "",
        f"A reminder that the following task for *{project_code}* is due soon:",
        f"📋 *{task_title}*",
        f"📅 Due: {_fmt_date(due_date)}",
    ]
    if agency_name:
        lines.extend(["", f"— {agency_name}"])
    return "\n".join(lines)


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


def project_stage_changed(
    client_name: str,
    project_code: str,
    new_status: str,
    agency_name: str = "",
) -> str:
    lines = [
        f"Hi {client_name},",
        "",
        f"Your project *{project_code}* has moved to a new stage: *{new_status}*.",
        "",
        "We'll be in touch with updates soon.",
    ]
    if agency_name:
        lines.extend(["", f"— {agency_name}"])
    return "\n".join(lines)


def invoice_sent(
    client_name: str,
    invoice_no: str,
    amount: float,
    currency: str = "INR",
    due_date=None,
    agency_name: str = "",
) -> str:
    lines = [
        f"Hi {client_name},",
        "",
        f"Your invoice *{invoice_no}* for *{currency} {amount:,.2f}* has been issued.",
    ]
    if due_date:
        lines.append(f"📅 Due by: {_fmt_date(due_date)}")
    lines.extend([
        "",
        "Please let us know if you have any questions.",
    ])
    if agency_name:
        lines.extend(["", f"— {agency_name}"])
    return "\n".join(lines)


def invoice_due_soon(
    client_name: str,
    invoice_no: str,
    amount: float,
    due_date,
    currency: str = "INR",
    agency_name: str = "",
) -> str:
    lines = [
        f"Hi {client_name},",
        "",
        f"A gentle reminder that invoice *{invoice_no}* (*{currency} {amount:,.2f}*) is due on *{_fmt_date(due_date)}*.",
        "",
        "Please reach out if you need any assistance.",
    ]
    if agency_name:
        lines.extend(["", f"— {agency_name}"])
    return "\n".join(lines)


def invoice_overdue(
    client_name: str,
    invoice_no: str,
    amount: float,
    due_date,
    currency: str = "INR",
    agency_name: str = "",
) -> str:
    lines = [
        f"Hi {client_name},",
        "",
        f"Invoice *{invoice_no}* (*{currency} {amount:,.2f}*) was due on *{_fmt_date(due_date)}* and appears to be outstanding.",
        "",
        "Kindly arrange payment at your earliest convenience. Thank you!",
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


def task_assigned_associate(
    associate_name: str,
    task_title: str,
    project_code: str,
    due_date=None,
    agency_name: str = "",
) -> str:
    lines = [
        f"Hi {associate_name},",
        "",
        f"You've been assigned a task on project *{project_code}*:",
        f"📋 *{task_title}*",
    ]
    if due_date:
        lines.append(f"📅 Due: {_fmt_date(due_date)}")
    if agency_name:
        lines.extend(["", f"— {agency_name}"])
    return "\n".join(lines)


def task_deadline_associate(
    associate_name: str,
    task_title: str,
    project_code: str,
    due_date=None,
    agency_name: str = "",
) -> str:
    lines = [
        f"Reminder: *{task_title}* ({project_code}) is due soon.",
    ]
    if due_date:
        lines.append(f"📅 Due: {_fmt_date(due_date)}")
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
