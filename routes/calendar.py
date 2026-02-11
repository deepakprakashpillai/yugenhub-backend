from fastapi import APIRouter, HTTPException, Depends, Query
from typing import List, Optional
from datetime import datetime, timedelta
from database import projects_collection, tasks_collection, associates_collection
from models.user import UserModel
from routes.deps import get_current_user
from logging_config import get_logger

router = APIRouter(prefix="/api/calendar", tags=["Calendar"])
logger = get_logger("calendar")

@router.get("")
async def get_calendar_events(
    start: str = Query(..., description="Start date (YYYY-MM-DD)"),
    end: str = Query(..., description="End date (YYYY-MM-DD)"),
    type: Optional[str] = Query(None, description="Filter by type: 'event', 'task', or None for both"),
    assigned_only: bool = Query(False, description="Show only items assigned to the current user"),
    current_user: UserModel = Depends(get_current_user)
):
    """
    Get all calendar items (Project Events + Task Due Dates) within a date range.
    
    Filters:
    - type: 'event' (shoots only), 'task' (deliverables only), or None (both)
    - assigned_only: If true, shows only items assigned to the current user
    """
    current_agency_id = current_user.agency_id
    calendar_items = []

    # Parse dates
    try:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
        # Adjust end date to include the full day
        end_dt = end_dt.replace(hour=23, minute=59, second=59)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    # --- RESOLVE USER TO ASSOCIATE ID (for assigned_only filter) ---
    user_associate_id = None
    if assigned_only:
        # Find associate with matching email
        associate = await associates_collection.find_one({
            "email_id": current_user.email,
            "agency_id": current_agency_id
        })
        if associate:
            user_associate_id = str(associate["_id"])

    # 1. FETCH PROJECT EVENTS (Shoots, Meetings, etc.)
    if type != "task":  # Skip if only tasks requested
        # Find projects that have at least one event in the range
        projects_cursor = projects_collection.find({
            "agency_id": current_agency_id,
            "events": {"$ne": []} 
        })
        
        async for project in projects_cursor:
            for event in project.get("events", []):
                evt_date = event.get("start_date")
                if not evt_date:
                    continue
                
                # Check if it's already a datetime object (from Mongo) or string (legacy/some updates)
                if isinstance(evt_date, str):
                    try:
                        evt_dt = datetime.fromisoformat(evt_date)
                    except ValueError:
                        continue
                elif isinstance(evt_date, datetime):
                    evt_dt = evt_date
                else:
                    continue
                    
                if not (start_dt <= evt_dt <= end_dt):
                    continue
                
                # ASSIGNED_ONLY FILTER for events
                if assigned_only:
                    # If user has no associate record, they can't be assigned to any event
                    if not user_associate_id:
                        continue  # Skip all events
                    
                    # Check if current user's associate_id is in this event's assignments
                    event_assignments = event.get("assignments", [])
                    is_assigned = any(
                        a.get("associate_id") == user_associate_id 
                        for a in event_assignments
                    )
                    if not is_assigned:
                        continue  # Skip this event
                
                calendar_items.append({
                    "id": event.get("id"),
                    "type": "event",
                    "title": f"{project.get('code', 'Unknown')} - {event.get('type')}",
                    "date": evt_dt.strftime("%Y-%m-%d"),
                    "color": "blue",  # Default event color
                    "project_id": str(project["_id"]),
                    "project_code": project.get("code"),
                    "details": {
                        "venue": event.get("venue_name"),
                        "status": "scheduled"  # Default as events don't have explicit status usually
                    }
                })

    # 2. FETCH TASKS (Deliverables + General Tasks with Due Dates)
    if type != "event":  # Skip if only events requested
        task_query = {"studio_id": current_agency_id}
        
        # ASSIGNED_ONLY FILTER for tasks
        # RBAC: Members can ONLY see their own tasks
        if current_user.role.lower() == 'member':
            task_query["assigned_to"] = current_user.id
        elif assigned_only:
            # For non-members, respect the filter
            task_query["assigned_to"] = current_user.id
        
        # Fetch all tasks (filtering date in Python due to mixed data types: String vs Date)
        tasks_cursor = tasks_collection.find(task_query)

        async for task in tasks_cursor:
            due_date = task.get("due_date")
            if not due_date:
                continue
            
            # Normalize due_date to datetime
            task_dt = None
            if isinstance(due_date, datetime):
                task_dt = due_date
            elif isinstance(due_date, str):
                try:
                    # Handle both full ISO and YYYY-MM-DD
                    if "T" in due_date:
                        task_dt = datetime.fromisoformat(due_date)
                    else:
                        task_dt = datetime.strptime(due_date, "%Y-%m-%d")
                except ValueError:
                    continue # Skip invalid dates
            
            if not task_dt:
                continue

            # Filter by Date Range
            if not (start_dt <= task_dt <= end_dt):
                continue
                
            calendar_items.append({
                "id": task.get("id"),
                "type": "task",
                "title": task.get("title"),
                "date": task_dt.strftime("%Y-%m-%d"),
                "color": "green" if task.get("status") == "done" else "orange",
                "project_id": task.get("project_id"),
                "details": {
                    "status": task.get("status"),
                    "assignee": task.get("assigned_to")
                }
            })

    # Sort by date
    calendar_items.sort(key=lambda x: x["date"])

    logger.debug(f"Calendar query completed", extra={"data": {"start": start, "end": end, "type": type, "items_returned": len(calendar_items)}})
    return calendar_items

