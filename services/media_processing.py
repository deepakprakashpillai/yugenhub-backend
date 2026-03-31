"""Background task orchestrators for thumbnail and watermark processing."""

from logging_config import get_logger
from utils.r2 import download_r2_object, upload_r2_object, delete_r2_object
from utils.media import generate_thumbnail, generate_preview, generate_video_thumbnail, apply_video_watermark
from database import projects_collection, albums_collection
from config import config

logger = get_logger("media_processing")


async def _update_album_file_field(album_id: str, tab_id: str, file_id: str, updates: dict):
    """Update specific fields on an AlbumFile within an album tab."""
    set_fields = {f"tabs.$[t].files.$[f].{k}": v for k, v in updates.items()}
    await albums_collection.update_one(
        {"id": album_id},
        {"$set": set_fields},
        array_filters=[{"t.id": tab_id}, {"f.id": file_id}],
    )


async def _update_file_field(project_id: str, deliverable_id: str, file_id: str, updates: dict):
    """Update specific fields on a DeliverableFile within a portal deliverable."""
    from bson import ObjectId
    set_fields = {f"portal_deliverables.$[d].files.$[f].{k}": v for k, v in updates.items()}
    await projects_collection.update_one(
        {"_id": ObjectId(project_id)},
        {"$set": set_fields},
        array_filters=[{"d.id": deliverable_id}, {"f.id": file_id}],
    )


async def process_thumbnail(project_id: str, deliverable_id: str, file_id: str, r2_key: str, content_type: str, agency_id: str):
    """Async background task: download file, generate thumbnail + preview (images), upload, update DB.

    For images: generates both a 400x400 thumbnail AND a 1920px preview in one pass (single R2 download).
    For videos: generates thumbnail only.
    """
    thumb_r2_key = f"thumbnails/{agency_id}/{project_id}/{file_id}.jpg"
    preview_r2_key = f"previews/{agency_id}/{project_id}/{file_id}.jpg"
    is_image = content_type.startswith("image/")

    try:
        initial_updates = {"thumbnail_status": "processing"}
        if is_image:
            initial_updates["preview_status"] = "processing"
        await _update_file_field(project_id, deliverable_id, file_id, initial_updates)

        file_bytes = download_r2_object(r2_key)  # Single download for all derived assets

        if is_image:
            thumb_bytes = generate_thumbnail(file_bytes, content_type)
            preview_bytes = generate_preview(file_bytes, content_type)
        elif content_type.startswith("video/"):
            thumb_bytes = generate_video_thumbnail(file_bytes)
        else:
            return

        upload_r2_object(thumb_r2_key, thumb_bytes, "image/jpeg")
        thumb_url = f"{config.R2_PUBLIC_URL}/{thumb_r2_key}" if config.R2_PUBLIC_URL else thumb_r2_key

        updates = {
            "thumbnail_r2_key": thumb_r2_key,
            "thumbnail_r2_url": thumb_url,
            "thumbnail_status": "done",
        }

        if is_image:
            upload_r2_object(preview_r2_key, preview_bytes, "image/jpeg")
            preview_url = f"{config.R2_PUBLIC_URL}/{preview_r2_key}" if config.R2_PUBLIC_URL else preview_r2_key
            updates.update({
                "preview_r2_key": preview_r2_key,
                "preview_r2_url": preview_url,
                "preview_status": "done",
            })

        await _update_file_field(project_id, deliverable_id, file_id, updates)
        logger.info(f"Thumbnail{'+ preview ' if is_image else ' '}generated for file {file_id}")

    except Exception as e:
        logger.error(f"Thumbnail generation failed for file {file_id}: {e}")
        fail_updates = {"thumbnail_status": "failed"}
        if is_image:
            fail_updates["preview_status"] = "failed"
        await _update_file_field(project_id, deliverable_id, file_id, fail_updates)


async def process_watermark(project_id: str, deliverable_id: str, file_id: str, r2_key: str, watermark_text: str, agency_id: str):
    """Async background task: download video, apply watermark, upload, update DB."""
    wm_r2_key = f"watermarks/{agency_id}/{project_id}/{file_id}_wm.mp4"
    try:
        await _update_file_field(project_id, deliverable_id, file_id, {"watermark_status": "processing"})

        video_bytes = download_r2_object(r2_key)
        wm_bytes = apply_video_watermark(video_bytes, watermark_text)

        upload_r2_object(wm_r2_key, wm_bytes, "video/mp4")
        wm_url = f"{config.R2_PUBLIC_URL}/{wm_r2_key}" if config.R2_PUBLIC_URL else wm_r2_key

        await _update_file_field(project_id, deliverable_id, file_id, {
            "watermark_r2_key": wm_r2_key,
            "watermark_r2_url": wm_url,
            "watermark_status": "done",
        })
        logger.info(f"Watermark applied for file {file_id}")

    except Exception as e:
        logger.error(f"Watermark processing failed for file {file_id}: {e}")
        await _update_file_field(project_id, deliverable_id, file_id, {"watermark_status": "failed"})


async def process_album_thumbnail(
    album_id: str, tab_id: str, file_id: str, r2_key: str, content_type: str, agency_id: str
):
    """Async background task: generate thumbnail + preview for an album file.

    For images: generates 400x400 thumbnail AND 1920px preview (single R2 download).
    For videos: generates thumbnail only (frame at 1s).
    """
    thumb_r2_key = f"album_thumbs/{agency_id}/{album_id}/{file_id}.jpg"
    preview_r2_key = f"album_previews/{agency_id}/{album_id}/{file_id}.jpg"
    is_image = content_type.startswith("image/")

    try:
        await _update_album_file_field(album_id, tab_id, file_id, {"thumbnail_status": "processing"})

        file_bytes = download_r2_object(r2_key)

        if is_image:
            thumb_bytes = generate_thumbnail(file_bytes, content_type)
            preview_bytes = generate_preview(file_bytes, content_type)
        elif content_type.startswith("video/"):
            thumb_bytes = generate_video_thumbnail(file_bytes)
        else:
            return

        upload_r2_object(thumb_r2_key, thumb_bytes, "image/jpeg")

        updates = {
            "thumbnail_r2_key": thumb_r2_key,
            "thumbnail_status": "done",
        }

        if is_image:
            upload_r2_object(preview_r2_key, preview_bytes, "image/jpeg")
            updates["preview_r2_key"] = preview_r2_key

        await _update_album_file_field(album_id, tab_id, file_id, updates)
        logger.info(f"Album thumbnail{'+ preview ' if is_image else ' '}generated for file {file_id}")

    except Exception as e:
        logger.error(f"Album thumbnail generation failed for file {file_id}: {e}")
        await _update_album_file_field(album_id, tab_id, file_id, {"thumbnail_status": "failed"})
