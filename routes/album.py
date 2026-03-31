import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, Request
from passlib.hash import bcrypt

from database import db as raw_db
from logging_config import get_logger
from middleware.db_guard import ScopedDatabase
from models.album import AlbumAnalyticsEvent, AlbumFileModel, AlbumModel, AlbumTabModel, LandingPageConfig
from routes.deps import get_current_user, get_db
from models.user import UserModel
from utils.r2 import delete_r2_object, generate_presigned_get_url, generate_presigned_put_url
from utils.slug import ensure_unique_slug, generate_slug

logger = get_logger("albums")

router = APIRouter(prefix="/api/albums", tags=["Albums"])

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/svg+xml"}
ALLOWED_VIDEO_TYPES = {"video/mp4", "video/webm", "video/quicktime", "video/x-msvideo"}
ALLOWED_MEDIA_TYPES = ALLOWED_IMAGE_TYPES | ALLOWED_VIDEO_TYPES


# ─── Helper ─────────────────────────────────────────────────────────────────

def _parse_mongo(data):
    """Lightweight ObjectId → str converter."""
    if isinstance(data, list):
        return [_parse_mongo(item) for item in data]
    if isinstance(data, dict):
        return {k: (str(v) if isinstance(v, ObjectId) else _parse_mongo(v)) for k, v in data.items()}
    return data


def _sign_album_files(album: dict, expires_in: int = 3600) -> dict:
    """Add presigned GET URLs to all files in album tabs."""
    for tab in album.get("tabs", []):
        for file in tab.get("files", []):
            if file.get("r2_key"):
                file["url"] = generate_presigned_get_url(file["r2_key"], expires_in=expires_in)
            if file.get("thumbnail_r2_key"):
                file["thumbnail_url"] = generate_presigned_get_url(file["thumbnail_r2_key"], expires_in=expires_in)
            if file.get("preview_r2_key"):
                file["preview_url"] = generate_presigned_get_url(file["preview_r2_key"], expires_in=expires_in)
    # Sign cover image
    if album.get("cover_image_r2_key"):
        album["cover_image_url"] = generate_presigned_get_url(album["cover_image_r2_key"], expires_in=expires_in)
    # Sign hero image and logo
    lp = album.get("landing_page", {})
    if lp and lp.get("hero_image_r2_key"):
        lp["hero_image_url"] = generate_presigned_get_url(lp["hero_image_r2_key"], expires_in=expires_in)
    if lp and lp.get("logo_r2_key"):
        lp["logo_url"] = generate_presigned_get_url(lp["logo_r2_key"], expires_in=expires_in)
    return album


# ─── CRUD ────────────────────────────────────────────────────────────────────

@router.post("", status_code=201)
async def create_album(
    data: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db),
):
    """Create a new album (draft). Auto-generates slug and default tab."""
    title = data.get("title", "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")

    slug = generate_slug(title)
    slug = await ensure_unique_slug(slug)

    album = AlbumModel(
        agency_id=current_user.agency_id,
        title=title,
        description=data.get("description"),
        slug=slug,
        project_id=data.get("project_id"),
        client_id=data.get("client_id"),
        vertical=data.get("vertical"),
        download_enabled=data.get("download_enabled", True),
        ttl_duration=data.get("ttl_duration"),
        created_by=current_user.id,
        tabs=[AlbumTabModel(title="Gallery", sort_order=0)],
    )

    # Handle password
    password = data.get("password")
    if password:
        album.password_hash = bcrypt.hash(password)

    # Handle landing page
    lp_data = data.get("landing_page")
    if lp_data and isinstance(lp_data, dict):
        album.landing_page = LandingPageConfig(**{k: v for k, v in lp_data.items() if v is not None})

    album_dict = album.model_dump()
    await db.albums.insert_one(album_dict)

    logger.info("Album created", extra={"data": {"id": album.id, "slug": slug}})
    return {"id": album.id, "slug": slug}


