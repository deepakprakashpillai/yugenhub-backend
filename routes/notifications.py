from fastapi import APIRouter, Depends, HTTPException
from typing import List
from database import notifications_collection
from models.notification import NotificationModel
from models.user import UserModel
from routes.deps import get_current_user
from logging_config import get_logger

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])
logger = get_logger("notifications")


def parse_mongo_data(data):
    if isinstance(data, list):
        return [parse_mongo_data(item) for item in data]
    if isinstance(data, dict):
        if "_id" in data:
            data["_id"] = str(data["_id"])
        return {k: parse_mongo_data(v) for k, v in data.items()}
    return data


@router.get("", response_model=List[dict])
async def get_notifications(
    unread_only: bool = False,
    current_user: UserModel = Depends(get_current_user)
):
    """Get all notifications for the current user."""
    query = {"user_id": current_user.id}
    if unread_only:
        query["read"] = False
    
    notifications = await notifications_collection.find(query).sort("created_at", -1).to_list(50)
    return parse_mongo_data(notifications)


@router.get("/unread-count")
async def get_unread_count(current_user: UserModel = Depends(get_current_user)):
    """Get count of unread notifications."""
    count = await notifications_collection.count_documents({
        "user_id": current_user.id,
        "read": False
    })
    return {"count": count}


@router.patch("/{notification_id}/read")
async def mark_as_read(
    notification_id: str,
    current_user: UserModel = Depends(get_current_user)
):
    """Mark a notification as read."""
    result = await notifications_collection.update_one(
        {"id": notification_id, "user_id": current_user.id},
        {"$set": {"read": True}}
    )
    if result.matched_count == 0:
        logger.warning(f"Notification not found for mark-as-read", extra={"data": {"notification_id": notification_id}})
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"message": "Marked as read"}


@router.post("/mark-all-read")
async def mark_all_read(current_user: UserModel = Depends(get_current_user)):
    """Mark all notifications as read for the current user."""
    await notifications_collection.update_many(
        {"user_id": current_user.id, "read": False},
        {"$set": {"read": True}}
    )
    return {"message": "All notifications marked as read"}
