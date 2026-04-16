"""
Helper to find-or-create system folders in the Media library.
Used by deliverable upload integration and the migration service.
"""
from models.media import MediaFolder
from logging_config import get_logger

logger = get_logger("media_folders")


async def get_or_create_system_folder(agency_id: str, path_parts: list, db) -> str:
    """
    Walk path_parts top-down, finding or creating each folder.
    All folders created here are marked is_system=True.
    Returns the leaf folder_id.
    """
    parent_id = None
    current_path = "/"

    for part in path_parts:
        current_path = f"{current_path}{part}/"

        existing = await db.media_folders.find_one({
            "agency_id": agency_id,
            "name": part,
            "parent_id": parent_id,
        })

        if existing:
            parent_id = existing["id"]
        else:
            folder = MediaFolder(
                agency_id=agency_id,
                name=part,
                parent_id=parent_id,
                path=current_path,
                is_system=True,
            )
            await db.media_folders.insert_one(folder.model_dump())
            parent_id = folder.id
            logger.info(
                "Created system folder",
                extra={"data": {"path": current_path, "agency_id": agency_id}}
            )

    return parent_id
