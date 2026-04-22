from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional
import secrets
import uuid

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query

from config import config
from database import db as raw_db
from logging_config import get_logger
from middleware.db_guard import ScopedDatabase
from models.media import MediaFolder, MediaItem
from models.user import UserModel
from routes.deps import get_db, require_media_access
from utils.r2 import delete_r2_object, generate_presigned_get_url, generate_presigned_put_url

logger = get_logger("media")

router = APIRouter(prefix="/api/media", tags=["media"])

ALLOWED_CONTENT_TYPES = {
    "image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif",
    "video/mp4", "video/webm", "video/quicktime", "video/x-msvideo",
    "application/pdf",
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _parse_mongo(data):
    if isinstance(data, list):
        return [_parse_mongo(item) for item in data]
    if isinstance(data, dict):
        return {k: (str(v) if isinstance(v, ObjectId) else _parse_mongo(v)) for k, v in data.items()}
    return data


def _build_path(parent_path: str, name: str) -> str:
    return f"{parent_path.rstrip('/')}/{name}/"


async def _build_folder_tree(folders: List[dict]) -> List[dict]:
    """Convert flat folder list into a nested tree."""
    folder_map = {f["id"]: {**f, "children": []} for f in folders}
    roots = []
    for f in folder_map.values():
        parent_id = f.get("parent_id")
        if parent_id and parent_id in folder_map:
            folder_map[parent_id]["children"].append(f)
        else:
            roots.append(f)
    return roots


async def _get_descendant_folder_ids(db: ScopedDatabase, folder_id: str) -> List[str]:
    """Return all descendant folder IDs, inclusive of the given folder."""
    ids = [folder_id]
    queue = [folder_id]
    while queue:
        current = queue.pop()
        children = await db.media_folders.find({"parent_id": current}).to_list(length=None)
        for child in children:
            ids.append(child["id"])
            queue.append(child["id"])
    return ids


def _delete_item_r2_keys(item: dict):
    """Best-effort delete all R2 objects for a media item."""
    for field in ("r2_key", "thumbnail_r2_key", "preview_r2_key", "watermark_r2_key"):
        key = item.get(field)
        if key:
            delete_r2_object(key)


async def _cascade_deliverable_unlink(project_id: str, deliverable_id: str, media_item_id: str):
    """Remove a DeliverableFile linked to this media item; revert status to Pending if no files remain."""
    try:
        project = await raw_db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            return
        for pd in project.get("portal_deliverables", []):
            if pd["id"] != deliverable_id:
                continue
            updated_files = [f for f in pd.get("files", []) if f.get("media_item_id") != media_item_id]
            new_status = pd["status"] if updated_files else "Pending"
            await raw_db.projects.update_one(
                {"_id": ObjectId(project_id), "portal_deliverables.id": deliverable_id},
                {"$set": {
                    "portal_deliverables.$.files": updated_files,
                    "portal_deliverables.$.status": new_status,
                }}
            )
            logger.info(
                "Deliverable unlinked from deleted media item",
                extra={"data": {"deliverable_id": deliverable_id, "media_item_id": media_item_id}}
            )
            return
    except Exception as e:
        logger.error(f"Cascade deliverable unlink failed: {e}")


# ─── Folder Endpoints ─────────────────────────────────────────────────────────

@router.get("/folders")
async def list_folder_tree(
    current_user: UserModel = Depends(require_media_access()),
    db: ScopedDatabase = Depends(get_db),
):
    """Return the full flat folder list for the agency. Tree building is done client-side."""
    folders = await db.media_folders.find({}).to_list(length=None)
    return _parse_mongo(folders)


@router.post("/folders", status_code=201)
async def create_folder(
    data: dict = Body(...),
    current_user: UserModel = Depends(require_media_access()),
    db: ScopedDatabase = Depends(get_db),
):
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Folder name is required")

    parent_id: Optional[str] = data.get("parent_id") or None
    parent_path = "/"

    if parent_id:
        parent = await db.media_folders.find_one({"id": parent_id})
        if not parent:
            raise HTTPException(status_code=404, detail="Parent folder not found")
        parent_path = parent["path"]

    existing = await db.media_folders.find_one({"parent_id": parent_id, "name": name})
    if existing:
        raise HTTPException(status_code=409, detail="A folder with this name already exists here")

    folder = MediaFolder(
        agency_id=current_user.agency_id,
        name=name,
        parent_id=parent_id,
        path=_build_path(parent_path, name),
        created_by=current_user.id,
    )
    await db.media_folders.insert_one(folder.model_dump())
    logger.info("Media folder created", extra={"data": {"id": folder.id, "path": folder.path}})
    return {"id": folder.id, "name": folder.name, "path": folder.path, "parent_id": folder.parent_id}


@router.patch("/folders/{folder_id}")
async def rename_folder(
    folder_id: str,
    data: dict = Body(...),
    current_user: UserModel = Depends(require_media_access()),
    db: ScopedDatabase = Depends(get_db),
):
    folder = await db.media_folders.find_one({"id": folder_id})
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    if folder.get("is_system"):
        raise HTTPException(status_code=403, detail="System folders cannot be renamed")

    new_name = data.get("name", "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Name is required")

    # Duplicate check at same level
    dupe = await db.media_folders.find_one({
        "parent_id": folder.get("parent_id"), "name": new_name, "id": {"$ne": folder_id}
    })
    if dupe:
        raise HTTPException(status_code=409, detail="A folder with this name already exists here")

    parent_path = "/"
    if folder.get("parent_id"):
        parent = await db.media_folders.find_one({"id": folder["parent_id"]})
        if parent:
            parent_path = parent["path"]

    old_path = folder["path"]
    new_path = _build_path(parent_path, new_name)
    now = datetime.now(timezone.utc)

    await db.media_folders.update_one(
        {"id": folder_id},
        {"$set": {"name": new_name, "path": new_path, "updated_at": now}}
    )

    # Rewrite paths of all descendants
    descendants = await db.media_folders.find({"path": {"$regex": f"^{old_path}"}}).to_list(length=None)
    for f in descendants:
        if f["id"] == folder_id:
            continue
        updated_path = new_path + f["path"][len(old_path):]
        await db.media_folders.update_one(
            {"id": f["id"]},
            {"$set": {"path": updated_path, "updated_at": now}}
        )

    return {"id": folder_id, "name": new_name, "path": new_path}


@router.delete("/folders/{folder_id}", status_code=204)
async def delete_folder(
    folder_id: str,
    cascade: bool = Query(False, description="Delete folder even if it contains files"),
    current_user: UserModel = Depends(require_media_access()),
    db: ScopedDatabase = Depends(get_db),
):
    folder = await db.media_folders.find_one({"id": folder_id})
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    if folder.get("is_system"):
        raise HTTPException(status_code=403, detail="System folders cannot be deleted")

    descendant_ids = await _get_descendant_folder_ids(db, folder_id)

    item_count = await db.media_items.count_documents({
        "folder_id": {"$in": descendant_ids}, "status": "active"
    })
    if item_count > 0 and not cascade:
        raise HTTPException(
            status_code=409,
            detail=f"Folder contains {item_count} file(s). Use ?cascade=true to delete with all contents."
        )

    if cascade and item_count > 0:
        # Only process active items — pending (in-progress uploads) are left to expire naturally
        items = await db.media_items.find({"folder_id": {"$in": descendant_ids}, "status": "active"}).to_list(length=None)
        for item in items:
            _delete_item_r2_keys(item)
        await db.media_items.delete_many({"folder_id": {"$in": descendant_ids}, "status": "active"})

    await db.media_folders.delete_many({"id": {"$in": descendant_ids}})


# ─── File Endpoints ───────────────────────────────────────────────────────────

@router.get("/folders/{folder_id}/items")
async def list_folder_items(
    folder_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    current_user: UserModel = Depends(require_media_access()),
    db: ScopedDatabase = Depends(get_db),
):
    folder = await db.media_folders.find_one({"id": folder_id})
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    skip = (page - 1) * limit
    items = await db.media_items.find(
        {"folder_id": folder_id, "status": "active"}
    ).skip(skip).limit(limit).to_list(length=limit)

    total = await db.media_items.count_documents({"folder_id": folder_id, "status": "active"})
    items = _parse_mongo(items)

    for item in items:
        if item.get("thumbnail_r2_key"):
            item["thumbnail_r2_url"] = generate_presigned_get_url(item["thumbnail_r2_key"], expires_in=3600)
        if item.get("preview_r2_key"):
            item["preview_url"] = generate_presigned_get_url(item["preview_r2_key"], expires_in=3600)

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": (total + limit - 1) // limit,
        "folder": _parse_mongo(folder),
        "data": items,
    }


