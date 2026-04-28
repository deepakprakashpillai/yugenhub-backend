from fastapi import APIRouter, Body, Depends, HTTPException, BackgroundTasks, Query
from datetime import datetime, timezone
import uuid
from bson import ObjectId
from database import (
    projects_collection, configs_collection,
    tasks_collection, task_history_collection,
    notifications_collection, users_collection,
    media_items_collection, media_folders_collection,
)
from models.project import FeedbackEntry, DeliverableFile
from models.notification import NotificationModel
from utils.google_verify import verify_google_id_token
from utils.r2 import (
    create_multipart_upload, generate_presigned_upload_part_url,
    complete_multipart_upload, abort_multipart_upload,
)
from utils.email import send_email
from services.deliverable_sync import on_portal_file_added
from config import config
from logging_config import get_logger
from middleware.rate_limiter import (
    check_editor_identify_limit,
    check_editor_parts_limit,
    check_editor_read_limit,
    check_editor_write_limit,
)

router = APIRouter(prefix="/api/editor", tags=["Editor Portal"])
logger = get_logger("editor")


class _EditorDB:
    """Adapter giving sync-service helpers access to raw collections (no auth scope)."""
    def __init__(self):
        self.projects = projects_collection
        self.tasks = tasks_collection
        self.task_history = task_history_collection
        self.notifications = notifications_collection
        self.users = users_collection
        self.media_items = media_items_collection
        self.media_folders = media_folders_collection

_editor_db = _EditorDB()


def _parse_mongo(data):
    """Lightweight ObjectId → str converter."""
    if isinstance(data, list):
        return [_parse_mongo(item) for item in data]
    if isinstance(data, dict):
        from bson import ObjectId
        return {k: (str(v) if isinstance(v, ObjectId) else _parse_mongo(v)) for k, v in data.items()}
    return data


async def _get_project_by_editor_token(token: str):
    """Find project by editor token string. Returns (project, token_entry) or raises 404."""
    project = await projects_collection.find_one({"editor_tokens.token": token})
    if not project:
        raise HTTPException(status_code=404, detail="Editor link not found")

    token_entry = next(
        (t for t in project.get("editor_tokens", []) if t["token"] == token),
        None,
    )
    if not token_entry:
        raise HTTPException(status_code=404, detail="Editor link not found")

    return project, token_entry


@router.get("/{token}")
async def get_editor_data(token: str, _: None = Depends(check_editor_read_limit)):
    """Public: Return scoped project data for an editor link."""
    project, token_entry = await _get_project_by_editor_token(token)

    scoped_pd_ids = set(token_entry.get("deliverable_ids", []))

    # Filter deliverables to scoped ones
    scoped_deliverables = [
        pd for pd in project.get("portal_deliverables", [])
        if pd.get("id") in scoped_pd_ids
    ]

    # Filter events to those referenced by scoped deliverables
    scoped_event_ids = {pd.get("event_id") for pd in scoped_deliverables if pd.get("event_id")}
    scoped_events = [
        e for e in project.get("events", [])
        if e.get("id") in scoped_event_ids
    ]

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
        "org_settings": org_settings,
        "editor_token_label": token_entry.get("label", ""),
        "deliverables": scoped_deliverables,
        "events": scoped_events,
        "portal_watermark_text": project.get("portal_watermark_text"),
    })


@router.post("/{token}/identify")
async def identify_editor(token: str, body: dict = Body(...), _: None = Depends(check_editor_identify_limit)):
    """Public: Verify Google ID token for editor identity. No DB writes."""
    await _get_project_by_editor_token(token)

    credential = body.get("credential")
    if not credential:
        raise HTTPException(status_code=400, detail="credential is required")

    id_info = verify_google_id_token(credential)
    if not id_info:
        raise HTTPException(status_code=401, detail="Invalid Google credential")

    return {
        "email": id_info.get("email"),
        "name": id_info.get("name"),
        "picture": id_info.get("picture"),
    }


