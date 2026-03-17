"""Background task orchestrators for thumbnail and watermark processing."""

from logging_config import get_logger
from utils.r2 import download_r2_object, upload_r2_object, delete_r2_object
from utils.media import generate_thumbnail, generate_video_thumbnail, apply_video_watermark
from database import projects_collection
from config import config

logger = get_logger("media_processing")


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
    """Async background task: download file, generate thumbnail, upload, update DB."""
    thumb_r2_key = f"thumbnails/{agency_id}/{project_id}/{file_id}.jpg"
    try:
        await _update_file_field(project_id, deliverable_id, file_id, {"thumbnail_status": "processing"})

        file_bytes = download_r2_object(r2_key)

        if content_type.startswith("image/"):
            thumb_bytes = generate_thumbnail(file_bytes, content_type)
        elif content_type.startswith("video/"):
            thumb_bytes = generate_video_thumbnail(file_bytes)
        else:
            return

        upload_r2_object(thumb_r2_key, thumb_bytes, "image/jpeg")
        thumb_url = f"{config.R2_PUBLIC_URL}/{thumb_r2_key}" if config.R2_PUBLIC_URL else thumb_r2_key

        await _update_file_field(project_id, deliverable_id, file_id, {
            "thumbnail_r2_key": thumb_r2_key,
            "thumbnail_r2_url": thumb_url,
            "thumbnail_status": "done",
        })
        logger.info(f"Thumbnail generated for file {file_id}")

    except Exception as e:
        logger.error(f"Thumbnail generation failed for file {file_id}: {e}")
        await _update_file_field(project_id, deliverable_id, file_id, {"thumbnail_status": "failed"})


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
