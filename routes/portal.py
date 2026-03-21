from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import RedirectResponse
from datetime import datetime, timezone
from database import projects_collection, clients_collection, configs_collection, tasks_collection, task_history_collection, portal_analytics_collection
from models.project import FeedbackEntry
from models.portal_analytics import PortalAnalyticsEvent
from utils.r2 import generate_presigned_get_url
from logging_config import get_logger

from services.deliverable_sync import on_client_approved, on_client_feedback

router = APIRouter(prefix="/api/portal", tags=["Portal"])
logger = get_logger("portal")


class _PortalDB:
    """Lightweight adapter so sync service can use raw collections (no auth scope needed for portal)."""
    def __init__(self):
        self.projects = projects_collection
        self.tasks = tasks_collection
        self.task_history = task_history_collection

_portal_db = _PortalDB()


def _parse_mongo(data):
    """Lightweight ObjectId → str converter."""
    if isinstance(data, list):
        return [_parse_mongo(item) for item in data]
    if isinstance(data, dict):
        from bson import ObjectId
        return {k: (str(v) if isinstance(v, ObjectId) else _parse_mongo(v)) for k, v in data.items()}
    return data


@router.get("/{token}")
async def get_portal(token: str):
    """Public: Fetch project info for the client portal. No auth required."""
    project = await projects_collection.find_one({"portal_token": token})
    if not project:
        raise HTTPException(status_code=404, detail="Portal not found")

    # Fetch client name
    client_name = None
    client_id = project.get("client_id")
    if client_id:
        from bson import ObjectId
        client = await clients_collection.find_one({"_id": ObjectId(client_id)})
        if client:
            client_name = client.get("name")

    # Fetch org settings for theming
    agency_id = project.get("agency_id")
    org_settings = {}
    if agency_id:
        config_doc = await configs_collection.find_one({"agency_id": agency_id})
        if config_doc:
            org_settings = {
                "org_name": config_doc.get("org_name", ""),
                "theme_mode": config_doc.get("theme_mode", "dark"),
                "accent_color": config_doc.get("accent_color", "#ef4444"),
            }

    return _parse_mongo({
        "project_code": project.get("code"),
        "status": project.get("status"),
        "vertical": project.get("vertical"),
        "client_name": client_name,
        "metadata": project.get("metadata", {}),
        "events": project.get("events", []),
        "portal_deliverables": project.get("portal_deliverables", []),
        "portal_watermark_enabled": project.get("portal_watermark_enabled", False),
        "created_on": project.get("created_on"),
        "org_settings": org_settings,
    })


@router.post("/{token}/deliverables/{deliverable_id}/approve")
async def approve_deliverable(token: str, deliverable_id: str):
    """Public: Client approves a deliverable."""
    project = await projects_collection.find_one({"portal_token": token})
    if not project:
        raise HTTPException(status_code=404, detail="Portal not found")

    result = await projects_collection.update_one(
        {"portal_token": token, "portal_deliverables.id": deliverable_id},
        {
            "$set": {
                "portal_deliverables.$.status": "Approved",
                "portal_deliverables.$.updated_on": datetime.now(timezone.utc),
                "updated_on": datetime.now(timezone.utc),
            }
        }
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Deliverable not found")

    # Sync: check if all portal deliverables for the linked task are approved -> task done
    project_id = str(project["_id"])
    await on_client_approved(_portal_db, project_id, deliverable_id)

    return {"message": "Deliverable approved"}


@router.post("/{token}/deliverables/{deliverable_id}/feedback")
async def submit_client_feedback(
    token: str,
    deliverable_id: str,
    body: dict = Body(...)
):
    """Public: Client submits feedback on a deliverable."""
    project = await projects_collection.find_one({"portal_token": token})
    if not project:
        raise HTTPException(status_code=404, detail="Portal not found")

    message = body.get("message", "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    feedback = FeedbackEntry(
        message=message,
        author_type="client",
        author_name=body.get("author_name"),
        file_id=body.get("file_id"),
    )

    result = await projects_collection.update_one(
        {"portal_token": token, "portal_deliverables.id": deliverable_id},
        {
            "$push": {"portal_deliverables.$.feedback": feedback.model_dump()},
            "$set": {
                "portal_deliverables.$.status": "Changes Requested",
                "portal_deliverables.$.updated_on": datetime.now(timezone.utc),
                "updated_on": datetime.now(timezone.utc),
            },
        }
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Deliverable not found")

    # Sync: set linked task to blocked when client requests changes + sync comment
    project_id = str(project["_id"])
    await on_client_feedback(_portal_db, project_id, deliverable_id, feedback_entry={
        "message": message,
        "file_id": body.get("file_id"),
        "author_name": body.get("author_name"),
    })

    return _parse_mongo(feedback.model_dump())


@router.get("/{token}/deliverables/{deliverable_id}/files/{file_id}/download")
async def download_file(token: str, deliverable_id: str, file_id: str, request: Request):
    """Public: Proxy download with limit enforcement. Returns 302 redirect to presigned URL."""
    project = await projects_collection.find_one({"portal_token": token})
    if not project:
        raise HTTPException(status_code=404, detail="Portal not found")

    deliverable = next(
        (d for d in project.get("portal_deliverables", []) if d.get("id") == deliverable_id),
        None
    )
    if not deliverable:
        raise HTTPException(status_code=404, detail="Deliverable not found")

    if deliverable.get("downloads_disabled"):
        raise HTTPException(status_code=403, detail="Downloads are disabled for this deliverable")

    max_downloads = deliverable.get("max_downloads")
    download_count = deliverable.get("download_count", 0)
    if max_downloads is not None and download_count >= max_downloads:
        raise HTTPException(status_code=403, detail="Download limit reached")

    file_entry = next(
        (f for f in deliverable.get("files", []) if f.get("id") == file_id),
        None
    )
    if not file_entry:
        raise HTTPException(status_code=404, detail="File not found")

    # Increment download count
    await projects_collection.update_one(
        {"portal_token": token, "portal_deliverables.id": deliverable_id},
        {"$inc": {"portal_deliverables.$.download_count": 1}}
    )

    # Log analytics event
    project_id = str(project["_id"])
    analytics_event = PortalAnalyticsEvent(
        project_id=project_id,
        portal_token=token,
        event_type="file_download",
        deliverable_id=deliverable_id,
        file_id=file_id,
        file_name=file_entry.get("file_name"),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await portal_analytics_collection.insert_one(analytics_event.model_dump())

    # Generate presigned URL and redirect
    presigned_url = generate_presigned_get_url(file_entry["r2_key"], expires_in=300)
    return RedirectResponse(url=presigned_url, status_code=302)


@router.post("/{token}/track")
async def track_portal_event(token: str, body: dict = Body(...), request: Request = None):
    """Public: Track portal engagement events (visit, deliverable_view)."""
    project = await projects_collection.find_one({"portal_token": token}, {"_id": 1})
    if not project:
        raise HTTPException(status_code=404, detail="Portal not found")

    event_type = body.get("event_type", "visit")
    if event_type not in ("visit", "deliverable_view"):
        raise HTTPException(status_code=400, detail="Invalid event type")

    analytics_event = PortalAnalyticsEvent(
        project_id=str(project["_id"]),
        portal_token=token,
        event_type=event_type,
        deliverable_id=body.get("deliverable_id"),
        ip_address=request.client.host if request and request.client else None,
        user_agent=request.headers.get("user-agent") if request else None,
    )
    await portal_analytics_collection.insert_one(analytics_event.model_dump())

    return {"message": "Event tracked"}