@router.post("/upload-url", status_code=201)
async def get_upload_url(
    data: dict = Body(...),
    current_user: UserModel = Depends(require_media_access()),
    db: ScopedDatabase = Depends(get_db),
):
    """Step 1 of 2: pre-create a MediaItem and return a presigned PUT URL for direct browser upload."""
    file_name = data.get("file_name", "").strip()
    content_type = data.get("content_type", "").strip()
    folder_id = data.get("folder_id", "").strip()

    if not file_name or not content_type or not folder_id:
        raise HTTPException(status_code=400, detail="file_name, content_type, and folder_id are required")
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {content_type}")

    folder = await db.media_folders.find_one({"id": folder_id})
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    ext = Path(file_name).suffix.lower()
    item_id = str(uuid.uuid4())
    r2_key = f"media/{current_user.agency_id}/files/{item_id}{ext}"
    r2_url = f"{config.R2_PUBLIC_URL}/{r2_key}" if config.R2_PUBLIC_URL else r2_key

    is_image = content_type.startswith("image/")
    is_video = content_type.startswith("video/")

    item = MediaItem(
        id=item_id,
        agency_id=current_user.agency_id,
        folder_id=folder_id,
        name=file_name,
        r2_key=r2_key,
        r2_url=r2_url,
        content_type=content_type,
        source="direct",
        status="pending",
        uploaded_by=current_user.id,
        thumbnail_status="pending" if (is_image or is_video) else "n/a",
        preview_status="pending" if is_image else "n/a",
        watermark_status="n/a",
    )
    await db.media_items.insert_one(item.model_dump())

    upload_url = generate_presigned_put_url(r2_key, content_type)
    logger.info("Media upload URL generated", extra={"data": {"item_id": item_id, "folder_id": folder_id}})
    return {"upload_url": upload_url, "r2_key": r2_key, "media_item_id": item_id}