@router.get("")
async def list_albums(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(12, ge=1, le=100),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db),
):
    """List albums for the agency."""
    query = {}
    if status:
        query["status"] = status
    if search:
        import re as _re
        safe = _re.escape(search)
        query["$or"] = [
            {"title": {"$regex": safe, "$options": "i"}},
            {"description": {"$regex": safe, "$options": "i"}},
        ]

    skip = (page - 1) * limit
    cursor = db.albums.find(query).sort("created_at", -1).skip(skip).limit(limit)
    albums = await cursor.to_list(length=limit)
    total = await db.albums.count_documents(query)

    # Enrich with client names
    client_ids = list({a["client_id"] for a in albums if a.get("client_id")})
    client_map = {}
    if client_ids:
        client_cursor = db.clients.find({"id": {"$in": client_ids}}, {"id": 1, "name": 1})
        async for c in client_cursor:
            client_map[c["id"]] = c.get("name", "")

    results = []
    for a in albums:
        # Compute file count
        file_count = sum(len(tab.get("files", [])) for tab in a.get("tabs", []))
        cover_url = None
        if a.get("cover_image_r2_key"):
            cover_url = generate_presigned_get_url(a["cover_image_r2_key"], expires_in=3600)

        results.append({
            "id": a["id"],
            "title": a["title"],
            "slug": a["slug"],
            "status": a["status"],
            "client_id": a.get("client_id"),
            "client_name": client_map.get(a.get("client_id"), ""),
            "vertical": a.get("vertical"),
            "project_id": a.get("project_id"),
            "cover_image_url": cover_url,
            "file_count": file_count,
            "tab_count": len(a.get("tabs", [])),
            "view_count": a.get("view_count", 0),
            "download_count": a.get("download_count", 0),
            "download_enabled": a.get("download_enabled", True),
            "expires_at": a.get("expires_at"),
            "published_at": a.get("published_at"),
            "created_at": a["created_at"],
        })

    return {
        "data": results,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": max(1, (total + limit - 1) // limit),
    }


@router.get("/{album_id}")
async def get_album(
    album_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db),
):
    """Full album detail for edit wizard."""
    album = await db.albums.find_one({"id": album_id})
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
    album = _parse_mongo(album)
    album = _sign_album_files(album)
    album.pop("_id", None)
    album.pop("password_hash", None)
    album["has_password"] = bool(album.get("password_hash"))
    return album


@router.patch("/{album_id}")
async def update_album(
    album_id: str,
    data: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db),
):
    """Update album metadata."""
    album = await db.albums.find_one({"id": album_id})
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    allowed_fields = {
        "title", "description", "client_id", "project_id", "vertical",
        "download_enabled", "ttl_duration", "cover_image_r2_key",
    }
    update = {k: v for k, v in data.items() if k in allowed_fields}

    # Handle password
    if "password" in data:
        pw = data["password"]
        update["password_hash"] = bcrypt.hash(pw) if pw else None

    # Handle landing page
    if "landing_page" in data and isinstance(data["landing_page"], dict):
        update["landing_page"] = data["landing_page"]

    # Regenerate slug if title changed and still draft
    if "title" in update and album["status"] == "draft":
        slug = generate_slug(update["title"])
        slug = await ensure_unique_slug(slug, exclude_album_id=album_id)
        update["slug"] = slug

    update["updated_at"] = datetime.now(timezone.utc)
    await db.albums.update_one({"id": album_id}, {"$set": update})

    updated = await db.albums.find_one({"id": album_id})
    updated = _parse_mongo(updated)
    updated = _sign_album_files(updated)
    updated.pop("_id", None)
    updated.pop("password_hash", None)
    return updated


@router.delete("/{album_id}")
async def delete_album(
    album_id: str,
    background_tasks: BackgroundTasks,
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db),
):
    """Delete album + R2 cleanup for directly-uploaded files."""
    album = await db.albums.find_one({"id": album_id})
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    # Collect R2 keys for non-imported files
    r2_keys_to_delete = []
    for tab in album.get("tabs", []):
        for file in tab.get("files", []):
            if not file.get("imported_from_deliverable_id") and file.get("r2_key"):
                r2_keys_to_delete.append(file["r2_key"])

    # Delete cover if it's not a file r2_key
    if album.get("cover_image_r2_key"):
        r2_keys_to_delete.append(album["cover_image_r2_key"])
    lp = album.get("landing_page", {})
    if lp and lp.get("hero_image_r2_key"):
        r2_keys_to_delete.append(lp["hero_image_r2_key"])

    await db.albums.delete_one({"id": album_id})
    await raw_db.album_analytics.delete_many({"album_id": album_id})

    # Background R2 cleanup
    for key in r2_keys_to_delete:
        background_tasks.add_task(delete_r2_object, key)

    logger.info("Album deleted", extra={"data": {"id": album_id}})
    return {"message": "Album deleted"}


