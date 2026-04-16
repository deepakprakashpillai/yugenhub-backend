"""
R2 bucket usage statistics service.

Calculates per-agency storage breakdown by listing all objects under
media/{agency_id}/ and categorising by sub-prefix.

Results are cached in the `bucket_stats_cache` collection and are
considered stale after CACHE_TTL_HOURS hours.
"""
from datetime import datetime, timezone, timedelta
from logging_config import get_logger
from utils.r2 import get_r2_client
from config import config

logger = get_logger("r2_usage")

CACHE_TTL_HOURS = 24


def _categorise_key(key: str, agency_id: str) -> str:
    """Return the category name for an R2 key based on sub-prefix."""
    prefix = f"media/{agency_id}/"
    relative = key[len(prefix):]          # strip agency prefix
    if relative.startswith("files/"):
        return "original"
    if relative.startswith("thumbs/"):
        return "thumbnails"
    if relative.startswith("previews/"):
        return "previews"
    if relative.startswith("watermarks/"):
        return "watermarks"
    return "other"


async def calculate_bucket_stats(agency_id: str, db) -> dict:
    """
    List all R2 objects under media/{agency_id}/, tally sizes by category,
    persist the result to bucket_stats_cache, and return the stats dict.
    """
    prefix = f"media/{agency_id}/"
    r2 = get_r2_client()

    categories = {
        "original": 0,
        "thumbnails": 0,
        "previews": 0,
        "watermarks": 0,
        "other": 0,
    }
    file_count = 0

    paginator = r2.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=config.R2_BUCKET_NAME, Prefix=prefix)

    for page in pages:
        for obj in page.get("Contents", []):
            size = obj.get("Size", 0)
            cat = _categorise_key(obj["Key"], agency_id)
            categories[cat] += size
            file_count += 1

    total_bytes = sum(categories.values())
    derived_bytes = categories["thumbnails"] + categories["previews"] + categories["watermarks"]
    now = datetime.now(timezone.utc)

    stats = {
        "agency_id": agency_id,
        "total_bytes": total_bytes,
        "original_bytes": categories["original"],
        "thumbnail_bytes": categories["thumbnails"],
        "preview_bytes": categories["previews"],
        "watermark_bytes": categories["watermarks"],
        "other_bytes": categories["other"],
        "derived_bytes": derived_bytes,
        "file_count": file_count,
        "last_updated": now,
        "is_stale": False,
    }

    await db.bucket_stats_cache.update_one(
        {"agency_id": agency_id},
        {"$set": stats},
        upsert=True,
    )
    logger.info(f"R2 usage stats refreshed for agency {agency_id}: {total_bytes} bytes, {file_count} files")
    return stats


async def get_cached_stats(agency_id: str, db) -> dict:
    """
    Return cached stats. If never computed, compute now (blocking).
    Sets is_stale=True if cache is older than CACHE_TTL_HOURS.
    """
    cached = await db.bucket_stats_cache.find_one({"agency_id": agency_id})
    if not cached:
        return await calculate_bucket_stats(agency_id, db)

    last_updated = cached.get("last_updated")
    if last_updated and datetime.now(timezone.utc) - last_updated > timedelta(hours=CACHE_TTL_HOURS):
        cached["is_stale"] = True

    # Strip MongoDB _id before returning
    cached.pop("_id", None)
    return cached
