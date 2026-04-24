"""Enqueue a WhatsApp message after checking notification settings and dedup."""

import re
from datetime import datetime, timezone, timedelta

from bson import ObjectId
from logging_config import get_logger
from middleware.db_guard import ScopedDatabase
from models.communication import CommunicationMessage, ALL_ALERT_TYPES
from models.communication import EVENT_ASSIGNED, DELIVERABLE_ASSIGNED, EVENT_REMINDER, DELIVERABLE_REMINDER, DELIVERABLE_OVERDUE
from models.communication_settings import CommunicationSettings
from utils.phone import resolve_whatsapp_number
import utils.whatsapp_templates as templates

logger = get_logger("communication_generator")


async def _get_settings(db: ScopedDatabase, agency_id: str) -> CommunicationSettings:
    doc = await db.communication_settings.find_one({"agency_id": agency_id})
    if doc:
        settings = CommunicationSettings(**doc)
        # Auto-add any new alert types introduced after this settings doc was created
        stored = set(settings.globally_enabled_types)
        from models.communication import ALL_ALERT_TYPES as _ALL
        missing = [t for t in _ALL if t not in stored]
        if missing:
            settings.globally_enabled_types = settings.globally_enabled_types + missing
        return settings
    return CommunicationSettings(agency_id=agency_id)


def _render_body(alert_type: str, ctx: dict) -> str | None:
    try:
        if alert_type == "project_confirmation":
            return templates.project_confirmation(**ctx)
        if alert_type == "deliverable_uploaded":
            return templates.deliverable_uploaded(**ctx)
        if alert_type == "approval_requested":
            return templates.approval_requested(**ctx)
        if alert_type == "event_assigned":
            return templates.event_assigned(**ctx)
        if alert_type == "deliverable_assigned":
            return templates.deliverable_assigned(**ctx)
        if alert_type == "event_reminder":
            return templates.event_reminder(**ctx)
        if alert_type == "deliverable_reminder":
            return templates.deliverable_reminder(**ctx)
        if alert_type == "deliverable_overdue":
            return templates.deliverable_overdue(**ctx)
        if alert_type == "custom":
            return ctx.get("message_body", "")
        logger.warning(f"Unknown alert_type for rendering: {alert_type}")
        return None
    except Exception as exc:
        logger.error(f"Template render failed for {alert_type}: {exc}", extra={"data": ctx})
        return None


def _render_custom_template(body_template: str, ctx: dict) -> str:
    """Substitute {{variable}} placeholders with values from ctx."""
    result = body_template
    for key, value in ctx.items():
        placeholder = "{{" + key + "}}"
        if placeholder not in result:
            continue
        if isinstance(value, datetime):
            formatted = value.strftime("%d %b %Y")
        elif value is None:
            formatted = ""
        elif key == "amount" and isinstance(value, (int, float)):
            formatted = f"{value:,.2f}"
        else:
            formatted = str(value)
        result = result.replace(placeholder, formatted)
    # Strip any unfilled placeholders
    result = re.sub(r"\{\{[^}]+\}\}", "", result)
    return result.strip()