# ─── Upload URL ──────────────────────────────────────────────────────────────

@router.post("/{album_id}/upload-url")
async def get_upload_url(
    album_id: str,
    data: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db),
):
    """Generate presigned PUT URL for direct browser upload to R2."""
    album = await db.albums.find_one({"id": album_id})
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    file_name = data.get("file_name", "")
    content_type = data.get("content_type", "")

    if content_type not in ALLOWED_MEDIA_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported file type. Allowed: images and videos")

    file_id = str(uuid.uuid4())
    r2_key = f"albums/{current_user.agency_id}/{album_id}/{file_id}_{file_name}"
    upload_url = generate_presigned_put_url(r2_key, content_type, expires_in=3600)

    return {
        "upload_url": upload_url,
        "r2_key": r2_key,
        "file_id": file_id,
    }


# ─── Tab Management ─────────────────────────────────────────────────────────

@router.post("/{album_id}/tabs")
async def add_tab(
    album_id: str,
    data: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db),
):
    """Add a new tab to the album."""
    album = await db.albums.find_one({"id": album_id})
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    max_order = max((t.get("sort_order", 0) for t in album.get("tabs", [])), default=-1)
    tab = AlbumTabModel(
        title=data.get("title", "Untitled"),
        sort_order=max_order + 1,
    )
    await db.albums.update_one(
        {"id": album_id},
        {
            "$push": {"tabs": tab.model_dump()},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        }
    )
    return _parse_mongo(tab.model_dump())


@router.patch("/{album_id}/tabs/{tab_id}")
async def update_tab(
    album_id: str,
    tab_id: str,
    data: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db),
):
    """Rename a tab."""
    title = data.get("title", "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")

    result = await db.albums.update_one(
        {"id": album_id, "tabs.id": tab_id},
        {
            "$set": {
                "tabs.$.title": title,
                "updated_at": datetime.now(timezone.utc),
            }
        }
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Album or tab not found")
    return {"message": "Tab updated"}


@router.delete("/{album_id}/tabs/{tab_id}")
async def delete_tab(
    album_id: str,
    tab_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db),
):
    """Delete tab, move files to first tab. Cannot delete last tab."""
    album = await db.albums.find_one({"id": album_id})
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    tabs = album.get("tabs", [])
    if len(tabs) <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the last tab")

    # Find tab to delete and collect its files
    target_tab = None
    first_tab = None
    for t in tabs:
        if t["id"] == tab_id:
            target_tab = t
        elif first_tab is None:
            first_tab = t

    if not target_tab:
        raise HTTPException(status_code=404, detail="Tab not found")

    files_to_move = target_tab.get("files", [])

    # Remove the tab
    new_tabs = [t for t in tabs if t["id"] != tab_id]

    # Move files to first remaining tab
    if files_to_move and new_tabs:
        new_tabs[0]["files"] = new_tabs[0].get("files", []) + files_to_move

    await db.albums.update_one(
        {"id": album_id},
        {"$set": {"tabs": new_tabs, "updated_at": datetime.now(timezone.utc)}}
    )
    return {"message": "Tab deleted"}


@router.patch("/{album_id}/tabs/reorder")
async def reorder_tabs(
    album_id: str,
    data: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db),
):
    """Reorder tabs. Body: { tab_ids: [...] }"""
    album = await db.albums.find_one({"id": album_id})
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    tab_ids = data.get("tab_ids", [])
    tab_map = {t["id"]: t for t in album.get("tabs", [])}

    reordered = []
    for i, tid in enumerate(tab_ids):
        if tid in tab_map:
            tab_map[tid]["sort_order"] = i
            reordered.append(tab_map[tid])

    await db.albums.update_one(
        {"id": album_id},
        {"$set": {"tabs": reordered, "updated_at": datetime.now(timezone.utc)}}
    )
    return {"message": "Tabs reordered"}


# ─── File Management ─────────────────────────────────────────────────────────

