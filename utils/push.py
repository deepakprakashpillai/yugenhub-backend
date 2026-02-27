import json
from pywebpush import webpush, WebPushException
from config import config
from logging_config import get_logger
from middleware.db_guard import ScopedDatabase

logger = get_logger("push_utils")

async def send_push_notification(db: ScopedDatabase, user_id: str, title: str, message: str, url: str = "/") -> int:
    """
    Send a Web Push Notification to all active subscriptions for a user.
    Returns the number of successful pushes.
    """
    if not config.VAPID_PRIVATE_KEY or not config.VAPID_CLAIM_EMAIL:
        logger.warning("VAPID keys not configured. Skipping push notification.")
        return 0

    # Retrieve user's push subscriptions
    subscriptions = await db.push_subscriptions.find({"user_id": user_id}).to_list(10)
    if not subscriptions:
        return 0

    # Retrieve user's notification preferences
    prefs = await db.notification_prefs.find_one({"user_id": user_id})
    if prefs and prefs.get("push_notifications") is False:
        # User explicitly disabled push notifications
        return 0

    vapid_claims = {
        "sub": config.VAPID_CLAIM_EMAIL
    }

    payload = json.dumps({
        "title": title,
        "body": message,
        "data": {
            "url": url
        }
    })

    success_count = 0
    endpoints_to_remove = []

    for sub in subscriptions:
        try:
            # We must pass the subscription info as a dict with endpoint and keys dict
            sub_info = {
                "endpoint": sub["endpoint"],
                "keys": sub["keys"]
            }
            webpush(
                subscription_info=sub_info,
                data=payload,
                vapid_private_key=config.VAPID_PRIVATE_KEY,
                vapid_claims=vapid_claims
            )
            success_count += 1
        except WebPushException as ex:
            # If the subscription is expired or no longer valid (status code 410 or 404),
            # we should remove it from our database.
            if ex.response is not None and ex.response.status_code in (410, 404):
                endpoints_to_remove.append(sub["endpoint"])
            else:
                logger.error(f"Failed to send Web Push: {repr(ex)}")
        except Exception as e:
            logger.error(f"Unexpected error sending Web Push: {str(e)}")

    # Clean up expired subscriptions
    if endpoints_to_remove:
        await db.push_subscriptions.delete_many({
            "user_id": user_id,
            "endpoint": {"$in": endpoints_to_remove}
        })
        logger.info(f"Removed {len(endpoints_to_remove)} expired push subscriptions for user {user_id}")

    return success_count
