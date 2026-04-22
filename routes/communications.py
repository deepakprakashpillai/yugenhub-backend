from datetime import datetime, timezone, timedelta
from typing import Optional
import uuid

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, HTTPException, Query

from logging_config import get_logger
from middleware.db_guard import ScopedDatabase
from models.communication import CommunicationMessage, ALL_ALERT_TYPES, ALERT_TYPE_LABELS, CUSTOM
from models.communication_settings import CommunicationSettings, ClientAlertOverride, OperatorAlertOverride
from models.communication_template import CommunicationTemplate, ALERT_TYPE_VARIABLES, DEFAULT_TEMPLATES
from models.scheduler_config import SchedulerConfig
from models.user import UserModel
from routes.deps import get_db, require_communications_access, require_role, get_current_user
from utils.whatsapp_sender import get_sender

logger = get_logger("communications")

router = APIRouter(prefix="/api/communications", tags=["communications"])

_dep = require_communications_access()


def _parse_mongo(data):
    if isinstance(data, list):
        return [_parse_mongo(item) for item in data]
    if isinstance(data, dict):
        return {k: (str(v) if isinstance(v, ObjectId) else _parse_mongo(v)) for k, v in data.items()}
    return data


# ─── Alert types ──────────────────────────────────────────────────────────────

@router.get("/alert-types")
async def list_alert_types(current_user: UserModel = Depends(_dep)):
    return [{"value": t, "label": ALERT_TYPE_LABELS.get(t, t)} for t in ALL_ALERT_TYPES]


# ─── Message queue ────────────────────────────────────────────────────────────

@router.get("/messages")
async def list_messages(
    alert_type: Optional[str] = None,
    recipient_id: Optional[str] = None,
    status: Optional[str] = None,
    send_channel: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    sort_by: str = Query("created_at", description="created_at or sent_at"),
    order: str = Query("desc", description="asc or desc"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, le=200),
    current_user: UserModel = Depends(_dep),
    db: ScopedDatabase = Depends(get_db),
):
    query: dict = {}

    if alert_type:
        query["alert_type"] = alert_type
    if recipient_id:
        query["recipient_id"] = recipient_id
    if status:
        query["status"] = status
    if send_channel in ("manual", "automation"):
        query["send_channel"] = send_channel
    if date_from or date_to:
        date_filter: dict = {}
        if date_from:
            try:
                date_filter["$gte"] = datetime.fromisoformat(date_from.replace("Z", "+00:00"))
            except ValueError:
                pass
        if date_to:
            try:
                # Use exclusive upper bound on next day so the full date_to day is included
                date_filter["$lt"] = datetime.fromisoformat(date_to.replace("Z", "+00:00")) + timedelta(days=1)
            except ValueError:
                pass
        if date_filter:
            query["created_at"] = date_filter

    # Operator-side exclusion (members only — owner/admin see everything)
    if current_user.role not in ("owner", "admin"):
        settings_doc = await db.communication_settings.find_one({})
        if settings_doc:
            op_override = (settings_doc.get("operator_overrides") or {}).get(current_user.id)
            if op_override:
                if op_override.get("excluded"):
                    return {"messages": [], "total": 0, "page": page, "limit": limit}
                hidden = op_override.get("hidden_types", [])
                if hidden:
                    if alert_type:
                        # User filtered to a specific type — enforce that it's not hidden
                        if alert_type in hidden:
                            return {"messages": [], "total": 0, "page": page, "limit": limit}
                        # else: specific type not hidden; no extra filter needed
                    else:
                        query["alert_type"] = {"$nin": hidden}

    sort_dir = -1 if order == "desc" else 1
    primary_sort = sort_by if sort_by in ("created_at", "sent_at") else "created_at"
    sort_spec = [(primary_sort, sort_dir)] if primary_sort == "created_at" else [(primary_sort, sort_dir), ("created_at", sort_dir)]
    skip = (page - 1) * limit

    cursor = db.communications_messages.find(query).sort(sort_spec).skip(skip).limit(limit)
    messages = await cursor.to_list(length=limit)
    total = await db.communications_messages.count_documents(query)

    return {"messages": _parse_mongo(messages), "total": total, "page": page, "limit": limit}


