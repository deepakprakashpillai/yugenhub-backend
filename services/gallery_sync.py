"""Gallery sync service — auto-create and sync gallery albums with project events."""

import uuid
from datetime import datetime, timezone
from logging_config import get_logger
from models.album import AlbumModel, AlbumTabModel, LandingPageConfig

logger = get_logger("gallery_sync")


async def ensure_project_album(project_id: str, project: dict, agency_id: str, albums_col) -> str:
    """
    Ensure a gallery album exists for the given project.
    Creates one if missing. Returns album_id.
    This is idempotent — safe to call multiple times.
    """
    # Use existing album_id if already linked
    existing_album_id = project.get("gallery_album_id")
    if existing_album_id:
        # Verify it still exists
        existing = await albums_col.find_one({"id": existing_album_id})
        if existing:
            return existing_album_id
        # Album was deleted, we'll re-create below

    # Build album title from project metadata / code
    metadata = project.get("metadata", {})
    title_parts = [v for v in [
        metadata.get("bride_name") or metadata.get("client_name"),
        metadata.get("groom_name"),
        project.get("code"),
    ] if v]
    album_title = " & ".join(title_parts[:2]) if len(title_parts) >= 2 else (title_parts[0] if title_parts else project.get("code", "Gallery"))

    # One tab per event
    events = project.get("events", [])
    tabs = []
    for i, event in enumerate(events):
        tab = AlbumTabModel(
            title=event.get("type", f"Event {i + 1}"),
            sort_order=i,
            event_id=event.get("id"),
        )
        tabs.append(tab.model_dump())

    # If no events, create a single default tab
    if not tabs:
        default_tab = AlbumTabModel(title="Gallery", sort_order=0)
        tabs.append(default_tab.model_dump())

    album = AlbumModel(
        agency_id=agency_id,
        project_id=project_id,
        client_id=project.get("client_id"),
        vertical=project.get("vertical"),
        title=album_title,
        slug=str(uuid.uuid4()),
        status="draft",
        tabs=[AlbumTabModel(**t) for t in tabs],
        landing_page=LandingPageConfig(color_scheme="light"),
    )

    album_data = album.model_dump()

    await albums_col.insert_one(album_data)
    logger.info(f"Auto-created gallery album for project {project_id}", extra={"data": {"album_id": album.id, "tabs": len(tabs)}})
    return album.id


async def sync_event_to_album_tab(album_id: str, event: dict, albums_col) -> bool:
    """
    Ensure the album has a tab for the given event.
    Creates the tab if missing. Idempotent.
    Returns True if a new tab was created.
    """
    event_id = event.get("id")
    album = await albums_col.find_one({"id": album_id})
    if not album:
        logger.warning(f"Album {album_id} not found for event sync")
        return False

    # Check if tab already exists for this event
    for tab in album.get("tabs", []):
        if tab.get("event_id") == event_id:
            return False  # Already exists

    # Count existing tabs for sort_order
    existing_count = len(album.get("tabs", []))
    new_tab = AlbumTabModel(
        title=event.get("type", "Event"),
        sort_order=existing_count,
        event_id=event_id,
    )

    await albums_col.update_one(
        {"id": album_id},
        {
            "$push": {"tabs": new_tab.model_dump()},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        }
    )
    logger.info(f"Added tab for event {event_id} to album {album_id}")
    return True


def compute_gallery_url(album: dict, gallery_frontend_url: str):
    """Compute the public gallery URL for a published album."""
    if album.get("status") != "published":
        return None
    slug = album.get("slug")
    if not slug:
        return None
    return f"{gallery_frontend_url.rstrip('/')}/gallery/{slug}"