@router.post("/items")
async def register_file(
    data: dict = Body(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: UserModel = Depends(require_media_access()),
    db: ScopedDatabase = Depends(get_db),
):
    """Step 2 of 2: activate a pending MediaItem after the browser has PUT the file to R2."""
    media_item_id = data.get("media_item_id", "").strip()
    size_bytes = int(data.get("size_bytes", 0))

    if not media_item_id:
        raise HTTPException(status_code=400, detail="media_item_id is required")

    item = await db.media_items.find_one({"id": media_item_id, "status": "pending"})
    if not item:
        raise HTTPException(status_code=404, detail="Pending media item not found")

    now = datetime.now(timezone.utc)
    await db.media_items.update_one(
        {"id": media_item_id},
        {"$set": {"status": "active", "size_bytes": size_bytes, "updated_at": now}}
    )

    if item["content_type"].startswith("image/") or item["content_type"].startswith("video/"):
        from services.media_processing import process_media_item_thumbnail
        background_tasks.add_task(
            process_media_item_thumbnail,
            item_id=media_item_id,
            r2_key=item["r2_key"],
            content_type=item["content_type"],
            agency_id=current_user.agency_id,
        )

    logger.info("Media item activated", extra={"data": {"id": media_item_id, "size_bytes": size_bytes}})
    return {"id": media_item_id, "status": "active"}


@router.patch("/items/{item_id}")
async def update_item(
    item_id: str,
    data: dict = Body(...),
    current_user: UserModel = Depends(require_media_access()),
    db: ScopedDatabase = Depends(get_db),
):
    """Rename a file and/or move it to a different folder."""
    item = await db.media_items.find_one({"id": item_id, "status": "active"})
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")

    updates: dict = {}
    if "name" in data and data["name"].strip():
        updates["name"] = data["name"].strip()
    if "folder_id" in data:
        folder = await db.media_folders.find_one({"id": data["folder_id"]})
        if not folder:
            raise HTTPException(status_code=404, detail="Target folder not found")
        updates["folder_id"] = data["folder_id"]

    if not updates:
        raise HTTPException(status_code=400, detail="Nothing to update")

    updates["updated_at"] = datetime.now(timezone.utc)
    await db.media_items.update_one({"id": item_id}, {"$set": updates})
    return {"id": item_id, **{k: v for k, v in updates.items() if k != "updated_at"}}


@router.delete("/items/{item_id}", status_code=204)
async def delete_item(
    item_id: str,
    current_user: UserModel = Depends(require_media_access()),
    db: ScopedDatabase = Depends(get_db),
):
    item = await db.media_items.find_one({"id": item_id, "status": "active"})
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")

    _delete_item_r2_keys(item)

    if item.get("source") == "deliverable" and item.get("source_project_id") and item.get("source_deliverable_id"):
        await _cascade_deliverable_unlink(
            project_id=item["source_project_id"],
            deliverable_id=item["source_deliverable_id"],
            media_item_id=item_id,
        )

    await db.media_items.delete_one({"id": item_id})
    logger.info("Media item deleted", extra={"data": {"id": item_id}})


@router.get("/items/{item_id}/download")
async def get_download_url(
    item_id: str,
    expires_in: int = Query(300, ge=60, le=86400),
    current_user: UserModel = Depends(require_media_access()),
    db: ScopedDatabase = Depends(get_db),
):
    item = await db.media_items.find_one({"id": item_id, "status": "active"})
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")

    url = generate_presigned_get_url(item["r2_key"], expires_in=expires_in)
    return {"url": url, "file_name": item["name"], "content_type": item["content_type"]}


@router.get("/search")
async def search_items(
    q: str = Query(..., min_length=1),
    limit: int = Query(30, ge=1, le=100),
    current_user: UserModel = Depends(require_media_access()),
    db: ScopedDatabase = Depends(get_db),
):
    items = await db.media_items.find({
        "name": {"$regex": q, "$options": "i"},
        "status": "active",
    }).limit(limit).to_list(length=limit)

    items = _parse_mongo(items)
    for item in items:
        if item.get("thumbnail_r2_key"):
            item["thumbnail_r2_url"] = generate_presigned_get_url(item["thumbnail_r2_key"], expires_in=3600)

    return {"data": items, "count": len(items)}


# ─── Sharing Endpoints ────────────────────────────────────────────────────────

@router.post("/items/{item_id}/share")
async def create_share_link(
    item_id: str,
    data: dict = Body(default={}),
    current_user: UserModel = Depends(require_media_access()),
    db: ScopedDatabase = Depends(get_db),
):
    """Generate (or refresh) a public share token for a media item."""
    item = await db.media_items.find_one({"id": item_id, "status": "active"})
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")

    expires_in_days: Optional[int] = data.get("expires_in_days")  # None = never expires
    expires_at = None
    if expires_in_days is not None:
        if not isinstance(expires_in_days, int) or expires_in_days < 1:
            raise HTTPException(status_code=400, detail="expires_in_days must be a positive integer")
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)

    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)

    await db.media_items.update_one(
        {"id": item_id},
        {"$set": {"share_token": token, "share_expires_at": expires_at, "updated_at": now}}
    )

    share_url = f"{config.FRONTEND_URL[0]}/share/{token}"
    logger.info("Share link created", extra={"data": {"item_id": item_id}})
    return {"share_url": share_url, "token": token, "expires_at": expires_at}


