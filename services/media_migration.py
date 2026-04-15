"""
Media Migration Service — Step 6
Migrates existing deliverable and album files into the Media library.

For each file:
  1. Copy R2 object to new media/ key path
  2. Create MediaItem record
  3. Update the source record (DeliverableFile / AlbumFile) with new r2_key + media_item_id
  4. Delete old R2 object
  5. Also repath thumbnail / preview / watermark keys
"""
from pathlib import Path
from datetime import datetime, timezone
from bson import ObjectId
import uuid

from database import db as raw_db
from models.media import MediaItem, MediaFolder
from config import config
from logging_config import get_logger
from utils.r2 import copy_r2_object, delete_r2_object

logger = get_logger("media_migration")


# ─── Internal helpers ──────────────────────────────────────────────────────────

async def _update_job(job_id: str, **fields):
    await raw_db.migration_jobs.update_one(
        {"job_id": job_id},
        {"$set": {"updated_at": datetime.now(timezone.utc), **fields}}
    )


async def _get_or_create_folder(agency_id: str, name: str, parent_id, path: str) -> str:
    """Find or create a single folder; returns its id."""
    existing = await raw_db.media_folders.find_one({
        "agency_id": agency_id,
        "name": name,
        "parent_id": parent_id,
    })
    if existing:
        return existing["id"]
    folder = MediaFolder(
        agency_id=agency_id,
        name=name,
        parent_id=parent_id,
        path=path,
        is_system=True,
    )
    await raw_db.media_folders.insert_one(folder.model_dump())
    return folder.id


async def _ensure_folder_path(agency_id: str, path_parts: list) -> str:
    """Walk path_parts, find-or-create each folder. Returns leaf folder_id."""
    parent_id = None
    current_path = "/"
    for part in path_parts:
        current_path = f"{current_path}{part}/"
        parent_id = await _get_or_create_folder(agency_id, part, parent_id, current_path)
    return parent_id


def _new_r2_key(agency_id: str, file_id: str, old_key: str) -> str:
    ext = Path(old_key).suffix.lower()
    return f"media/{agency_id}/files/{file_id}{ext}"


def _new_thumb_key(agency_id: str, file_id: str) -> str:
    return f"media/{agency_id}/thumbs/{file_id}.jpg"


def _new_preview_key(agency_id: str, file_id: str) -> str:
    return f"media/{agency_id}/previews/{file_id}.jpg"


def _new_watermark_key(agency_id: str, file_id: str) -> str:
    return f"media/{agency_id}/watermarks/{file_id}_wm.mp4"


def _copy_safe(src: str, dst: str) -> bool:
    """Copy R2 object, return True on success."""
    try:
        copy_r2_object(src, dst)
        return True
    except Exception as e:
        logger.warning(f"R2 copy failed {src} → {dst}: {e}")
        return False


def _delete_safe(key: str):
    try:
        delete_r2_object(key)
    except Exception as e:
        logger.warning(f"R2 delete failed {key}: {e}")


def _public_url(r2_key: str) -> str:
    if config.R2_PUBLIC_URL:
        return f"{config.R2_PUBLIC_URL}/{r2_key}"
    return ""


# ─── Main migration runner ─────────────────────────────────────────────────────