@router.post("/{token}/deliverables/{del_id}/comment")
async def post_editor_comment(token: str, del_id: str, body: dict = Body(...), _: None = Depends(check_editor_write_limit)):
    """Public: Post a comment as an editor. author_type is always hardcoded server-side."""
    project, token_entry = await _get_project_by_editor_token(token)

    if del_id not in token_entry.get("deliverable_ids", []):
        raise HTTPException(status_code=403, detail="Deliverable not in scope for this editor link")

    message = body.get("message", "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    feedback = FeedbackEntry(
        message=message,
        author_type="editor",  # hardcoded — never trust the client value
        author_name=body.get("author_name"),
        author_email=body.get("author_email"),
        file_id=body.get("file_id"),
    )

    result = await projects_collection.update_one(
        {"editor_tokens.token": token, "portal_deliverables.id": del_id},
        {
            "$push": {"portal_deliverables.$.feedback": feedback.model_dump()},
            "$set": {
                "portal_deliverables.$.updated_on": datetime.now(timezone.utc),
                "updated_on": datetime.now(timezone.utc),
            },
        },
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Deliverable not found")

    logger.info(f"Editor comment posted on deliverable {del_id}")
    return _parse_mongo(feedback.model_dump())


# ── Upload endpoints ───────────────────────────────────────────────────────────

@router.post("/{token}/deliverables/{del_id}/upload/init")
async def init_upload(token: str, del_id: str, body: dict = Body(...), _: None = Depends(check_editor_write_limit)):
    """Public: Initiate a multipart upload for a deliverable file."""
    project, token_entry = await _get_project_by_editor_token(token)

    if del_id not in token_entry.get("deliverable_ids", []):
        raise HTTPException(status_code=403, detail="Deliverable not in scope for this editor link")

    file_name = body.get("file_name")
    content_type = body.get("content_type", "application/octet-stream")
    file_size = body.get("file_size", 0)

    if not file_name:
        raise HTTPException(status_code=400, detail="file_name is required")
    if file_size > 5 * 1024 ** 3:
        raise HTTPException(status_code=413, detail="File exceeds 5 GB limit")

    agency_id = project.get("agency_id", "default")
    project_id = str(project["_id"])
    r2_key = f"deliverables/{agency_id}/{project_id}/{uuid.uuid4()}_{file_name}"

    upload_id = create_multipart_upload(r2_key, content_type)
    return {"upload_id": upload_id, "key": r2_key, "part_size": 100 * 1024 * 1024}


@router.get("/{token}/upload/part-url")
async def get_part_url(
    token: str,
    key: str = Query(...),
    upload_id: str = Query(...),
    part_number: int = Query(...),
    _: None = Depends(check_editor_parts_limit),
):
    """Public: Return a presigned URL for uploading a single multipart chunk."""
    project, _ = await _get_project_by_editor_token(token)
    agency_id = project.get("agency_id", "default")
    project_id = str(project["_id"])
    if not key.startswith(f"deliverables/{agency_id}/{project_id}/"):
        raise HTTPException(status_code=403, detail="Key not in scope for this editor link")
    url = generate_presigned_upload_part_url(key, upload_id, part_number)
    return {"url": url}


@router.post("/{token}/deliverables/{del_id}/upload/complete")
async def complete_upload(
    token: str,
    del_id: str,
    background_tasks: BackgroundTasks,
    body: dict = Body(...),
    _: None = Depends(check_editor_write_limit),
):
    """Public: Finalize multipart upload and register file with full side effects."""
    project, token_entry = await _get_project_by_editor_token(token)

    if del_id not in token_entry.get("deliverable_ids", []):
        raise HTTPException(status_code=403, detail="Deliverable not in scope for this editor link")

    key = body.get("key")
    upload_id = body.get("upload_id")
    parts = body.get("parts", [])
    file_name = body.get("file_name", "file")
    content_type = body.get("content_type", "application/octet-stream")
    editor_email = body.get("editor_email", "")
    editor_name = body.get("editor_name", "Editor")

    if not key or not upload_id:
        raise HTTPException(status_code=400, detail="key and upload_id are required")

    r2_url = complete_multipart_upload(key, upload_id, parts)

    is_image = content_type.startswith("image/")
    is_video = content_type.startswith("video/")

    file_entry = DeliverableFile(
        file_name=file_name,
        content_type=content_type,
        r2_key=key,
        r2_url=r2_url,
        uploaded_by=editor_email,
        uploaded_by_name=editor_name,
        thumbnail_status="pending" if (is_image or is_video) else "n/a",
        watermark_status="pending" if is_video else "n/a",
        preview_status="pending" if is_image else "n/a",
    )

    now = datetime.now(timezone.utc)
    project_id = str(project["_id"])

    await projects_collection.update_one(
        {"editor_tokens.token": token, "portal_deliverables.id": del_id},
        {
            "$push": {"portal_deliverables.$.files": file_entry.model_dump()},
            "$set": {
                "portal_deliverables.$.updated_on": now,
                "updated_on": now,
            },
        },
    )

    # Flip Pending → Uploaded
    await on_portal_file_added(_editor_db, project_id, del_id)

    # Re-fetch project for side effects
    project = await projects_collection.find_one(
        {"_id": ObjectId(project_id)},
        {"agency_id": 1, "code": 1, "metadata": 1, "portal_watermark_enabled": 1,
         "portal_watermark_text": 1, "portal_deliverables": 1},
    )
    agency_id = project.get("agency_id", "default") if project else "default"

    # Auto-create MediaItem (non-fatal)
    try:
        from services.media_folders import get_or_create_system_folder
        from models.media import MediaItem as MediaItemModel

        project_label = (
            (project.get("code") or project.get("metadata", {}).get("project_type", "Project"))
            if project else "Project"
        )
        deliverable_title = del_id
        if project:
            for pd in project.get("portal_deliverables", []):
                if pd["id"] == del_id:
                    deliverable_title = pd.get("title", del_id)
                    break

        folder_id = await get_or_create_system_folder(
            agency_id,
            ["Deliverables", project_label, deliverable_title],
            _editor_db,
        )
        media_item = MediaItemModel(
            agency_id=agency_id,
            folder_id=folder_id,
            name=file_name,
            r2_key=key,
            r2_url=r2_url,
            content_type=content_type,
            size_bytes=0,
            thumbnail_status="pending" if (is_image or is_video) else "n/a",
            preview_status="pending" if is_image else "n/a",
            watermark_status="pending" if is_video else "n/a",
            source="deliverable",
            source_project_id=project_id,
            source_deliverable_id=del_id,
            uploaded_by=editor_email,
            status="active",
        )
        await media_items_collection.insert_one(media_item.model_dump())
        await projects_collection.update_one(
            {"_id": ObjectId(project_id)},
            {"$set": {"portal_deliverables.$[d].files.$[f].media_item_id": media_item.id}},
            array_filters=[{"d.id": del_id}, {"f.id": file_entry.id}],
        )
    except Exception:
        logger.error("Failed to create MediaItem for editor upload", exc_info=True)

    # Background processing
    if is_image or is_video:
        from services.media_processing import process_thumbnail, process_watermark
        background_tasks.add_task(
            process_thumbnail, project_id, del_id, file_entry.id, key, content_type, agency_id
        )
        if is_video and project and project.get("portal_watermark_enabled"):
            watermark_text = project.get("portal_watermark_text") or "Protected"
            background_tasks.add_task(
                process_watermark, project_id, del_id, file_entry.id, key, watermark_text, agency_id
            )

    # Notify agency (non-fatal)
    try:
        pd_entry = next(
            (d for d in (project.get("portal_deliverables", []) if project else []) if d["id"] == del_id),
            None,
        )
        task_id = pd_entry.get("task_id") if pd_entry else None
        deliverable_title = pd_entry.get("title", del_id) if pd_entry else del_id

        if task_id:
            task = await tasks_collection.find_one({"id": task_id})
            recipient_id = (task.get("assigned_to") or task.get("incharge_user_id")) if task else None
            if recipient_id:
                notification = NotificationModel(
                    user_id=recipient_id,
                    type="task_updated",
                    title="Editor Upload",
                    message=f"Editor {editor_name} uploaded {file_name} to {deliverable_title}",
                    resource_type="task",
                    resource_id=task_id,
                    metadata={"project_id": project_id},
                )
                await notifications_collection.insert_one(notification.model_dump())

                user_doc = await users_collection.find_one({"id": recipient_id})
                if user_doc and user_doc.get("email"):
                    send_email(
                        to_email=user_doc["email"],
                        subject=f"Editor upload: {file_name}",
                        html_content=(
                            f"<p>Editor <strong>{editor_name}</strong> ({editor_email}) uploaded "
                            f"<strong>{file_name}</strong> to deliverable "
                            f"<strong>{deliverable_title}</strong>.</p>"
                        ),
                    )
    except Exception:
        logger.error("Failed to notify agency about editor upload", exc_info=True)

    logger.info(f"Editor upload complete: {file_name} → deliverable {del_id}")
    return {"file": _parse_mongo(file_entry.model_dump())}


@router.post("/{token}/deliverables/{del_id}/upload/abort", status_code=204)
async def abort_upload_route(token: str, del_id: str, body: dict = Body(...), _: None = Depends(check_editor_write_limit)):
    """Public: Abort an in-progress multipart upload, releasing stored parts."""
    await _get_project_by_editor_token(token)
    key = body.get("key")
    upload_id = body.get("upload_id")
    if key and upload_id:
        abort_multipart_upload(key, upload_id)


# ── Version replacement endpoints ─────────────────────────────────────────────

@router.post("/{token}/deliverables/{del_id}/files/{file_id}/version/init")
async def init_version_upload(token: str, del_id: str, file_id: str, body: dict = Body(...), _: None = Depends(check_editor_write_limit)):
    """Public: Initiate a multipart upload for a version replacement."""
    project, token_entry = await _get_project_by_editor_token(token)

    if del_id not in token_entry.get("deliverable_ids", []):
        raise HTTPException(status_code=403, detail="Deliverable not in scope for this editor link")

    # Validate file exists
    deliverable = next((d for d in project.get("portal_deliverables", []) if d["id"] == del_id), None)
    if not deliverable:
        raise HTTPException(status_code=404, detail="Deliverable not found")
    if not any(f["id"] == file_id for f in deliverable.get("files", [])):
        raise HTTPException(status_code=404, detail="File not found")

    file_name = body.get("file_name")
    content_type = body.get("content_type", "application/octet-stream")
    file_size = body.get("file_size", 0)

    if not file_name:
        raise HTTPException(status_code=400, detail="file_name is required")
    if file_size > 5 * 1024 ** 3:
        raise HTTPException(status_code=413, detail="File exceeds 5 GB limit")

    agency_id = project.get("agency_id", "default")
    project_id = str(project["_id"])
    r2_key = f"deliverables/{agency_id}/{project_id}/{uuid.uuid4()}_{file_name}"

    upload_id = create_multipart_upload(r2_key, content_type)
    return {"upload_id": upload_id, "key": r2_key, "part_size": 100 * 1024 * 1024}


@router.post("/{token}/deliverables/{del_id}/files/{file_id}/version/complete")
async def complete_version_upload(
    token: str,
    del_id: str,
    file_id: str,
    background_tasks: BackgroundTasks,
    body: dict = Body(...),
    _: None = Depends(check_editor_write_limit),
):
    """Public: Finalize version upload. Snapshots old binary to previous_versions — NO R2 delete."""
    project, token_entry = await _get_project_by_editor_token(token)

    if del_id not in token_entry.get("deliverable_ids", []):
        raise HTTPException(status_code=403, detail="Deliverable not in scope for this editor link")

    deliverable = next((d for d in project.get("portal_deliverables", []) if d["id"] == del_id), None)
    if not deliverable:
        raise HTTPException(status_code=404, detail="Deliverable not found")

    file_entry = next((f for f in deliverable.get("files", []) if f["id"] == file_id), None)
    if not file_entry:
        raise HTTPException(status_code=404, detail="File not found")

    key = body.get("key")
    upload_id = body.get("upload_id")
    parts = body.get("parts", [])
    file_name = body.get("file_name", file_entry["file_name"])
    content_type = body.get("content_type", file_entry["content_type"])
    editor_email = body.get("editor_email", "")
    editor_name = body.get("editor_name", "Editor")
    change_notes = body.get("change_notes", "")

    if not key or not upload_id:
        raise HTTPException(status_code=400, detail="key and upload_id are required")

    r2_url = complete_multipart_upload(key, upload_id, parts)

    from models.project import FileVersion

    # Snapshot current file to FileVersion — preserve all binary references, NO R2 delete
    version_entry = FileVersion(
        version=file_entry.get("version", 1),
        file_name=file_entry["file_name"],
        content_type=file_entry["content_type"],
        uploaded_by=file_entry.get("uploaded_by"),
        uploaded_by_name=file_entry.get("uploaded_by_name"),
        uploaded_on=file_entry.get("uploaded_on", datetime.now(timezone.utc)),
        change_notes=change_notes,
        r2_key=file_entry.get("r2_key"),
        r2_url=file_entry.get("r2_url"),
        thumbnail_r2_key=file_entry.get("thumbnail_r2_key"),
        thumbnail_r2_url=file_entry.get("thumbnail_r2_url"),
        preview_r2_key=file_entry.get("preview_r2_key"),
        preview_r2_url=file_entry.get("preview_r2_url"),
    )

    is_image = content_type.startswith("image/")
    is_video = content_type.startswith("video/")
    new_version = file_entry.get("version", 1) + 1
    now = datetime.now(timezone.utc)
    project_id = str(project["_id"])

    update_fields = {
        "portal_deliverables.$[d].files.$[f].file_name": file_name,
        "portal_deliverables.$[d].files.$[f].content_type": content_type,
        "portal_deliverables.$[d].files.$[f].r2_key": key,
        "portal_deliverables.$[d].files.$[f].r2_url": r2_url,
        "portal_deliverables.$[d].files.$[f].uploaded_on": now,
        "portal_deliverables.$[d].files.$[f].uploaded_by": editor_email,
        "portal_deliverables.$[d].files.$[f].uploaded_by_name": editor_name,
        "portal_deliverables.$[d].files.$[f].version": new_version,
        "portal_deliverables.$[d].files.$[f].thumbnail_r2_key": None,
        "portal_deliverables.$[d].files.$[f].thumbnail_r2_url": None,
        "portal_deliverables.$[d].files.$[f].thumbnail_status": "pending" if (is_image or is_video) else "n/a",
        "portal_deliverables.$[d].files.$[f].watermark_r2_key": None,
        "portal_deliverables.$[d].files.$[f].watermark_r2_url": None,
        "portal_deliverables.$[d].files.$[f].watermark_status": "pending" if is_video else "n/a",
        "portal_deliverables.$[d].files.$[f].preview_r2_key": None,
        "portal_deliverables.$[d].files.$[f].preview_r2_url": None,
        "portal_deliverables.$[d].files.$[f].preview_status": "pending" if is_image else "n/a",
        "updated_on": now,
    }

    await projects_collection.update_one(
        {"_id": ObjectId(project_id)},
        {
            "$set": update_fields,
            "$push": {"portal_deliverables.$[d].files.$[f].previous_versions": version_entry.model_dump()},
        },
        array_filters=[{"d.id": del_id}, {"f.id": file_id}],
    )

    # Sync linked MediaItem (NO R2 delete — intentionally different from replace_file)
    media_item_id = file_entry.get("media_item_id")
    if media_item_id:
        try:
            await media_items_collection.update_one(
                {"id": media_item_id},
                {"$set": {
                    "name": file_name,
                    "r2_key": key,
                    "r2_url": r2_url,
                    "content_type": content_type,
                    "size_bytes": 0,
                    "thumbnail_r2_key": None, "thumbnail_r2_url": None,
                    "thumbnail_status": "pending" if (is_image or is_video) else "n/a",
                    "preview_r2_key": None, "preview_r2_url": None,
                    "preview_status": "pending" if is_image else "n/a",
                    "watermark_r2_key": None, "watermark_r2_url": None,
                    "watermark_status": "pending" if is_video else "n/a",
                    "updated_at": now,
                }},
            )
        except Exception:
            logger.error("Failed to sync MediaItem after version upload", exc_info=True)

    # Re-fetch project for agency_id and watermark settings
    project = await projects_collection.find_one(
        {"_id": ObjectId(project_id)},
        {"agency_id": 1, "code": 1, "metadata": 1, "portal_watermark_enabled": 1,
         "portal_watermark_text": 1, "portal_deliverables": 1},
    )
    agency_id = project.get("agency_id", "default") if project else "default"

    # Background processing on new file
    if is_image or is_video:
        from services.media_processing import process_thumbnail, process_watermark
        background_tasks.add_task(
            process_thumbnail, project_id, del_id, file_id, key, content_type, agency_id
        )
        if is_video and project and project.get("portal_watermark_enabled"):
            watermark_text = project.get("portal_watermark_text") or "Protected"
            background_tasks.add_task(
                process_watermark, project_id, del_id, file_id, key, watermark_text, agency_id
            )

    # Re-sync status (Changes Requested → Uploaded)
    await on_portal_file_added(_editor_db, project_id, del_id)

    # Notify agency (non-fatal)
    try:
        pd_entry = next(
            (d for d in (project.get("portal_deliverables", []) if project else []) if d["id"] == del_id),
            None,
        )
        task_id = pd_entry.get("task_id") if pd_entry else None
        deliverable_title = pd_entry.get("title", del_id) if pd_entry else del_id
        if task_id:
            task = await tasks_collection.find_one({"id": task_id})
            recipient_id = (task.get("assigned_to") or task.get("incharge_user_id")) if task else None
            if recipient_id:
                notification = NotificationModel(
                    user_id=recipient_id,
                    type="task_updated",
                    title="Editor Version Upload",
                    message=f"Editor {editor_name} uploaded v{new_version} of {file_name} to {deliverable_title}",
                    resource_type="task",
                    resource_id=task_id,
                    metadata={"project_id": project_id},
                )
                await notifications_collection.insert_one(notification.model_dump())
                user_doc = await users_collection.find_one({"id": recipient_id})
                if user_doc and user_doc.get("email"):
                    send_email(
                        to_email=user_doc["email"],
                        subject=f"Editor version upload: {file_name} v{new_version}",
                        html_content=(
                            f"<p>Editor <strong>{editor_name}</strong> ({editor_email}) uploaded "
                            f"<strong>v{new_version}</strong> of <strong>{file_name}</strong> "
                            f"to deliverable <strong>{deliverable_title}</strong>."
                            + (f" Notes: {change_notes}" if change_notes else "") + "</p>"
                        ),
                    )
    except Exception:
        logger.error("Failed to notify agency about editor version upload", exc_info=True)

    logger.info(f"Editor version {new_version} uploaded: {file_name} → deliverable {del_id}")
    return {
        "file": _parse_mongo({
            **file_entry,
            "file_name": file_name,
            "content_type": content_type,
            "r2_key": key,
            "r2_url": r2_url,
            "version": new_version,
            "uploaded_by": editor_email,
            "uploaded_by_name": editor_name,
            "uploaded_on": now,
            "thumbnail_r2_key": None, "thumbnail_r2_url": None,
            "thumbnail_status": "pending" if (is_image or is_video) else "n/a",
            "watermark_r2_key": None, "watermark_r2_url": None,
            "watermark_status": "pending" if is_video else "n/a",
            "preview_r2_key": None, "preview_r2_url": None,
            "preview_status": "pending" if is_image else "n/a",
        }),
        "new_version": new_version,
    }


@router.get("/{token}/deliverables/{del_id}/files/{file_id}/versions/{version_num}/download")
async def download_version(token: str, del_id: str, file_id: str, version_num: int, _: None = Depends(check_editor_read_limit)):
    """Public: Redirect to a presigned GET URL for a specific version's binary."""
    from fastapi.responses import RedirectResponse
    from utils.r2 import generate_presigned_get_url

    project, token_entry = await _get_project_by_editor_token(token)

    if del_id not in token_entry.get("deliverable_ids", []):
        raise HTTPException(status_code=403, detail="Deliverable not in scope for this editor link")

    deliverable = next((d for d in project.get("portal_deliverables", []) if d["id"] == del_id), None)
    if not deliverable:
        raise HTTPException(status_code=404, detail="Deliverable not found")

    file_entry = next((f for f in deliverable.get("files", []) if f["id"] == file_id), None)
    if not file_entry:
        raise HTTPException(status_code=404, detail="File not found")

    version = next(
        (v for v in file_entry.get("previous_versions", []) if v.get("version") == version_num),
        None,
    )
    if not version or not version.get("r2_key"):
        raise HTTPException(status_code=404, detail="Version not found or binary not preserved")

    presigned_url = generate_presigned_get_url(version["r2_key"], expires_in=300)
    return RedirectResponse(url=presigned_url, status_code=302)
