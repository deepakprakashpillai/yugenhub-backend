import httpx
from bson import ObjectId
from datetime import datetime, timedelta
from typing import Optional, Union

from logging_config import get_logger
from middleware.db_guard import ScopedDatabase

logger = get_logger("calendar_automation")

WEBHOOK_URL = "https://n8n.yugenco.in/webhook/calendar-sync"

def _parse_date(date_val: Union[datetime, str, None]) -> Optional[datetime]:
    if not date_val:
        return None
    if isinstance(date_val, datetime):
        return date_val
    try:
        if "T" in date_val:
            return datetime.fromisoformat(date_val.replace('Z', '+00:00'))
        return datetime.strptime(date_val, "%Y-%m-%d")
    except ValueError:
        logger.warning(f"Could not parse date string: {date_val}")
        return None

async def sync_event_to_calendar(
    db: ScopedDatabase,
    project_id: str,
    event_id: str,
    action: str,
    project_vertical: Optional[str] = None,
    title: Optional[str] = None,
    start_time: Union[datetime, str, None] = None,
    end_time: Union[datetime, str, None] = None,
    calendar_event_id: Optional[str] = None
) -> Union[bool, str]:
    """
    Syncs a project event with the external calendar via n8n webhook inline.
    Supported actions: 'create', 'update', 'delete'
    Returns string ID if created and saved, True if successfully updated/deleted, False if skipped/failed.
    """
    # 1. Check Global Config
    config = await db.agency_configs.find_one({})
    if not config:
        return False
        
    automations_config = config.get("automations", {})
    if not automations_config.get("calendar_enabled", False):
        logger.info(f"Calendar sync bypassed for event {event_id} - global integration disabled in settings.")
        return False
        
    # 2. Check Vertical Config
    if project_vertical:
        verticals = config.get("verticals", [])
        vertical_config = next((v for v in verticals if v.get("id") == project_vertical), None)
        if vertical_config and vertical_config.get("calendar_sync") is False:
            logger.info(f"Calendar sync bypassed for event {event_id} - disabled for vertical '{project_vertical}'.")
            return False

    payload = {
        "action": action
    }
    
    start_dt = _parse_date(start_time)
    end_dt = _parse_date(end_time)
    
    if action in ["create", "update"]:
        if not title or not start_dt:
            logger.error(f"Title and start_time are required for '{action}' calendar sync")
            return False
            
        payload["title"] = title
        payload["start_time"] = start_dt.isoformat()
        
        if not end_dt:
            end_dt = start_dt + timedelta(hours=1)
            
        payload["end_time"] = end_dt.isoformat()
        
    if action in ["update", "delete"]:
        if not calendar_event_id:
            if action == "update":
                payload["action"] = "create"
            else:
                logger.warning(f"No calendar_event_id provided for delete action on event {event_id}. Skipping.")
                return False
        else:
            payload["calendar_event_id"] = calendar_event_id

    try:
        # Reduced timeout since we are making the caller wait
        async with httpx.AsyncClient() as client:
            response = await client.post(
                WEBHOOK_URL,
                json=payload,
                timeout=3.0
            )
            response.raise_for_status()
            
            try:
                data = response.json()
            except Exception:
                data = {}
            
            if payload["action"] == "create":
                returned_id = data.get("calendar_event_id") or data.get("calender_event_id")
                if returned_id:
                    await db.projects.update_one(
                        {"_id": ObjectId(project_id), "events.id": event_id},
                        {"$set": {"events.$.calendar_event_id": returned_id}}
                    )
                    logger.info(f"Successfully synced and saved calendar ID ({returned_id}) for event {event_id}")
                    return returned_id
                else:
                    logger.warning(f"Webhook returned success for create, but no calendar_event_id was found in response. Response: {data}")
                    # Synced but didn't return ID, still technically pushed
                    return True
            else:
                logger.info(f"Successfully synced calendar action '{action}' for event {event_id}")
                return True
                
    except httpx.HTTPError as e:
        logger.error(f"HTTP Error during calendar sync for event {event_id}: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error during calendar sync for event {event_id}: {str(e)}")
        return False
