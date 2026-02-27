from fastapi import APIRouter, Depends, HTTPException, Body
from models.user import UserModel
from routes.deps import get_current_user, get_db
from middleware.db_guard import ScopedDatabase
from config import config
from models.push_subscription import PushSubscriptionModel
from logging_config import get_logger

router = APIRouter(prefix="/api/push", tags=["Push Notifications"])
logger = get_logger("push")

@router.get("/vapid-public-key")
async def get_vapid_public_key():
    """Return the VAPID public key for the frontend to use when subscribing."""
    public_key = config.VAPID_PUBLIC_KEY
    if not public_key:
        raise HTTPException(status_code=500, detail="VAPID_PUBLIC_KEY is not configured on the server")
    return {"public_key": public_key}

@router.post("/subscribe")
async def subscribe_push(
    subscription: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Save a push subscription for the current user."""
    endpoint = subscription.get("endpoint")
    keys = subscription.get("keys")
    if not endpoint or not keys:
        raise HTTPException(status_code=400, detail="Invalid push subscription payload")

    # Upsert the subscription using the endpoint as the unique identifier
    await db.push_subscriptions.update_one(
        {"user_id": current_user.id, "endpoint": endpoint},
        {"$set": {
            "keys": keys,
            "agency_id": current_user.agency_id
        }},
        upsert=True
    )
    
    logger.info(f"Push subscription saved", extra={"data": {"user_id": current_user.id}})
    return {"message": "Subscription saved"}

@router.delete("/subscribe")
async def unsubscribe_push(
    subscription: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Remove a push subscription for the current user."""
    endpoint = subscription.get("endpoint")
    if not endpoint:
        raise HTTPException(status_code=400, detail="Endpoint is required")

    result = await db.push_subscriptions.delete_one({
        "user_id": current_user.id,
        "endpoint": endpoint
    })
    
    if result.deleted_count > 0:
        logger.info(f"Push subscription removed", extra={"data": {"user_id": current_user.id}})
    
    return {"message": "Subscription removed"}