@router.post("/messages", status_code=201)
async def create_message(
    body: dict = Body(...),
    current_user: UserModel = Depends(_dep),
    db: ScopedDatabase = Depends(get_db),
):
    """Manually compose a custom message. Supports recipient_type 'client' (default) or 'associate'."""
    message_body = (body.get("message_body") or "").strip()
    recipient_id = body.get("recipient_id", "")
    recipient_type = body.get("recipient_type", "client")
    recipient_name = body.get("recipient_name", "")
    recipient_phone = body.get("recipient_phone", "")

    if not message_body:
        raise HTTPException(status_code=400, detail="message_body is required")
    if not recipient_id:
        raise HTTPException(status_code=400, detail="recipient_id is required")

    if not recipient_phone:
        if recipient_type == "associate":
            if ObjectId.is_valid(recipient_id):
                assoc_doc = await db.associates.find_one({"_id": ObjectId(recipient_id)})
            else:
                assoc_doc = await db.associates.find_one({"id": recipient_id})
            if assoc_doc:
                recipient_phone = assoc_doc.get("phone_number", "").strip()
                recipient_name = recipient_name or assoc_doc.get("name", "")
        else:
            if ObjectId.is_valid(recipient_id):
                client_doc = await db.clients.find_one({"_id": ObjectId(recipient_id)})
            else:
                client_doc = await db.clients.find_one({"id": recipient_id})
            if client_doc:
                from utils.phone import resolve_whatsapp_number
                recipient_phone = resolve_whatsapp_number(client_doc) or ""
                recipient_name = recipient_name or client_doc.get("name", "")

    if not recipient_phone:
        raise HTTPException(status_code=400, detail="recipient_phone is required and could not be resolved")

    msg = CommunicationMessage(
        agency_id=current_user.agency_id,
        recipient_type=recipient_type,
        recipient_id=recipient_id,
        recipient_name=recipient_name,
        recipient_phone=recipient_phone,
        message_body=message_body,
        alert_type=CUSTOM,
        source={"kind": "custom", "id": str(uuid.uuid4())},
        created_by=current_user.id,
    )
    await db.communications_messages.insert_one(msg.model_dump())
    logger.info("Custom communication message created", extra={"data": {"id": msg.id, "by": current_user.id}})
    return _parse_mongo(msg.model_dump())


@router.patch("/messages/{message_id}")
async def edit_message(
    message_id: str,
    body: dict = Body(...),
    current_user: UserModel = Depends(_dep),
    db: ScopedDatabase = Depends(get_db),
):
    """Edit a pending message's body before sending."""
    msg = await db.communications_messages.find_one({"id": message_id})
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if msg.get("status") != "pending":
        raise HTTPException(status_code=400, detail="Only pending messages can be edited")

    new_body = (body.get("message_body") or "").strip()
    if not new_body:
        raise HTTPException(status_code=400, detail="message_body cannot be empty")

    await db.communications_messages.update_one(
        {"id": message_id},
        {"$set": {"message_body": new_body, "edited": True}},
    )
    logger.info("Communication message edited", extra={"data": {"id": message_id, "by": current_user.id}})
    return {"message": "Updated"}