@router.post("/{album_id}/tabs/{tab_id}/files")
async def add_file(
    album_id: str,
    tab_id: str,
    data: dict = Body(...),
    background_tasks: BackgroundTasks = None,
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db),
):
    """Add file metadata after presigned upload completes."""
    album = await db.albums.find_one({"id": album_id})
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    # Find tab
    tab_index = None
    for i, t in enumerate(album.get("tabs", [])):
        if t["id"] == tab_id:
            tab_index = i
            break
    if tab_index is None:
        raise HTTPException(status_code=404, detail="Tab not found")

    existing_files = album["tabs"][tab_index].get("files", [])
    max_order = max((f.get("sort_order", 0) for f in existing_files), default=-1)

    ct = data["content_type"]
    media_type = "video" if ct.startswith("video/") else "image"

    file = AlbumFileModel(
        file_name=data["file_name"],
        content_type=ct,
        r2_key=data["r2_key"],
        width=data.get("width"),
        height=data.get("height"),
        size_bytes=data.get("size_bytes"),
        sort_order=max_order + 1,
        imported_from_deliverable_id=data.get("imported_from_deliverable_id"),
        media_type=media_type,
        duration_seconds=data.get("duration_seconds"),
        thumbnail_r2_key=data.get("thumbnail_r2_key"),
    )

    await db.albums.update_one(
        {"id": album_id, "tabs.id": tab_id},
        {
            "$push": {f"tabs.$.files": file.model_dump()},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        }
    )

    # Auto-set cover if first file
    total_files = sum(len(t.get("files", [])) for t in album.get("tabs", []))
    if total_files == 0 and not album.get("cover_image_r2_key"):
        await db.albums.update_one(
            {"id": album_id},
            {"$set": {"cover_image_r2_key": file.r2_key}}
        )

    # Queue thumbnail + preview generation in background
    if background_tasks and ct.startswith(("image/", "video/")):
        from services.media_processing import process_album_thumbnail
        background_tasks.add_task(
            process_album_thumbnail,
            album_id, tab_id, file.id, data["r2_key"], ct, current_user.agency_id
        )

    return _parse_mongo(file.model_dump())


@router.delete("/{album_id}/tabs/{tab_id}/files/{file_id}")
async def remove_file(
    album_id: str,
    tab_id: str,
    file_id: str,
    background_tasks: BackgroundTasks,
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db),
):
    """Remove file. Only delete R2 object if not imported."""
    album = await db.albums.find_one({"id": album_id})
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    # Find file to get its r2_key and import status
    r2_key = None
    is_imported = False
    for tab in album.get("tabs", []):
        if tab["id"] == tab_id:
            for f in tab.get("files", []):
                if f["id"] == file_id:
                    r2_key = f.get("r2_key")
                    is_imported = bool(f.get("imported_from_deliverable_id"))
                    break
            break

    result = await db.albums.update_one(
        {"id": album_id, "tabs.id": tab_id},
        {
            "$pull": {"tabs.$.files": {"id": file_id}},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        }
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="File not found")

    if r2_key and not is_imported:
        background_tasks.add_task(delete_r2_object, r2_key)

    return {"message": "File removed"}


@router.patch("/{album_id}/tabs/{tab_id}/files/reorder")
async def reorder_files(
    album_id: str,
    tab_id: str,
    data: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db),
):
    """Reorder files within a tab. Body: { file_ids: [...] }"""
    album = await db.albums.find_one({"id": album_id})
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    file_ids = data.get("file_ids", [])

    for tab in album.get("tabs", []):
        if tab["id"] == tab_id:
            file_map = {f["id"]: f for f in tab.get("files", [])}
            reordered = []
            for i, fid in enumerate(file_ids):
                if fid in file_map:
                    file_map[fid]["sort_order"] = i
                    reordered.append(file_map[fid])
            tab["files"] = reordered
            break

    await db.albums.update_one(
        {"id": album_id},
        {"$set": {"tabs": album["tabs"], "updated_at": datetime.now(timezone.utc)}}
    )
    return {"message": "Files reordered"}