async def _already_queued_recently(
    db: ScopedDatabase,
    alert_type: str,
    source_id: str,
) -> bool:
    """Dedup: skip if same (alert_type, source.id) was enqueued within last 24h."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    existing = await db.communications_messages.find_one({
        "alert_type": alert_type,
        "source.id": source_id,
        "created_at": {"$gte": cutoff},
    })
    return existing is not None


async def enqueue_message(
    db: ScopedDatabase,
    agency_id: str,
    alert_type: str,
    recipient_client_id: str,
    source: dict,
    render_ctx: dict,
    created_by: str | None = None,
    skip_dedup: bool = False,
) -> CommunicationMessage | None:
    """Check settings, build message, insert into queue. Returns the new doc or None."""
    settings = await _get_settings(db, agency_id)

    if alert_type not in settings.globally_enabled_types:
        logger.debug(f"Alert type {alert_type} disabled globally — skipping")
        return None

    client_override = settings.client_overrides.get(recipient_client_id)
    if client_override:
        if client_override.excluded:
            logger.debug(f"Client {recipient_client_id} fully excluded from communications")
            return None
        if alert_type in client_override.disabled_types:
            logger.debug(f"Alert type {alert_type} disabled for client {recipient_client_id}")
            return None

    # project.client_id stores the MongoDB _id string; fall back to custom id field for legacy docs
    if ObjectId.is_valid(recipient_client_id):
        client_doc = await db.clients.find_one({"_id": ObjectId(recipient_client_id)})
    else:
        client_doc = await db.clients.find_one({"id": recipient_client_id})
    if not client_doc:
        logger.warning(f"Client {recipient_client_id} not found — skipping communication")
        return None

    phone = resolve_whatsapp_number(client_doc)
    if not phone:
        logger.warning(f"No WhatsApp/phone for client {recipient_client_id} — skipping")
        return None

    source_id = source.get("id", "")
    if not skip_dedup and source_id and alert_type not in ("custom",):
        if await _already_queued_recently(db, alert_type, source_id):
            logger.debug(f"Dedup: {alert_type}/{source_id} already queued in last 24h")
            return None

    ctx = dict(render_ctx)
    ctx.setdefault("client_name", client_doc.get("name", "there"))

    # Check for a custom template override for this agency
    custom_tpl = await db.communication_templates.find_one({"alert_type": alert_type})
    if custom_tpl and custom_tpl.get("body_template"):
        body = _render_custom_template(custom_tpl["body_template"], ctx)
    else:
        body = _render_body(alert_type, ctx)

    if not body:
        return None

    msg = CommunicationMessage(
        agency_id=agency_id,
        recipient_type="client",
        recipient_id=recipient_client_id,
        recipient_name=client_doc.get("name", ""),
        recipient_phone=phone,
        message_body=body,
        alert_type=alert_type,
        source=source,
        created_by=created_by,
    )
    await db.communications_messages.insert_one(msg.model_dump())
    logger.info(
        f"Communication message queued",
        extra={"data": {"alert_type": alert_type, "client_id": recipient_client_id, "id": msg.id}},
    )
    return msg


async def enqueue_message_associate(
    db: ScopedDatabase,
    agency_id: str,
    alert_type: str,
    recipient_associate_id: str,
    source: dict,
    render_ctx: dict,
    created_by: str | None = None,
    skip_dedup: bool = False,
) -> CommunicationMessage | None:
    """Enqueue a WA message to an associate (team member). Returns the new doc or None."""
    settings = await _get_settings(db, agency_id)

    if not settings.team_notifications_enabled:
        logger.debug("Team notifications disabled — skipping associate message")
        return None

    if alert_type not in settings.globally_enabled_types:
        logger.debug(f"Alert type {alert_type} disabled globally — skipping")
        return None

    from bson import ObjectId as ObjId
    if ObjId.is_valid(recipient_associate_id):
        associate_doc = await db.associates.find_one({"_id": ObjId(recipient_associate_id)})
    else:
        associate_doc = await db.associates.find_one({"id": recipient_associate_id})

    if not associate_doc:
        logger.warning(f"Associate {recipient_associate_id} not found — skipping")
        return None

    phone = associate_doc.get("phone_number", "").strip()
    if not phone:
        logger.warning(f"No phone for associate {recipient_associate_id} — skipping")
        return None

    source_id = source.get("id", "")
    if not skip_dedup and source_id and alert_type not in ("custom",):
        if await _already_queued_recently(db, alert_type, source_id):
            logger.debug(f"Dedup: {alert_type}/{source_id} already queued in last 24h")
            return None

    ctx = dict(render_ctx)
    ctx.setdefault("associate_name", associate_doc.get("name", "there"))

    custom_tpl = await db.communication_templates.find_one({"alert_type": alert_type})
    if custom_tpl and custom_tpl.get("body_template"):
        body = _render_custom_template(custom_tpl["body_template"], ctx)
    else:
        body = _render_body(alert_type, ctx)

    if not body:
        return None

    msg = CommunicationMessage(
        agency_id=agency_id,
        recipient_type="associate",
        recipient_id=recipient_associate_id,
        recipient_name=associate_doc.get("name", ""),
        recipient_phone=phone,
        message_body=body,
        alert_type=alert_type,
        source=source,
        created_by=created_by,
    )
    await db.communications_messages.insert_one(msg.model_dump())
    logger.info(
        "Associate communication message queued",
        extra={"data": {"alert_type": alert_type, "associate_id": recipient_associate_id, "id": msg.id}},
    )
    return msg