@router.delete("/messages/{message_id}", status_code=204)
async def delete_message(
    message_id: str,
    current_user: UserModel = Depends(_dep),
    db: ScopedDatabase = Depends(get_db),
):
    result = await db.communications_messages.delete_one({"id": message_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Message not found")
    logger.info("Communication message deleted", extra={"data": {"id": message_id, "by": current_user.id}})


@router.post("/messages/{message_id}/prepare-send")
async def prepare_send(
    message_id: str,
    current_user: UserModel = Depends(_dep),
    db: ScopedDatabase = Depends(get_db),
):
    """
    Single choke-point for sending. Phase 1: returns wa.me URL and marks sent.
    Phase 2: swap get_sender() to AutomationSender — this route signature stays the same.
    """
    msg = await db.communications_messages.find_one({"id": message_id})
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if msg.get("status") == "sent":
        raise HTTPException(status_code=400, detail="Message already sent")

    sender = get_sender()
    result = await sender.send(msg["recipient_phone"], msg["message_body"])

    if not result.success:
        await db.communications_messages.update_one(
            {"id": message_id},
            {"$set": {"status": "failed", "last_error": result.error}},
        )
        raise HTTPException(status_code=502, detail=f"Send failed: {result.error}")

    now = datetime.now(timezone.utc)
    await db.communications_messages.update_one(
        {"id": message_id},
        {"$set": {"status": "sent", "sent_at": now, "send_channel": "manual"}},
    )
    logger.info("Communication message sent", extra={"data": {"id": message_id, "channel": "manual"}})
    return {"wa_url": result.wa_url, "message_id": message_id, "sent_at": now.isoformat()}


@router.post("/messages/{message_id}/resend")
async def resend_message(
    message_id: str,
    current_user: UserModel = Depends(_dep),
    db: ScopedDatabase = Depends(get_db),
):
    """Re-open the wa.me link for a previously sent (or failed) message without status restriction."""
    msg = await db.communications_messages.find_one({"id": message_id})
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    sender = get_sender()
    result = await sender.send(msg["recipient_phone"], msg["message_body"])

    if not result.success:
        await db.communications_messages.update_one(
            {"id": message_id},
            {"$set": {"status": "failed", "last_error": result.error}},
        )
        raise HTTPException(status_code=502, detail=f"Send failed: {result.error}")

    now = datetime.now(timezone.utc)
    await db.communications_messages.update_one(
        {"id": message_id},
        {"$set": {"status": "sent", "sent_at": now, "send_channel": "manual", "last_error": None}},
    )
    logger.info("Communication message resent", extra={"data": {"id": message_id, "by": current_user.id}})
    return {"wa_url": result.wa_url, "message_id": message_id, "sent_at": now.isoformat()}


# ─── Settings ─────────────────────────────────────────────────────────────────

@router.get("/settings")
async def get_settings(
    current_user: UserModel = Depends(_dep),
    db: ScopedDatabase = Depends(get_db),
):
    doc = await db.communication_settings.find_one({})
    if not doc:
        default = CommunicationSettings(agency_id=current_user.agency_id)
        return _parse_mongo(default.model_dump())
    return _parse_mongo(doc)


@router.put("/settings")
async def update_settings(
    body: dict = Body(...),
    current_user: UserModel = Depends(require_role("owner", "admin")),
    db: ScopedDatabase = Depends(get_db),
):
    # Validate globally_enabled_types
    enabled_types = body.get("globally_enabled_types")
    if enabled_types is not None:
        if not isinstance(enabled_types, list):
            raise HTTPException(status_code=400, detail="globally_enabled_types must be a list")
        enabled_types = [t for t in enabled_types if t in ALL_ALERT_TYPES]

    # Parse client_overrides
    raw_client_overrides = body.get("client_overrides", {})
    client_overrides = {}
    for cid, val in raw_client_overrides.items():
        client_overrides[cid] = ClientAlertOverride(**val).model_dump()

    # Parse operator_overrides
    raw_op_overrides = body.get("operator_overrides", {})
    op_overrides = {}
    for uid, val in raw_op_overrides.items():
        op_overrides[uid] = OperatorAlertOverride(**val).model_dump()

    update_doc: dict = {
        "updated_at": datetime.now(timezone.utc),
        "client_overrides": client_overrides,
        "operator_overrides": op_overrides,
    }
    if enabled_types is not None:
        update_doc["globally_enabled_types"] = enabled_types
    if "team_notifications_enabled" in body:
        update_doc["team_notifications_enabled"] = bool(body["team_notifications_enabled"])

    base = CommunicationSettings(agency_id=current_user.agency_id).model_dump()
    set_on_insert = {k: v for k, v in base.items() if k not in update_doc}
    await db.communication_settings.update_one(
        {},
        {"$set": update_doc, "$setOnInsert": set_on_insert},
        upsert=True,
    )

    logger.info("Communication settings updated", extra={"data": {"by": current_user.id}})
    return {"message": "Settings updated"}


# ─── Templates ────────────────────────────────────────────────────────────────

@router.get("/templates")
async def list_templates(
    current_user: UserModel = Depends(_dep),
    db: ScopedDatabase = Depends(get_db),
):
    """Return all alert types with their current template (custom or default)."""
    custom_docs = await db.communication_templates.find({}).to_list(length=None)
    custom_by_type = {d["alert_type"]: d for d in custom_docs}

    result = []
    for alert_type in ALL_ALERT_TYPES:
        if alert_type == CUSTOM:
            continue
        doc = custom_by_type.get(alert_type)
        result.append({
            "alert_type": alert_type,
            "label": ALERT_TYPE_LABELS.get(alert_type, alert_type),
            "body_template": doc["body_template"] if doc else DEFAULT_TEMPLATES.get(alert_type, ""),
            "is_custom": doc is not None,
            "default_template": DEFAULT_TEMPLATES.get(alert_type, ""),
            "variables": ALERT_TYPE_VARIABLES.get(alert_type, []),
        })
    return result


@router.put("/templates/{alert_type}")
async def upsert_template(
    alert_type: str,
    body: dict = Body(...),
    current_user: UserModel = Depends(require_role("owner", "admin")),
    db: ScopedDatabase = Depends(get_db),
):
    if alert_type not in ALL_ALERT_TYPES or alert_type == CUSTOM:
        raise HTTPException(status_code=400, detail="Invalid alert type")
    body_template = (body.get("body_template") or "").strip()
    if not body_template:
        raise HTTPException(status_code=400, detail="body_template is required")

    tpl = CommunicationTemplate(
        agency_id=current_user.agency_id,
        alert_type=alert_type,
        body_template=body_template,
        updated_by=current_user.id,
    )
    await db.communication_templates.update_one(
        {"alert_type": alert_type},
        {"$set": tpl.model_dump()},
        upsert=True,
    )
    logger.info("Communication template saved", extra={"data": {"alert_type": alert_type, "by": current_user.id}})
    return {"alert_type": alert_type, "saved": True}


@router.delete("/templates/{alert_type}", status_code=204)
async def reset_template(
    alert_type: str,
    current_user: UserModel = Depends(require_role("owner", "admin")),
    db: ScopedDatabase = Depends(get_db),
):
    await db.communication_templates.delete_one({"alert_type": alert_type})
    logger.info("Communication template reset to default", extra={"data": {"alert_type": alert_type}})


# ─── Scheduler config ─────────────────────────────────────────────────────────

@router.get("/scheduler-config")
async def get_scheduler_config(
    current_user: UserModel = Depends(_dep),
    db: ScopedDatabase = Depends(get_db),
):
    doc = await db.scheduler_configs.find_one({})
    if not doc:
        return _parse_mongo(SchedulerConfig(agency_id=current_user.agency_id).model_dump())
    return _parse_mongo(doc)


@router.patch("/scheduler-config")
async def update_scheduler_config(
    body: dict = Body(...),
    current_user: UserModel = Depends(require_role("owner", "admin")),
    db: ScopedDatabase = Depends(get_db),
):
    allowed = {
        "task_deadline_enabled", "task_deadline_hours_before",
        "invoice_scan_enabled", "invoice_due_soon_days_before",
    }
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    updates["updated_at"] = datetime.now(timezone.utc)
    base = SchedulerConfig(agency_id=current_user.agency_id).model_dump()
    await db.scheduler_configs.update_one(
        {},
        {"$set": updates, "$setOnInsert": base},
        upsert=True,
    )
    logger.info("Scheduler config updated", extra={"data": {"by": current_user.id}})
    return {"updated": True}


@router.post("/scheduler/run-now/{job_name}")
async def scheduler_run_now(
    job_name: str,
    current_user: UserModel = Depends(require_role("owner", "admin")),
    db: ScopedDatabase = Depends(get_db),
):
    """Trigger an immediate scan for the current agency."""
    from services.communication_scheduler import (
        run_task_deadline_for_agency,
        run_task_deadline_associate_for_agency,
        run_invoice_scan_for_agency,
    )

    cfg_doc = await db.scheduler_configs.find_one({})
    _known = {"task_deadline_enabled", "task_deadline_hours_before", "invoice_scan_enabled", "invoice_due_soon_days_before"}
    cfg_data = {"agency_id": current_user.agency_id, **{k: v for k, v in (cfg_doc or {}).items() if k in _known}}
    cfg = SchedulerConfig(**cfg_data)

    if job_name == "task_deadline":
        count = await run_task_deadline_for_agency(db, current_user.agency_id, cfg.task_deadline_hours_before)
        return {"job": job_name, "queued": count}
    if job_name == "task_deadline_associate":
        count = await run_task_deadline_associate_for_agency(db, current_user.agency_id, cfg.task_deadline_hours_before)
        return {"job": job_name, "queued": count}
    if job_name == "invoice":
        count = await run_invoice_scan_for_agency(db, current_user.agency_id, cfg.invoice_due_soon_days_before)
        return {"job": job_name, "queued": count}
    raise HTTPException(status_code=400, detail="Unknown job name")


# ─── Blast ────────────────────────────────────────────────────────────────────

_BLAST_TYPES = {"task_deadline", "invoice_due_soon", "invoice_overdue", "approval_requested"}


async def _build_blast_candidates(
    alert_type: str,
    agency_id: str,
    db: ScopedDatabase,
) -> list[dict]:
    """Return list of candidate blast items without enqueuing."""
    from database import db as raw_db
    from bson import ObjectId as ObjId
    from services.communication_generator import _render_body, _render_custom_template

    now = datetime.now(timezone.utc)
    candidates = []

    # Resolve custom template if any
    custom_tpl = await db.communication_templates.find_one({"alert_type": alert_type})

    def _render(ctx: dict) -> str:
        if custom_tpl and custom_tpl.get("body_template"):
            return _render_custom_template(custom_tpl["body_template"], ctx)
        return _render_body(alert_type, ctx) or ""

    if alert_type == "task_deadline":
        window_end = now + timedelta(hours=24)
        cursor = raw_db.tasks.find({
            "$or": [{"studio_id": agency_id}, {"agency_id": agency_id}],
            "due_date": {"$gte": now, "$lte": window_end},
            "status": {"$nin": ["done", "completed"]},
        })
        async for task in cursor:
            project_id = task.get("project_id")
            client_id = None
            project_code = ""
            if project_id:
                try:
                    project = await raw_db.projects.find_one({"_id": ObjId(project_id)})
                except Exception:
                    project = await raw_db.projects.find_one({"id": project_id})
                if project:
                    client_id = project.get("client_id")
                    project_code = project.get("code", "")
            if not client_id:
                continue
            if ObjId.is_valid(client_id):
                client = await raw_db.clients.find_one({"_id": ObjId(client_id)})
            else:
                client = await raw_db.clients.find_one({"id": client_id})
            if not client:
                continue
            ctx = {
                "client_name": client.get("name", "there"),
                "task_title": task.get("title", ""),
                "project_code": project_code,
                "due_date": task.get("due_date"),
                "agency_name": "",
            }
            candidates.append({
                "recipient_id": str(client.get("_id", client.get("id", ""))),
                "recipient_name": client.get("name", ""),
                "recipient_phone": client.get("whatsapp_number") or client.get("phone", ""),
                "message_preview": _render(ctx),
                "source": {"kind": "task", "id": str(task.get("id", task.get("_id", "")))},
                "render_ctx": {k: str(v) if hasattr(v, "isoformat") else v for k, v in ctx.items()},
            })

    elif alert_type in ("invoice_due_soon", "invoice_overdue"):
        cursor = raw_db.finance_invoices.find({"agency_id": agency_id, "status": {"$nin": ["paid", "cancelled"]}})
        async for invoice in cursor:
            due_date = invoice.get("due_date")
            if not due_date:
                continue
            if isinstance(due_date, str):
                try:
                    due_date = datetime.fromisoformat(due_date.replace("Z", "+00:00"))
                except ValueError:
                    continue
            is_due_soon = now <= due_date <= now + timedelta(days=3)
            is_overdue = due_date < now
            if alert_type == "invoice_due_soon" and not is_due_soon:
                continue
            if alert_type == "invoice_overdue" and not is_overdue:
                continue

            client_id = invoice.get("client_id")
            if not client_id:
                continue
            if ObjId.is_valid(client_id):
                client = await raw_db.clients.find_one({"_id": ObjId(client_id)})
            else:
                client = await raw_db.clients.find_one({"id": client_id})
            if not client:
                continue
            ctx = {
                "client_name": client.get("name", "there"),
                "invoice_no": invoice.get("invoice_no", ""),
                "amount": invoice.get("total_amount", 0),
                "currency": invoice.get("currency", "INR"),
                "due_date": due_date,
                "agency_name": "",
            }
            candidates.append({
                "recipient_id": str(client.get("_id", client.get("id", ""))),
                "recipient_name": client.get("name", ""),
                "recipient_phone": client.get("whatsapp_number") or client.get("phone", ""),
                "message_preview": _render(ctx),
                "source": {"kind": "invoice", "id": str(invoice.get("id", invoice.get("_id", "")))},
                "render_ctx": {k: str(v) if hasattr(v, "isoformat") else v for k, v in ctx.items()},
            })

    elif alert_type == "approval_requested":
        cursor = raw_db.projects.find({"agency_id": agency_id})
        async for project in cursor:
            client_id = project.get("client_id")
            if not client_id:
                continue
            for pd in project.get("portal_deliverables", []):
                if pd.get("status") not in ("Pending", "pending"):
                    continue
                if ObjId.is_valid(client_id):
                    client = await raw_db.clients.find_one({"_id": ObjId(client_id)})
                else:
                    client = await raw_db.clients.find_one({"id": client_id})
                if not client:
                    continue
                ctx = {
                    "client_name": client.get("name", "there"),
                    "project_code": project.get("code", ""),
                    "deliverable_name": pd.get("name", ""),
                    "agency_name": "",
                }
                candidates.append({
                    "recipient_id": str(client.get("_id", client.get("id", ""))),
                    "recipient_name": client.get("name", ""),
                    "recipient_phone": client.get("whatsapp_number") or client.get("phone", ""),
                    "message_preview": _render(ctx),
                    "source": {"kind": "project", "id": str(project.get("_id", ""))},
                    "render_ctx": ctx,
                })

    return candidates


@router.post("/blast/preview")
async def blast_preview(
    body: dict = Body(...),
    current_user: UserModel = Depends(require_role("owner", "admin")),
    db: ScopedDatabase = Depends(get_db),
):
    alert_type = body.get("alert_type", "")
    if alert_type not in _BLAST_TYPES:
        raise HTTPException(status_code=400, detail=f"Blast not supported for alert type: {alert_type}")

    items = await _build_blast_candidates(alert_type, current_user.agency_id, db)
    return {"items": items, "count": len(items)}


@router.post("/blast/send")
async def blast_send(
    body: dict = Body(...),
    current_user: UserModel = Depends(require_role("owner", "admin")),
    db: ScopedDatabase = Depends(get_db),
):
    alert_type = body.get("alert_type", "")
    if alert_type not in _BLAST_TYPES:
        raise HTTPException(status_code=400, detail=f"Blast not supported for alert type: {alert_type}")

    items = body.get("items", [])
    if not items:
        raise HTTPException(status_code=400, detail="items list is required")

    from services.communication_generator import enqueue_message as _enqueue
    queued = 0
    skipped = 0

    for item in items:
        recipient_id = item.get("recipient_id", "")
        source = item.get("source", {})
        render_ctx = item.get("render_ctx", {})

        msg = await _enqueue(
            db=db,
            agency_id=current_user.agency_id,
            alert_type=alert_type,
            recipient_client_id=recipient_id,
            source=source,
            render_ctx=render_ctx,
            created_by=current_user.id,
            skip_dedup=False,
        )
        if msg:
            queued += 1
        else:
            skipped += 1

    logger.info("Blast sent", extra={"data": {"alert_type": alert_type, "queued": queued, "skipped": skipped}})
    return {"queued": queued, "skipped": skipped}