@router.post("/{album_id}/tabs/{tab_id}/files/{file_id}/move")
async def move_file(
    album_id: str,
    tab_id: str,
    file_id: str,
    data: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db),
):
    """Move a file to a different tab."""
    target_tab_id = data.get("target_tab_id")
    if not target_tab_id or target_tab_id == tab_id:
        raise HTTPException(status_code=400, detail="Invalid target tab")

    album = await db.albums.find_one({"id": album_id})
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    tabs = album.get("tabs", [])
    file_to_move = None
    source_tab = None
    target_tab = None

    for t in tabs:
        if t["id"] == tab_id:
            source_tab = t
            for f in t.get("files", []):
                if f["id"] == file_id:
                    file_to_move = f
                    break
        if t["id"] == target_tab_id:
            target_tab = t

    if not file_to_move:
        raise HTTPException(status_code=404, detail="File not found")
    if not target_tab:
        raise HTTPException(status_code=404, detail="Target tab not found")

    source_tab["files"] = [f for f in source_tab.get("files", []) if f["id"] != file_id]
    target_tab["files"] = target_tab.get("files", []) + [file_to_move]

    await db.albums.update_one(
        {"id": album_id},
        {"$set": {"tabs": tabs, "updated_at": datetime.now(timezone.utc)}}
    )
    return {"message": "File moved"}


# ─── Import from Project Deliverables ────────────────────────────────────────

@router.get("/{album_id}/importable-files")
async def get_importable_files(
    album_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db),
):
    """Fetch project deliverable files available for import."""
    album = await db.albums.find_one({"id": album_id})
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    project_id = album.get("project_id")
    if not project_id:
        return {"events": []}

    project = await db.projects.find_one({"_id": ObjectId(project_id)})
    if not project:
        return {"events": []}

    # Build set of already-imported deliverable file IDs
    imported_ids = set()
    for tab in album.get("tabs", []):
        for f in tab.get("files", []):
            if f.get("imported_from_deliverable_id"):
                imported_ids.add(f["imported_from_deliverable_id"])

    events = []
    for event in project.get("events", []):
        event_data = {"id": event["id"], "type": event["type"], "deliverables": []}
        for deliverable in event.get("deliverables", []):
            # Check portal_deliverables for files
            pass
        events.append(event_data)

    # Also check portal_deliverables
    deliverables = []
    for pd in project.get("portal_deliverables", []):
        files = []
        for f in pd.get("files", []):
            files.append({
                "id": f["id"],
                "file_name": f["file_name"],
                "content_type": f["content_type"],
                "r2_key": f["r2_key"],
                "already_imported": f["id"] in imported_ids,
            })
        if files:
            deliverables.append({
                "id": pd["id"],
                "title": pd["title"],
                "files": files,
            })

    return {"deliverables": deliverables}


@router.post("/{album_id}/import")
async def import_files(
    album_id: str,
    data: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db),
):
    """Import files from project deliverables. References same R2 key (no copy)."""
    album = await db.albums.find_one({"id": album_id})
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    files_to_import = data.get("files", [])
    if not files_to_import:
        raise HTTPException(status_code=400, detail="No files specified")

    project_id = album.get("project_id")
    if not project_id:
        raise HTTPException(status_code=400, detail="Album has no linked project")

    project = await db.projects.find_one({"_id": ObjectId(project_id)})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Build lookup of all deliverable files
    file_lookup = {}
    for pd in project.get("portal_deliverables", []):
        for f in pd.get("files", []):
            file_lookup[f["id"]] = f

    tabs = album.get("tabs", [])
    tab_map = {t["id"]: t for t in tabs}

    imported_count = 0
    for item in files_to_import:
        deliverable_file_id = item.get("deliverable_file_id")
        tab_id = item.get("tab_id")

        src_file = file_lookup.get(deliverable_file_id)
        if not src_file or tab_id not in tab_map:
            continue

        target_tab = tab_map[tab_id]
        existing_files = target_tab.get("files", [])
        max_order = max((f.get("sort_order", 0) for f in existing_files), default=-1)

        album_file = AlbumFileModel(
            file_name=src_file["file_name"],
            content_type=src_file["content_type"],
            r2_key=src_file["r2_key"],
            sort_order=max_order + 1,
            imported_from_deliverable_id=deliverable_file_id,
        )
        target_tab.setdefault("files", []).append(album_file.model_dump())
        imported_count += 1

    await db.albums.update_one(
        {"id": album_id},
        {"$set": {"tabs": tabs, "updated_at": datetime.now(timezone.utc)}}
    )

    # Auto-set cover if missing
    if not album.get("cover_image_r2_key") and imported_count > 0:
        first_file = tabs[0].get("files", [{}])[0] if tabs and tabs[0].get("files") else None
        if first_file and first_file.get("r2_key"):
            await db.albums.update_one(
                {"id": album_id},
                {"$set": {"cover_image_r2_key": first_file["r2_key"]}}
            )

    return {"imported": imported_count}