@router.delete("/items/{item_id}/share", status_code=204)
async def revoke_share_link(
    item_id: str,
    current_user: UserModel = Depends(require_media_access()),
    db: ScopedDatabase = Depends(get_db),
):
    """Revoke the public share token for a media item."""
    item = await db.media_items.find_one({"id": item_id, "status": "active"})
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")

    await db.media_items.update_one(
        {"id": item_id},
        {"$set": {"share_token": None, "share_expires_at": None, "updated_at": datetime.now(timezone.utc)}}
    )
    logger.info("Share link revoked", extra={"data": {"item_id": item_id}})


@router.get("/share/{token}")
async def resolve_share_link(token: str):
    """Public endpoint — no auth required. Resolves a share token to a presigned download URL."""
    item = await raw_db.media_items.find_one({"share_token": token, "status": "active"})
    if not item:
        raise HTTPException(status_code=404, detail="Share link not found or expired")

    expires_at = item.get("share_expires_at")
    if expires_at:
        # MongoDB may return a timezone-naive UTC datetime; normalise before comparing
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires_at:
            raise HTTPException(status_code=410, detail="Share link has expired")

    url = generate_presigned_get_url(item["r2_key"], expires_in=3600)
    return {
        "url": url,
        "file_name": item["name"],
        "content_type": item["content_type"],
        "expires_in": 3600,
    }


# ─── R2 Usage Endpoints ───────────────────────────────────────────────────────

@router.get("/usage")
async def get_usage_stats(
    current_user: UserModel = Depends(require_media_access()),
    db: ScopedDatabase = Depends(get_db),
):
    """Return cached R2 storage stats. Flags as stale if older than 24 h."""
    from services.r2_usage import get_cached_stats
    stats = await get_cached_stats(current_user.agency_id, db)
    return stats


@router.post("/usage/refresh")
async def refresh_usage_stats(
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: UserModel = Depends(require_media_access()),
    db: ScopedDatabase = Depends(get_db),
):
    """Trigger a background recalculation of R2 usage stats."""
    from services.r2_usage import calculate_bucket_stats
    background_tasks.add_task(calculate_bucket_stats, current_user.agency_id, db)
    logger.info("R2 usage refresh queued", extra={"data": {"agency_id": current_user.agency_id}})
    return {"status": "refreshing"}
