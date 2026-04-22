from datetime import datetime, timezone, timedelta
from typing import Optional
import uuid

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, HTTPException, Query

from logging_config import get_logger
from middleware.db_guard import ScopedDatabase
from models.communication import CommunicationMessage, ALL_ALERT_TYPES, ALERT_TYPE_LABELS, CUSTOM
from models.communication_settings import CommunicationSettings, ClientAlertOverride, OperatorAlertOverride
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
    """Manually compose a custom message."""
    message_body = (body.get("message_body") or "").strip()
    recipient_id = body.get("recipient_id", "")
    recipient_name = body.get("recipient_name", "")
    recipient_phone = body.get("recipient_phone", "")

    if not message_body:
        raise HTTPException(status_code=400, detail="message_body is required")
    if not recipient_id:
        raise HTTPException(status_code=400, detail="recipient_id is required")
    if not recipient_phone:
        # Try to resolve from DB — recipient_id is MongoDB _id string from the frontend
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

    base = CommunicationSettings(agency_id=current_user.agency_id).model_dump()
    set_on_insert = {k: v for k, v in base.items() if k not in update_doc}
    await db.communication_settings.update_one(
        {},
        {"$set": update_doc, "$setOnInsert": set_on_insert},
        upsert=True,
    )

    logger.info("Communication settings updated", extra={"data": {"by": current_user.id}})
    return {"message": "Settings updated"}
