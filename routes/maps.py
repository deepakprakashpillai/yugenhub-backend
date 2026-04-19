from fastapi import APIRouter, Body, Depends, HTTPException

from models.location import MapLocation
from models.user import UserModel
from routes.deps import get_current_user
from utils.maps import resolve_to_location
from logging_config import get_logger

router = APIRouter(prefix="/api/maps", tags=["Maps"])
logger = get_logger("maps")


@router.post("/resolve", response_model=MapLocation)
async def resolve_maps_url(
    payload: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
):
    """Resolve a Google Maps URL (including short links) to a structured MapLocation."""
    url = payload.get("url", "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    if len(url) > 2048:
        raise HTTPException(status_code=400, detail="url is too long")

    result = await resolve_to_location(url)
    logger.info("Resolved maps URL", extra={"data": {"user": current_user.id, "source": result.source}})
    return result