async def run_migration(agency_id: str, job_id: str):
    """
    Full migration: deliverable files + album files → Media library.
    Runs as a background task; writes progress to migration_jobs collection.
    """
    migrated = 0
    failed = 0
    errors = []

    try:
        await _update_job(job_id, status="running")

        # Count total unmigrated files upfront for progress tracking
        projects = await raw_db.projects.find({"agency_id": agency_id}).to_list(length=None)
        albums = await raw_db.albums.find({"agency_id": agency_id}).to_list(length=None)
        total = sum(
            1 for p in projects
            for pd in p.get("portal_deliverables", [])
            for f in pd.get("files", [])
            if not f.get("media_item_id") and f.get("r2_key")
        ) + sum(
            1 for a in albums
            for t in a.get("tabs", [])
            for f in t.get("files", [])
            if f.get("r2_key")
        )
        await _update_job(job_id, total=total)

        # ── 1. Deliverable files ───────────────────────────────────────────────

        for project in projects:
            project_id = str(project["_id"])
            project_label = project.get("code") or project.get("metadata", {}).get("project_type", "Project")

            for pd in project.get("portal_deliverables", []):
                deliverable_id = pd["id"]
                deliverable_title = pd.get("title", deliverable_id)

                for file in pd.get("files", []):
                    file_id = file["id"]

                    # Skip already-migrated files
                    if file.get("media_item_id"):
                        continue

                    old_key = file.get("r2_key")
                    if not old_key:
                        continue

                    new_key = _new_r2_key(agency_id, file_id, old_key)

                    # Copy main file
                    if not _copy_safe(old_key, new_key):
                        failed += 1
                        errors.append(f"Copy failed: {old_key}")
                        continue

                    # Repath derived keys
                    new_thumb_key = None
                    new_preview_key = None
                    new_watermark_key = None

                    if file.get("thumbnail_r2_key"):
                        tk = _new_thumb_key(agency_id, file_id)
                        if _copy_safe(file["thumbnail_r2_key"], tk):
                            new_thumb_key = tk

                    if file.get("preview_r2_key"):
                        pk = _new_preview_key(agency_id, file_id)
                        if _copy_safe(file["preview_r2_key"], pk):
                            new_preview_key = pk

                    if file.get("watermark_r2_key"):
                        wk = _new_watermark_key(agency_id, file_id)
                        if _copy_safe(file["watermark_r2_key"], wk):
                            new_watermark_key = wk

                    # Ensure system folders
                    try:
                        folder_id = await _ensure_folder_path(
                            agency_id,
                            ["Deliverables", project_label, deliverable_title]
                        )
                    except Exception as e:
                        failed += 1
                        errors.append(f"Folder creation failed for {file_id}: {e}")
                        _delete_safe(new_key)
                        continue

                    # Create MediaItem
                    content_type = file.get("content_type", "application/octet-stream")
                    is_image = content_type.startswith("image/")
                    is_video = content_type.startswith("video/")
                    media_item = MediaItem(
                        id=file_id,  # reuse file_id for stable reference
                        agency_id=agency_id,
                        folder_id=folder_id,
                        name=file.get("file_name", "file"),
                        r2_key=new_key,
                        r2_url=_public_url(new_key),
                        content_type=content_type,
                        size_bytes=0,
                        thumbnail_r2_key=new_thumb_key,
                        thumbnail_r2_url=_public_url(new_thumb_key) if new_thumb_key else None,
                        thumbnail_status="done" if new_thumb_key else ("n/a" if not (is_image or is_video) else "pending"),
                        preview_r2_key=new_preview_key,
                        preview_r2_url=_public_url(new_preview_key) if new_preview_key else None,
                        preview_status="done" if new_preview_key else ("n/a" if not is_image else "pending"),
                        watermark_r2_key=new_watermark_key,
                        watermark_r2_url=_public_url(new_watermark_key) if new_watermark_key else None,
                        watermark_status="done" if new_watermark_key else "n/a",
                        source="deliverable",
                        source_project_id=project_id,
                        source_deliverable_id=deliverable_id,
                        status="active",
                    )
                    try:
                        await raw_db.media_items.insert_one(media_item.model_dump())
                    except Exception as e:
                        # Might already exist from a previous partial run
                        if "duplicate" not in str(e).lower():
                            failed += 1
                            errors.append(f"MediaItem insert failed {file_id}: {e}")
                            _delete_safe(new_key)
                            continue

                    # Update DeliverableFile in DB
                    set_fields = {
                        "portal_deliverables.$[d].files.$[f].r2_key": new_key,
                        "portal_deliverables.$[d].files.$[f].r2_url": _public_url(new_key),
                        "portal_deliverables.$[d].files.$[f].media_item_id": media_item.id,
                    }
                    if new_thumb_key:
                        set_fields["portal_deliverables.$[d].files.$[f].thumbnail_r2_key"] = new_thumb_key
                        set_fields["portal_deliverables.$[d].files.$[f].thumbnail_r2_url"] = _public_url(new_thumb_key)
                    if new_preview_key:
                        set_fields["portal_deliverables.$[d].files.$[f].preview_r2_key"] = new_preview_key
                        set_fields["portal_deliverables.$[d].files.$[f].preview_r2_url"] = _public_url(new_preview_key)
                    if new_watermark_key:
                        set_fields["portal_deliverables.$[d].files.$[f].watermark_r2_key"] = new_watermark_key
                        set_fields["portal_deliverables.$[d].files.$[f].watermark_r2_url"] = _public_url(new_watermark_key)

                    await raw_db.projects.update_one(
                        {"_id": ObjectId(project_id)},
                        {"$set": set_fields},
                        array_filters=[{"d.id": deliverable_id}, {"f.id": file_id}]
                    )

                    # Delete old R2 objects
                    _delete_safe(old_key)
                    if file.get("thumbnail_r2_key") and new_thumb_key:
                        _delete_safe(file["thumbnail_r2_key"])
                    if file.get("preview_r2_key") and new_preview_key:
                        _delete_safe(file["preview_r2_key"])
                    if file.get("watermark_r2_key") and new_watermark_key:
                        _delete_safe(file["watermark_r2_key"])

                    migrated += 1
                    await _update_job(job_id, migrated=migrated, failed=failed)

        # ── 2. Album files ─────────────────────────────────────────────────────
        for album in albums:
            album_id = album["id"]
            album_title = album.get("title", album_id)

            for tab in album.get("tabs", []):
                tab_title = tab.get("title", "Gallery")

                for file in tab.get("files", []):
                    file_id = file["id"]

                    # Skip already-migrated
                    if await raw_db.media_items.find_one({"id": file_id}):
                        continue

                    old_key = file.get("r2_key")
                    if not old_key:
                        continue

                    new_key = _new_r2_key(agency_id, file_id, old_key)

                    if not _copy_safe(old_key, new_key):
                        failed += 1
                        errors.append(f"Album copy failed: {old_key}")
                        continue

                    new_thumb_key = None
                    new_preview_key = None

                    if file.get("thumbnail_r2_key"):
                        tk = _new_thumb_key(agency_id, file_id)
                        if _copy_safe(file["thumbnail_r2_key"], tk):
                            new_thumb_key = tk

                    if file.get("preview_r2_key"):
                        pk = _new_preview_key(agency_id, file_id)
                        if _copy_safe(file["preview_r2_key"], pk):
                            new_preview_key = pk

                    try:
                        folder_id = await _ensure_folder_path(
                            agency_id,
                            ["Albums", album_title, tab_title]
                        )
                    except Exception as e:
                        failed += 1
                        errors.append(f"Album folder creation failed {file_id}: {e}")
                        _delete_safe(new_key)
                        continue

                    content_type = file.get("content_type", "application/octet-stream")
                    is_image = content_type.startswith("image/")
                    is_video = content_type.startswith("video/")
                    media_item = MediaItem(
                        id=file_id,
                        agency_id=agency_id,
                        folder_id=folder_id,
                        name=file.get("file_name", "file"),
                        r2_key=new_key,
                        r2_url=_public_url(new_key),
                        content_type=content_type,
                        size_bytes=file.get("size_bytes") or 0,
                        thumbnail_r2_key=new_thumb_key,
                        thumbnail_r2_url=_public_url(new_thumb_key) if new_thumb_key else None,
                        thumbnail_status="done" if new_thumb_key else ("n/a" if not (is_image or is_video) else "pending"),
                        preview_r2_key=new_preview_key,
                        preview_r2_url=_public_url(new_preview_key) if new_preview_key else None,
                        preview_status="done" if new_preview_key else ("n/a" if not is_image else "pending"),
                        source="album",
                        source_album_id=album_id,
                        status="active",
                    )
                    try:
                        await raw_db.media_items.insert_one(media_item.model_dump())
                    except Exception as e:
                        if "duplicate" not in str(e).lower():
                            failed += 1
                            errors.append(f"Album MediaItem insert failed {file_id}: {e}")
                            _delete_safe(new_key)
                            continue

                    # Update album file in DB
                    set_fields = {
                        "tabs.$[t].files.$[f].r2_key": new_key,
                    }
                    if new_thumb_key:
                        set_fields["tabs.$[t].files.$[f].thumbnail_r2_key"] = new_thumb_key
                    if new_preview_key:
                        set_fields["tabs.$[t].files.$[f].preview_r2_key"] = new_preview_key

                    await raw_db.albums.update_one(
                        {"id": album_id},
                        {"$set": set_fields},
                        array_filters=[{"t.id": tab["id"]}, {"f.id": file_id}]
                    )

                    _delete_safe(old_key)
                    if file.get("thumbnail_r2_key") and new_thumb_key:
                        _delete_safe(file["thumbnail_r2_key"])
                    if file.get("preview_r2_key") and new_preview_key:
                        _delete_safe(file["preview_r2_key"])

                    migrated += 1
                    await _update_job(job_id, migrated=migrated, failed=failed)

        # ── 3. Finalise ────────────────────────────────────────────────────────
        await _update_job(
            job_id,
            status="completed",
            migrated=migrated,
            failed=failed,
            errors=errors[-50:],  # keep last 50 errors max
            completed_at=datetime.now(timezone.utc),
        )
        logger.info(
            "Migration completed",
            extra={"data": {"agency_id": agency_id, "migrated": migrated, "failed": failed}}
        )

    except Exception as e:
        logger.error(f"Migration failed with unexpected error: {e}", exc_info=True)
        await _update_job(
            job_id,
            status="failed",
            errors=errors + [str(e)],
            completed_at=datetime.now(timezone.utc),
        )
