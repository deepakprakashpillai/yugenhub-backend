"""Enqueue a WhatsApp message after checking notification settings and dedup.

Usage:
    background_tasks.add_task(
        enqueue_message,
        db=db,
        agency_id=agency_id,
        alert_type=TASK_ASSIGNED,
        recipient_client_id="<cid>",
        source={"kind": "task", "id": task_id},
        render_ctx={...},   # passed to the appropriate template function
    )
"""

from datetime import datetime, timezone, timedelta

from bson import ObjectId
from logging_config import get_logger
from middleware.db_guard import ScopedDatabase
from models.communication import CommunicationMessage, ALL_ALERT_TYPES
from models.communication_settings import CommunicationSettings
from utils.phone import resolve_whatsapp_number
import utils.whatsapp_templates as templates

logger = get_logger("communication_generator")


async def _get_settings(db: ScopedDatabase, agency_id: str) -> CommunicationSettings:
    doc = await db.communication_settings.find_one({"agency_id": agency_id})
    if doc:
        return CommunicationSettings(**doc)
    return CommunicationSettings(agency_id=agency_id)


def _render_body(alert_type: str, ctx: dict) -> str | None:
    try:
        if alert_type == "task_assigned":
            return templates.task_assigned(**ctx)
        if alert_type == "task_deadline":
            return templates.task_deadline(**ctx)
        if alert_type == "project_confirmation":
            return templates.project_confirmation(**ctx)
        if alert_type == "project_stage_changed":
            return templates.project_stage_changed(**ctx)
        if alert_type == "invoice_sent":
            return templates.invoice_sent(**ctx)
        if alert_type == "invoice_due_soon":
            return templates.invoice_due_soon(**ctx)
        if alert_type == "invoice_overdue":
            return templates.invoice_overdue(**ctx)
        if alert_type == "approval_requested":
            return templates.approval_requested(**ctx)
        if alert_type == "deliverable_uploaded":
            return templates.deliverable_uploaded(**ctx)
        if alert_type == "custom":
            return ctx.get("message_body", "")
        logger.warning(f"Unknown alert_type for rendering: {alert_type}")
        return None
    except Exception as exc:
        logger.error(f"Template render failed for {alert_type}: {exc}", extra={"data": ctx})
        return None


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
    if source_id and alert_type not in ("custom",):
        if await _already_queued_recently(db, alert_type, source_id):
            logger.debug(f"Dedup: {alert_type}/{source_id} already queued in last 24h")
            return None

    # Auto-inject client_name so callers don't have to fetch it separately
    ctx = dict(render_ctx)
    ctx.setdefault("client_name", client_doc.get("name", "there"))
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