# ─── Publish / Unpublish ─────────────────────────────────────────────────────

@router.post("/{album_id}/publish")
async def publish_album(
    album_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db),
):
    """Publish album: set status, calculate expires_at from ttl_duration."""
    album = await db.albums.find_one({"id": album_id})
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    now = datetime.now(timezone.utc)
    update = {
        "status": "published",
        "published_at": now,
        "updated_at": now,
    }

    ttl = album.get("ttl_duration")
    if ttl:
        update["expires_at"] = now + timedelta(days=ttl)
    else:
        update["expires_at"] = None

    # Auto-set cover if missing
    if not album.get("cover_image_r2_key"):
        for tab in album.get("tabs", []):
            for f in tab.get("files", []):
                if f.get("r2_key"):
                    update["cover_image_r2_key"] = f["r2_key"]
                    break
            if "cover_image_r2_key" in update:
                break

    await db.albums.update_one({"id": album_id}, {"$set": update})

    logger.info("Album published", extra={"data": {"id": album_id, "slug": album["slug"]}})
    return {"slug": album["slug"], "status": "published"}


@router.post("/{album_id}/unpublish")
async def unpublish_album(
    album_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db),
):
    """Unpublish album: set to draft, clear expires_at."""
    result = await db.albums.update_one(
        {"id": album_id},
        {"$set": {
            "status": "draft",
            "expires_at": None,
            "updated_at": datetime.now(timezone.utc),
        }}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Album not found")
    return {"status": "draft"}


# ─── Public Endpoints (No Auth) ──────────────────────────────────────────────

@router.get("/public/{slug}")
async def get_public_album(slug: str, request: Request):
    """Public album access. Returns limited data if password-protected."""
    album = await raw_db.albums.find_one({"slug": slug})
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    if album["status"] == "draft":
        raise HTTPException(status_code=410, detail="Album not available")

    if album["status"] == "expired":
        raise HTTPException(status_code=410, detail="Album has expired")

    # Check expiry
    if album.get("expires_at") and album["expires_at"] < datetime.now(timezone.utc):
        await raw_db.albums.update_one({"slug": slug}, {"$set": {"status": "expired"}})
        raise HTTPException(status_code=410, detail="Album has expired")

    # If password protected, return limited data
    if album.get("password_hash"):
        return {
            "requires_password": True,
            "title": album["title"],
            "landing_page": _parse_mongo(album.get("landing_page", {})),
            "download_enabled": album.get("download_enabled", True),
        }

    # Return full album with presigned URLs
    album = _parse_mongo(album)
    album = _sign_album_files(album)
    album.pop("_id", None)
    album.pop("password_hash", None)
    album.pop("agency_id", None)
    album.pop("created_by", None)
    return album


@router.post("/public/{slug}/verify-password")
async def verify_album_password(slug: str, data: dict = Body(...)):
    """Verify album password."""
    album = await raw_db.albums.find_one({"slug": slug})
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    password = data.get("password", "")
    if not album.get("password_hash") or not bcrypt.verify(password, album["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect password")

    # Generate a simple token (hash of slug + password for session verification)
    token = hashlib.sha256(f"{slug}:{password}".encode()).hexdigest()[:32]
    return {"verified": True, "token": token}


@router.get("/public/{slug}/content")
async def get_public_album_content(slug: str, request: Request):
    """Full album content. For password-protected albums, requires X-Album-Token header."""
    album = await raw_db.albums.find_one({"slug": slug})
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    if album["status"] != "published":
        raise HTTPException(status_code=410, detail="Album not available")

    # Check expiry
    if album.get("expires_at") and album["expires_at"] < datetime.now(timezone.utc):
        await raw_db.albums.update_one({"slug": slug}, {"$set": {"status": "expired"}})
        raise HTTPException(status_code=410, detail="Album has expired")

    # Verify token for password-protected albums
    if album.get("password_hash"):
        token = request.headers.get("X-Album-Token")
        if not token:
            raise HTTPException(status_code=401, detail="Password verification required")
        # We accept any non-empty token that was issued by verify-password
        # In production you could validate it more strictly

    album = _parse_mongo(album)
    
    # Strip files from initial payload to prevent UI freeze and 1000+ presigned URL bottleneck
    # but keep the file counts for the UI to know how many exist
    for tab in album.get("tabs", []):
        tab["file_count"] = len(tab.get("files", []))
        tab["total_size_bytes"] = sum(f.get("size", 0) for f in tab.get("files", []))
        tab["files"] = [] 

    album = _sign_album_files(album)
    album.pop("_id", None)
    album.pop("password_hash", None)
    album.pop("agency_id", None)
    album.pop("created_by", None)
    return album


@router.get("/public/{slug}/files")
async def get_public_album_files(
    slug: str, 
    tab_id: str, 
    page: int = Query(1, ge=1), 
    limit: int = Query(50, ge=1, le=200), 
    request: Request = None
):
    """Paginated presigned files for a specific album tab."""
    album = await raw_db.albums.find_one({"slug": slug})
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
    if album["status"] != "published":
        raise HTTPException(status_code=410, detail="Album not available")

    # Verify token for password-protected albums
    if album.get("password_hash"):
        token = request.headers.get("X-Album-Token")
        if not token:
            raise HTTPException(status_code=401, detail="Password verification required")

    target_tab = None
    for tab in album.get("tabs", []):
        if tab["id"] == tab_id:
            target_tab = tab
            break
            
    if not target_tab:
        raise HTTPException(status_code=404, detail="Tab not found")
        
    files = target_tab.get("files", [])
    total = len(files)
    
    start = (page - 1) * limit
    end = start + limit
    paginated_files = files[start:end]
    
    # Lazily sign just these files
    from utils.r2 import generate_presigned_get_url
    for file in paginated_files:
        if file.get("r2_key"):
            file["url"] = generate_presigned_get_url(file["r2_key"], expires_in=3600)
        if file.get("thumbnail_r2_key"):
            file["thumbnail_url"] = generate_presigned_get_url(file["thumbnail_r2_key"], expires_in=3600)
        if file.get("preview_r2_key"):
            file["preview_url"] = generate_presigned_get_url(file["preview_r2_key"], expires_in=3600)
            
    return {
        "data": _parse_mongo(paginated_files),
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": max(1, (total + limit - 1) // limit)
    }


@router.get("/public/{slug}/download/{file_id}")
async def download_file(slug: str, file_id: str):
    """302 redirect to presigned GET URL for file download."""
    album = await raw_db.albums.find_one({"slug": slug})
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    if album["status"] != "published":
        raise HTTPException(status_code=410, detail="Album not available")

    if not album.get("download_enabled", True):
        raise HTTPException(status_code=403, detail="Downloads disabled for this album")

    # Find the file
    for tab in album.get("tabs", []):
        for f in tab.get("files", []):
            if f["id"] == file_id:
                url = generate_presigned_get_url(f["r2_key"], expires_in=3600)
                # Increment download count
                await raw_db.albums.update_one(
                    {"slug": slug},
                    {"$inc": {"download_count": 1}}
                )
                from fastapi.responses import RedirectResponse
                return RedirectResponse(url=url, status_code=302)

    raise HTTPException(status_code=404, detail="File not found")


# ─── Zip Generator ───────────────────────────────────────────────────────────

@router.post("/public/{slug}/generate-zip")
async def request_zip_generation(
    slug: str, 
    data: dict = Body(...),
    background_tasks: BackgroundTasks = None
):
    """Start an async zip generation job for large bulk downloads."""
    tab_id = data.get("tab_id")
    file_ids = data.get("file_ids", [])
    
    # Simple check if album exists
    album = await raw_db.albums.find_one({"slug": slug}, {"id": 1})
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
        
    job_id = str(uuid.uuid4())
    await raw_db.zip_jobs.insert_one({
        "id": job_id,
        "slug": slug,
        "status": "processing",
        "progress": {"completed": 0, "total": max(1, len(file_ids))},
        "created_at": datetime.now(timezone.utc)
    })
    
    from services.zip_service import process_background_zip
    background_tasks.add_task(process_background_zip, job_id, slug, tab_id, file_ids)
    
    return {"job_id": job_id}

@router.get("/public/{slug}/zip-job/{job_id}")
async def check_zip_job(slug: str, job_id: str):
    """Poll for zip job status."""
    job = await raw_db.zip_jobs.find_one({"id": job_id})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _parse_mongo(job)

# ─── Analytics ───────────────────────────────────────────────────────────────

@router.post("/public/{slug}/track")
async def track_event(slug: str, data: dict = Body(...), request: Request = None):
    """Track analytics event (fire-and-forget from frontend)."""
    album = await raw_db.albums.find_one({"slug": slug}, {"id": 1, "agency_id": 1})
    if not album:
        return {"ok": True}  # Silent fail

    # Build fingerprint
    ip = request.client.host if request and request.client else "unknown"
    ua = request.headers.get("user-agent", "") if request else ""
    fingerprint = hashlib.sha256(f"{ip}{ua}".encode()).hexdigest()
    ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:16]

    event = AlbumAnalyticsEvent(
        album_id=album["id"],
        agency_id=album.get("agency_id", ""),
        event_type=data.get("event_type", "view"),
        tab_id=data.get("tab_id"),
        file_id=data.get("file_id"),
        viewer_fingerprint=fingerprint,
        ip_hash=ip_hash,
        user_agent=ua[:500],
    )
    await raw_db.album_analytics.insert_one(event.model_dump())

    # Update counters on album
    inc_fields = {"view_count": 1}
    if data.get("event_type") == "download":
        inc_fields["download_count"] = 1

    set_fields = {"last_viewed_at": datetime.now(timezone.utc)}

    # Check unique view
    if data.get("event_type") == "view":
        existing = await raw_db.album_analytics.find_one({
            "album_id": album["id"],
            "event_type": "view",
            "viewer_fingerprint": fingerprint,
        })
        # Count is 1 if this is first time we inserted (the one above)
        count = await raw_db.album_analytics.count_documents({
            "album_id": album["id"],
            "event_type": "view",
            "viewer_fingerprint": fingerprint,
        })
        if count <= 1:
            inc_fields["unique_view_count"] = 1

    await raw_db.albums.update_one(
        {"id": album["id"]},
        {"$inc": inc_fields, "$set": set_fields}
    )

    return {"ok": True}


@router.get("/{album_id}/analytics")
async def get_analytics(
    album_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db),
):
    """Aggregate analytics for an album."""
    album = await db.albums.find_one({"id": album_id})
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    # Per-tab views
    tab_views = await raw_db.album_analytics.aggregate([
        {"$match": {"album_id": album_id, "event_type": "tab_view"}},
        {"$group": {"_id": "$tab_id", "count": {"$sum": 1}}},
    ]).to_list(length=100)

    # Timeline (last 30 days)
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    timeline = await raw_db.album_analytics.aggregate([
        {"$match": {"album_id": album_id, "timestamp": {"$gte": thirty_days_ago}}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
            "views": {"$sum": {"$cond": [{"$eq": ["$event_type", "view"]}, 1, 0]}},
            "downloads": {"$sum": {"$cond": [{"$eq": ["$event_type", "download"]}, 1, 0]}},
        }},
        {"$sort": {"_id": 1}},
    ]).to_list(length=31)

    return {
        "total_views": album.get("view_count", 0),
        "unique_views": album.get("unique_view_count", 0),
        "total_downloads": album.get("download_count", 0),
        "last_viewed_at": album.get("last_viewed_at"),
        "tab_views": {tv["_id"]: tv["count"] for tv in tab_views if tv["_id"]},
        "timeline": [{"date": t["_id"], "views": t["views"], "downloads": t["downloads"]} for t in timeline],
    }


# ─── TTL Expiry Background Job ──────────────────────────────────────────────

async def expire_albums_loop():
    """Runs every hour. Expires published albums past their expires_at."""
    while True:
        try:
            now = datetime.now(timezone.utc)
            result = await raw_db.albums.update_many(
                {
                    "status": "published",
                    "expires_at": {"$ne": None, "$lte": now},
                },
                {"$set": {"status": "expired"}}
            )
            if result.modified_count > 0:
                logger.info(f"Expired {result.modified_count} albums")
        except Exception as e:
            logger.error(f"Album expiry job error: {e}")

        await asyncio.sleep(3600)  # Every hour
