from fastapi import APIRouter, Body, HTTPException, Query, BackgroundTasks
import re # IMPORTED
import secrets
from bson import ObjectId
from datetime import datetime, timezone
# REMOVED raw collection imports
from models.project import ProjectModel, EventModel, DeliverableModel, AssignmentModel, PortalDeliverableModel, FeedbackEntry, DeliverableFile
from models.notification import NotificationModel
from models.task import TaskModel # IMPORTED
from routes.deps import get_current_user, get_db, get_user_verticals
from models.user import UserModel
from middleware.db_guard import ScopedDatabase
from fastapi import Depends
from logging_config import get_logger
from config import config
from utils.email import send_event_assignment_email
from utils.push import send_push_notification
from utils.r2 import generate_presigned_put_url, delete_r2_object
from automations.calendar import sync_event_to_calendar, sync_attendee_to_calendar
from services.deliverable_sync import (
    on_deliverable_task_created, on_task_status_changed,
    on_task_quantity_changed, reconcile_project,
    on_portal_file_added, on_portal_file_removed,
)

router = APIRouter(prefix="/api/projects", tags=["Projects"])
logger = get_logger("projects")

async def _resolve_project_name(project: dict, db: ScopedDatabase) -> str:
    """Replicates the frontend's regex pattern replacing for vertical metadata templates."""
    metadata = project.get("metadata", {})
    fallback_name = metadata.get("project_type", project.get("code", "Project"))
    
    vertical_id = project.get("vertical")
    if not vertical_id:
        return fallback_name
        
    config = await db.agency_configs.find_one({})
    if not config:
        return fallback_name
        
    verticals = config.get("verticals", [])
    v_id = vertical_id.lower()
    vertical_config = next((v for v in verticals if v.get("id", "").lower() == v_id), None)
    
    if not vertical_config or not vertical_config.get("title_template"):
        return fallback_name
        
    template = vertical_config["title_template"]
    
    # Create a case-insensitive lookup dict
    lower_metadata = {k.lower(): v for k, v in metadata.items()}
    
    def replacer(match):
        key = match.group(1).lower()
        val = lower_metadata.get(key, "")
        if val and isinstance(val, str):
            val = val.strip()
            return val.split(" ")[0] if val else ""
        return str(val) if val else ""
        
    resolved = re.sub(r"\{(\w+)\}", replacer, template)
    resolved = resolved.strip()
    resolved = re.sub(r"^[&\s]+|[&\s]+$", "", resolved)
    
    return resolved if resolved and resolved != "&" else fallback_name

# --- HELPER: Notify Associate on Assignment ---
async def notify_associate_assignment(db: ScopedDatabase, background_tasks: BackgroundTasks, associate_id: str, project_code: str, event_type: str, event_date: datetime, agency_id: str):
    """
    Send a notification to an associate when they are assigned to an event.
    Looks up the associate's email, finds the corresponding user, and creates notification.
    """
    if not associate_id or not ObjectId.is_valid(associate_id):
        return
    
    # Find associate and their email
    associate = await db.associates.find_one({"_id": ObjectId(associate_id)})
    if not associate:
        return
        
    associate_email = associate.get("email_id") or associate.get("email")
    if not associate_email:
        return  # No email, can't notify
    
    # Find user with this email -- USERS collection is special, might need checking
    
    user = await db.users.find_one({"email": associate_email})
    if not user:
        return  # No user account, can't notify
    
    # Format Date
    formatted_date = event_date.strftime("%b %d, %Y") if event_date else "TBD"

    # Create notification
    notification = NotificationModel(
        user_id=user.get("id"),
        agency_id=agency_id,
        type="event_assigned",
        title="Assigned to Event",
        message=f"You have been assigned to {event_type} on {formatted_date} for project {project_code}",
        resource_type="project",
        resource_id=None  # Could add project_id here
    )
    
    await db.notifications.insert_one(notification.model_dump())
    
    # Send Email!
    try:
        org_config = await db.agency_configs.find_one({})
        org_name = org_config.get("org_name", "My Agency") if org_config else "My Agency"
        background_tasks.add_task(
            send_event_assignment_email,
            to_email=user.get("email"), # This comes from the linked user's data
            org_name=org_name,
            associate_name=associate.get("name", "Associate"),
            project_code=project_code,
            event_type=event_type,
            event_date=event_date,
            frontend_url=config.FRONTEND_URL
        )
    except Exception as e:
        logger.error(f"Failed to queue event assignment email: {e}")

    # Send Push Notification
    background_tasks.add_task(
        send_push_notification,
        db=db,
        user_id=user.get("id"),
        title="Assigned to Event",
        message=f"You have been assigned to {event_type} on {formatted_date} for project {project_code}",
        url=f"/projects"
    )

    logger.info(f"Notification sent to associate for event assignment", extra={"data": {"associate": associate.get('name'), "project": project_code, "event": event_type}})

# --- HELPER FUNCTION ---
# Recursively fixes ObjectId errors for nested events/deliverables
def parse_mongo_data(data):
    if isinstance(data, list):
        return [parse_mongo_data(item) for item in data]
    if isinstance(data, dict):
        return {k: (str(v) if isinstance(v, ObjectId) else parse_mongo_data(v)) for k, v in data.items()}
    return data

# --- HELPER: Sequential Project Codes ---
async def get_next_sequence_value(db: ScopedDatabase, vertical: str) -> str:
    """
    Generate a sequential project code: [PREFIX]-[YEAR]-[SEQ]
    Example: KN-2026-0001
    """
    now = datetime.now(timezone.utc)
    year = now.year
    prefix = vertical[:2].upper()
    
    # Counter key is unique per vertical and year within the agency
    counter_id = f"projects_{vertical}_{year}"
    
    # Atomic increment using find_one_and_update
    # We use db.counters (ScopedCollection) which automatically scopes by agency_id
    counter = await db.counters.find_one_and_update(
        {"_id": counter_id},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True
    )
    
    seq_number = counter.get("seq", 1)
    return f"{prefix}-{year}-{seq_number:04d}"

# --- CORE ENDPOINTS ---

@router.post("", status_code=201)
async def create_project(
    background_tasks: BackgroundTasks,
    project: ProjectModel = Body(...), 
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """CREATE: Generate sequential code and save project"""
    project.agency_id = current_user.agency_id

    # 1. Fetch the active config
    config = await db.agency_configs.find_one({})
    allowed_verticals = [v["id"] for v in config.get("verticals", [])] if config else []
    
    # 2. Validation
    if project.vertical not in allowed_verticals:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid vertical. Allowed: {allowed_verticals}"
        )
    
    # 3. RBAC: Check user has access to this vertical
    user_verticals = await get_user_verticals(current_user, db)
    if project.vertical not in user_verticals:
        raise HTTPException(status_code=403, detail="You don't have access to this vertical")

    # 3. Automatic Code Generation
    # If code is missing or placeholder-y, generate a sequential one
    if not project.code or "-" not in project.code:
        project.code = await get_next_sequence_value(db, project.vertical)

    project_data = project.model_dump()
    project_data["portal_token"] = secrets.token_urlsafe(32)
    project_data["code"] = project_data["code"].upper()
    
    # Double check for duplicate (Collision guardrail)
    if await db.projects.find_one({"code": project_data["code"]}):
        # If collision happens (rare with atomic counters but possible if manual entry exists), 
        # try one more time or fail. 
        # For simplicity, we'll try one more increment if code was auto-generated.
        project_data["code"] = await get_next_sequence_value(db, project.vertical)
        if await db.projects.find_one({"code": project_data["code"]}):
             raise HTTPException(status_code=400, detail="Project code collision detected")

    # 4. Save Project
    # Guardrail ensures agency_id is injected
    new_project = await db.projects.insert_one(project_data)
    project_id = str(new_project.inserted_id)
    project_data["_id"] = project_id
    
    # 5. Handle Nested Events (Sync Tasks & Notifications)
    # If events were provided in the initial payload, we process them now.
    if project.events:
        all_new_tasks = []
        for event in project.events:
            # Sync to external calendar (Synchronous to provide immediate feedback to frontend)
            project_name = await _resolve_project_name(project_data, db)
            sync_result = await sync_event_to_calendar(
                db=db,
                project_id=project_id,
                event_id=event.id,
                action="create",
                project_vertical=project_data.get("vertical"),
                title=f"{event.type} - {project_name}",
                start_time=event.start_date,
                end_time=event.end_date
            )
            
            # Inject calendar_event_id into the local dict so it is sent down in the API response
            if sync_result and isinstance(sync_result, str):
                for e_dict in project_data.get("events", []):
                    if e_dict.get("id") == event.id:
                        e_dict["calendar_event_id"] = sync_result
                        break
            
            # a. Create Tasks for Deliverables
            if event.deliverables:
                for deliverable in event.deliverables:
                    task = TaskModel(
                        title=f"{deliverable.type} ({event.type})",
                        description=f"Deliverable for {event.type}",
                        project_id=project_id,
                        event_id=event.id,
                        deliverable_id=deliverable.id,
                        status="todo",
                        priority="medium",
                        due_date=deliverable.due_date,
                        assigned_to=deliverable.incharge_id,
                        studio_id=current_user.agency_id,
                        created_by=current_user.id,
                        type="project",
                        category="deliverable",
                        quantity=deliverable.quantity,
                    )
                    all_new_tasks.append(task.model_dump())
            
            # b. Send Notifications & Sync Attendees for Assignments
            if event.assignments:
                for assignment in event.assignments:
                    await notify_associate_assignment(
                        db,
                        background_tasks,
                        assignment.associate_id,
                        project_data["code"],
                        event.type,
                        event.start_date,
                        current_user.agency_id
                    )
                    
                    if sync_result and isinstance(sync_result, str):
                        if ObjectId.is_valid(assignment.associate_id):
                            associate = await db.associates.find_one({"_id": ObjectId(assignment.associate_id)})
                            if associate:
                                email = associate.get("email_id") or associate.get("email")
                                if email:
                                    background_tasks.add_task(
                                        sync_attendee_to_calendar,
                                        db=db,
                                        calendar_event_id=sync_result,
                                        email=email,
                                        action="add_attendee"
                                    )
        
        if all_new_tasks:
            await db.tasks.insert_many(all_new_tasks)
            # Auto-create portal deliverables for each task
            for task_dict in all_new_tasks:
                await on_deliverable_task_created(db, task_dict, project_id)
            logger.info(f"Created {len(all_new_tasks)} tasks during project creation", extra={"data": {"project_id": project_id}})

    logger.info(f"Project created with sequential code", extra={"data": {"code": project_data['code'], "vertical": project_data['vertical'], "event_count": len(project.events)}})
    
    return parse_mongo_data(project_data)

@router.get("")
async def list_projects(
    vertical: str = None, 
    search: str = None,
    status: str = None,
    view: str = "all",
    sort: str = "newest",
    page: int = Query(1, ge=1), 
    limit: int = Query(12, le=1000),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """READ LIST: Get projects with pagination, filtering, and sorting"""
    # Helper for robust date parsing inside the endpoint
    def parse_event_date(date_val):
        if not date_val: return None
        if isinstance(date_val, datetime):
            if date_val.tzinfo is None:
                return date_val.replace(tzinfo=timezone.utc)
            return date_val
        if isinstance(date_val, str):
            try:
                dt = datetime.fromisoformat(date_val.replace('Z', '+00:00'))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                return None
        return None

    # Guardrail handles agency_id, so we just build the rest of the query
    query = {}
    
    # RBAC: Scope to user's allowed verticals
    user_verticals = await get_user_verticals(current_user, db)
    if vertical:
        # If requesting a specific vertical, verify access
        if vertical not in user_verticals:
            return {"total": 0, "page": page, "limit": limit, "data": []}
        query["vertical"] = vertical
    else:
        # Scope to all allowed verticals
        query["vertical"] = {"$in": user_verticals}

    # View Logic (Supercedes Status if View is specific)
    # Status filter is applied ON TOP of View if provided (e.g. View=Ongoing + Status=Enquiry)
    
    base_status_filter = {}

    if view == "upcoming":
        base_status_filter = {"status": "booked"}
    elif view == "active":
        base_status_filter = {"status": {"$in": ["booked", "Booked", "ongoing", "Ongoing", "production", "Production"]}}
    elif view == "ongoing":
        base_status_filter = {"status": {"$in": ["ongoing", "Ongoing", "production", "Production"]}}
    elif view == "production":
        base_status_filter = {"status": {"$in": ["ongoing", "Ongoing", "production", "Production"]}}
    elif view == "enquiry":
        base_status_filter = {"status": {"$in": ["enquiry", "Enquiry"]}}
    elif view == "completed":
        base_status_filter = {"status": {"$in": ["completed", "Completed"]}}
    elif view == "cancelled":
        base_status_filter = {"status": {"$in": ["cancelled", "archived", "Cancelled", "Archived"]}}
    
    # If specific status is requested, it must be valid within the view
    if status and status != "all":
        # Check if view logic already set a constraint? 
        # Actually, for custom statuses, we need exact match usually,
        # but to support legacy "Ongoing" vs "ongoing", we use case-insensitive regex.
        # ESCAPE the status to prevent regex injection since IDs might have special chars (unlikely but safe).
        query["status"] = {"$regex": f"^{re.escape(status)}$", "$options": "i"}
        
        # Security check: if view='completed' but user requests status='production', returns empty
        if view == "completed" and status.lower() not in ["completed"]:
             query["status"] = "IMPOSSIBLE_MATCH"
        if view == "ongoing" and status.lower() not in ["enquiry", "production", "ongoing"]:
             # Allow 'ongoing' in ongoing view
             query["status"] = "IMPOSSIBLE_MATCH"

    elif base_status_filter:
        query.update(base_status_filter)

    if search:
        # Case-insensitive regex search on title, code, or client name
        regex_pattern = {"$regex": search, "$options": "i"}
        query["$or"] = [
            {"title": regex_pattern},
            {"code": regex_pattern},
            {"metadata.client_name": regex_pattern}
        ]

    # 2. Sorting & Pagination
    # Strategy: For 'upcoming', we need complex logic (finding min future date). 
    # For now, to ensure accuracy, we will Fetch Filtered -> Sort in Python -> Slice.
    # This is efficient enough for project lists < 1000 items.
    # For 'newest'/'oldest', we can use Mongo sort IF no complex search, 
    # but uniform path is often less buggy for mixed advanced sorts.
    
    # However, 'newest' and 'oldest' are heavily optimized in Mongo, so let's try to use them if possible.
    # But 'search' score or 'upcoming' logic complicates it.
    
    # HYBRID APPROACH:
    # If sort is 'upcoming' or view is 'production', fetch ALL matching query (no skip/limit yet), filter/sort in Py, then slice.
    # If sort is standard, use Mongo skip/limit.
    
    skip = (page - 1) * limit

    if view == "production":
        cursor = db.projects.find(query)
        all_projects = await cursor.to_list(length=1000)
        
        # We need to filter for projects that have at least one past event
        # AND that specific event has incomplete tasks.
        prod_projects = []
        now = datetime.now(timezone.utc)
        
        # Get all tasks for these projects at once to avoid N+1 queries
        project_ids = [str(p["_id"]) for p in all_projects]
        tasks_cursor = db.tasks.find({"project_id": {"$in": project_ids}})
        all_tasks = await tasks_cursor.to_list(length=None)
        
        # Group tasks by project_id and event_id
        tasks_by_proj_event = {}
        for t in all_tasks:
            pid = str(t.get("project_id"))
            eid = str(t.get("event_id"))
            if pid not in tasks_by_proj_event:
                tasks_by_proj_event[pid] = {}
            if eid not in tasks_by_proj_event[pid]:
                tasks_by_proj_event[pid][eid] = []
            tasks_by_proj_event[pid][eid].append(t)
            
        for p in all_projects:
            pid = str(p["_id"])
            is_production = False
            for e in p.get("events", []):
                event_date = parse_event_date(e.get("start_date"))
                if not event_date: continue

                if event_date.tzinfo is None:
                    event_date = event_date.replace(tzinfo=timezone.utc)
                if event_date < now:
                    # Check tasks for THIS specific event
                    eid = str(e.get("id"))
                    event_tasks = tasks_by_proj_event.get(pid, {}).get(eid, [])
                    if not event_tasks: continue # If no deliverables/tasks exist, it might not be production

                    completed = sum(1 for t in event_tasks if t.get("status") == "done")
                    total = len(event_tasks)
                    
                    if completed < total:
                        is_production = True
                        break # Found one qualifying event, so the project is in production
            
            if is_production:
                prod_projects.append(p)
                
        # Now sort the filtered projects
        def safe_sort_key(p):
            if sort == "upcoming":
                nows = datetime.now(timezone.utc)
                future_events = [parse_event_date(e.get("start_date")) for e in p.get("events", [])]
                return min(future_dates) if future_dates else datetime(9999, 12, 31, tzinfo=timezone.utc)
            
            val = p.get("created_on")
            if isinstance(val, datetime):
                if val.tzinfo is None:
                    return val.replace(tzinfo=timezone.utc)
                return val
            elif isinstance(val, str):
                try:
                    dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
                except ValueError:
                    return datetime.min.replace(tzinfo=timezone.utc)
            return datetime.min.replace(tzinfo=timezone.utc)

        if sort == "upcoming":
            prod_projects.sort(key=safe_sort_key)
        else:
            sort_order = -1 if sort == "newest" else 1
            prod_projects.sort(key=safe_sort_key, reverse=(sort_order == -1))
        
        total = len(prod_projects)
        paginated_data = prod_projects[skip : skip + limit]

    elif sort == "upcoming":
        # Fetch ALL matching basics
        cursor = db.projects.find(query)
        all_projects = await cursor.to_list(length=1000) # Safety cap
        
        def get_next_event_date(p):
            now = datetime.now(timezone.utc)
            future_events = [parse_event_date(e.get("start_date")) for e in p.get("events", [])]
            future_dates = [d for d in future_events if d and d > now]
            return min(future_dates) if future_dates else datetime(9999, 12, 31, tzinfo=timezone.utc)

        all_projects.sort(key=get_next_event_date)
        
        # Manually verify/debug:
        # print(f"DEBUG: Sorted {[p['code'] for p in all_projects]}")

        # Slice for pagination
        total = len(all_projects)
        paginated_data = all_projects[skip : skip + limit]
        
    else:
        # Standard Mongo Sort
        sort_order = -1 if sort == "newest" else 1 # 1 is oldest (ascending date)
        
        # Careful: sort argument must be valid.
        logger.debug(f"List projects query", extra={"data": {"query": str(query), "sort": sort, "skip": skip, "limit": limit}})

        cursor = db.projects.find(query).sort("created_on", sort_order).skip(skip).limit(limit)
        paginated_data = await cursor.to_list(length=limit)
        total = await db.projects.count_documents(query)
        
        logger.debug(f"List projects result", extra={"data": {"returned": len(paginated_data), "total": total}})
    
    # 3. Enrich with Task Stats (Progress)
    if paginated_data:
        # Extract IDs using explicit string conversion for safety
        project_ids = [str(p["_id"]) for p in paginated_data]
        
        # Aggregate stats for these projects only
        # Note: tasks collection usually uses 'studio_id', but db wrapper handles that if configured.
        # Check ScopedDatabase implementation: It maps 'studio_field_name' to 'studio_id' for tasks/task_history.
        # So calling db.tasks.aggregate automatically injects studio_id=agency_id.
        
        # However, for aggregation we need to be careful if we manually constructing pipeline.
        # ScopedCollection.aggregate injects a $match stage.
        # So we just provide the rest.
        
        stats_cursor = db.tasks.aggregate([
            {
                "$match": {
                    "project_id": {"$in": project_ids}
                    # studio_id injected by wrapper
                }
            },
            {
                "$group": {
                    "_id": "$project_id",
                    "total_tasks": {"$sum": 1},
                    "completed_tasks": {
                        "$sum": {"$cond": [{"$eq": ["$status", "done"]}, 1, 0]}
                    }
                }
            }
        ])
        
        stats_map = {doc["_id"]: doc for doc in await stats_cursor.to_list(length=len(project_ids))}
        
        # Merge stats into projects
        for project in paginated_data:
            pid = str(project["_id"])
            if pid in stats_map:
                s = stats_map[pid]
                task_total = s["total_tasks"]
                completed = s["completed_tasks"]
                percentage = int((completed / task_total) * 100) if task_total > 0 else 0
                project["stats"] = {
                    "total_tasks": task_total,
                    "completed_tasks": completed,
                    "percentage": percentage
                }
            else:
                project["stats"] = {"total_tasks": 0, "completed_tasks": 0, "percentage": 0}

    return {
        "total": total, 
        "page": page, 
        "limit": limit,
        "data": parse_mongo_data(paginated_data)
    }

@router.get("/{id}")
async def get_project(id: str, current_user: UserModel = Depends(get_current_user), db: ScopedDatabase = Depends(get_db)):
    """READ ONE: Fetch a single project by ID"""
    if not ObjectId.is_valid(id):
        raise HTTPException(status_code=400, detail="Invalid ID")
    
    # db.projects.find_one automatically injects {"agency_id": current_agency_id}
    project = await db.projects.find_one({"_id": ObjectId(id)})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # RBAC: Check vertical access
    user_verticals = await get_user_verticals(current_user, db)
    if project.get("vertical") and project["vertical"] not in user_verticals:
        raise HTTPException(status_code=404, detail="Project not found")  # 404 not 403 for seamless invisibility
    
    return parse_mongo_data(project)

@router.delete("/{id}")
async def delete_project(id: str, background_tasks: BackgroundTasks, current_user: UserModel = Depends(get_current_user), db: ScopedDatabase = Depends(get_db)):
    """DELETE: Remove an entire project"""
    if not ObjectId.is_valid(id):
        raise HTTPException(status_code=400, detail="Invalid ID")
    
    # RBAC: Check vertical access before deleting
    project = await db.projects.find_one({"_id": ObjectId(id)})
    if project:
        user_verticals = await get_user_verticals(current_user, db)
        if project.get("vertical") and project["vertical"] not in user_verticals:
            raise HTTPException(status_code=404, detail="Project not found")

        # Cascade Delete Tasks AFTER RBAC check (db.tasks uses studio_id automatically)
        await db.tasks.delete_many({"project_id": id})

        # Send calendar delete actions for any tracked events
        for event in project.get("events", []):
            if event.get("calendar_event_id"):
                await sync_event_to_calendar(
                    db=db,
                    project_id=id,
                    event_id=event.get("id"),
                    action="delete",
                    project_vertical=project.get("vertical"),
                    calendar_event_id=event.get("calendar_event_id")
                )

    result = await db.projects.delete_one({"_id": ObjectId(id)})
    
    if result.deleted_count == 0:
        logger.warning(f"Delete project failed: not found", extra={"data": {"project_id": id}})
        raise HTTPException(status_code=404, detail="Project not found")
    
    logger.info(f"Project deleted", extra={"data": {"project_id": id}})
    return {"message": "Project and associated tasks deleted successfully"}

@router.delete("/{project_id}/events/{event_id}")
async def delete_event(project_id: str, event_id: str, background_tasks: BackgroundTasks, current_user: UserModel = Depends(get_current_user), db: ScopedDatabase = Depends(get_db)):
    """DELETE: Remove an event from a project"""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")
    
    # Cascade Delete Tasks linked to this Event
    await db.tasks.delete_many({"event_id": event_id, "project_id": project_id})

    # Get the event first to check for calendar_event_id
    project_doc = await db.projects.find_one({"_id": ObjectId(project_id)})
    calendar_synced = False
    if project_doc:
        for evt in project_doc.get("events", []):
            if evt.get("id") == event_id and evt.get("calendar_event_id"):
                calendar_synced = await sync_event_to_calendar(
                    db=db,
                    project_id=project_id,
                    event_id=event_id,
                    action="delete",
                    project_vertical=project_doc.get("vertical"),
                    calendar_event_id=evt.get("calendar_event_id")
                )
                break

    result = await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {"$pull": {"events": {"id": event_id}}, "$set": {"updated_on": datetime.now(timezone.utc)}}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Project or Event not found")
    
    return {"message": "Event and associated tasks deleted successfully", "calendar_synced": bool(calendar_synced)}

@router.get("/stats/overview")
async def get_project_stats(vertical: str = None, current_user: UserModel = Depends(get_current_user), db: ScopedDatabase = Depends(get_db)):
    """READ STATS: Get overview metrics for the vertical/dashboard"""
    base_query = {}
    
    # RBAC: Scope to user's allowed verticals
    user_verticals = await get_user_verticals(current_user, db)
    if vertical:
        if vertical not in user_verticals:
            return {"total": 0, "active": 0, "ongoing": 0, "this_month": 0}
        base_query["vertical"] = vertical
    else:
        base_query["vertical"] = {"$in": user_verticals}

    # 1. Total Projects
    total = await db.projects.count_documents(base_query)

    # 2. Active Projects (Status != COMPLETED, ARCHIVED, CANCELLED)
    active_query = base_query.copy()
    active_query["status"] = {"$nin": ["Completed", "Archived", "Cancelled", "completed", "archived", "cancelled"]}
    active = await db.projects.count_documents(active_query)

    # 3. This Month (Active Projects having events in the current month)
    # MUST be a subset of 'active' to ensure logical numbers (This Month <= Active)
    now = datetime.now(timezone.utc)
    start_of_month = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    if now.month == 12:
        next_month = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_month = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    
    month_query = base_query.copy() # Revert to ALL projects (including Completed)
    # Check if ANY event in the 'events' array falls within the current month ranges.
    # Note: start_date in Mongo is stored as ISODate (datetime) or string depending on ingestion. 
    # Seed script uses datetime objects. Pydantic might serialize to string. 
    # We will try matching both or assume datetime if using Pymongo directly. 
    # Safe approach: usage of $elemMatch for the array.
    
    month_query["events"] = {
        "$elemMatch": {
            "start_date": {"$gte": start_of_month, "$lt": next_month}
        }
    }
    
    # Fallback: If dates are stored as Strings (ISO), we need string comparison.
    # But standard Pymongo + datetime inserts result in ISODate. 
    # Let's start with standard Date query.
    
    this_month = await db.projects.count_documents(month_query)

    # 4. Ongoing/Production (Specific Status)
    prod_query = base_query.copy()
    # Match both 'ongoing' and 'production' (case insensitive)
    prod_query["status"] = {"$in": ["ongoing", "Ongoing", "production", "Production"]}
    ongoing_count = await db.projects.count_documents(prod_query)

    return {
        "total": total,
        "active": active,
        "ongoing": ongoing_count,
        "this_month": this_month
    }

# --- ADVANCED LOGIC (The stuff you had) ---

@router.post("/{project_id}/events")
async def add_event_to_project(
    project_id: str, 
    background_tasks: BackgroundTasks,
    event: EventModel = Body(...), 
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """UPDATE: Add a new event (like a Reception) to an existing Project"""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    result = await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {"$push": {"events": event.model_dump()}, "$set": {"updated_on": datetime.now(timezone.utc)}}
    )

    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")

    # --- SYNC: Create Tasks for Deliverables ---
    # Deliverables should be treated as Tasks for progress tracking
    project_code = "UNKNOWN"
    
    # We need project details for the task
    project_doc = await db.projects.find_one({"_id": ObjectId(project_id)})
    if project_doc:
        project_code = project_doc.get("code", "PROJECT")

    # Sync to external calendar (Synchronous)
    calendar_synced = False
    sync_result = None
    if project_doc:
        project_name = await _resolve_project_name(project_doc, db)
        sync_result = await sync_event_to_calendar(
            db=db,
            project_id=project_id,
            event_id=event.id,
            action="create",
            project_vertical=project_doc.get("vertical"),
            title=f"{event.type} - {project_name}",
            start_time=event.start_date,
            end_time=event.end_date
        )
        
        if sync_result and isinstance(sync_result, str):
            calendar_synced = True
            # Update the event with the returned calendar_event_id
            await db.projects.update_one(
                {"_id": ObjectId(project_id), "events.id": event.id},
                {"$set": {"events.$.calendar_event_id": sync_result}}
            )
        elif sync_result:
             calendar_synced = True

    # Handle Team Assignments (Notifications and Calendar Sync)
    if event.assignments:
        for assignment in event.assignments:
            # Note: We send standard assignment notifications
            await notify_associate_assignment(
                db,
                background_tasks,
                assignment.associate_id,
                project_code,
                event.type,
                event.start_date,
                current_user.agency_id
            )
            
            # If the event was synced and we have a valid ID, add attendee to the calendar event
            if sync_result and isinstance(sync_result, str):
                 if ObjectId.is_valid(assignment.associate_id):
                     associate = await db.associates.find_one({"_id": ObjectId(assignment.associate_id)})
                     if associate:
                          email = associate.get("email_id") or associate.get("email")
                          if email:
                              background_tasks.add_task(
                                  sync_attendee_to_calendar,
                                  db=db,
                                  calendar_event_id=sync_result,
                                  email=email,
                                  action="add_attendee"
                              )

    new_tasks = []
    if event.deliverables:
        for deliverable in event.deliverables:
            # Create a Task for this deliverable
            task = TaskModel(
                title=f"{deliverable.type} ({event.type})",
                description=f"Deliverable for {event.type}",
                project_id=project_id,
                event_id=event.id,
                # Store link to deliverable if needed in metadata
                status="todo", # Default
                priority="medium",
                due_date=deliverable.due_date,
                assigned_to=deliverable.incharge_id,
                studio_id=current_user.agency_id, # ScopedDB enforces this anyway
                created_by=current_user.id,
                type="project",
                category="deliverable"
            )
            new_tasks.append(task.model_dump())
            
        if new_tasks:
            await db.tasks.insert_many(new_tasks)
            logger.info(f"Created tasks from deliverables", extra={"data": {"count": len(new_tasks), "event_type": event.type, "project_id": project_id}})

    return {"message": "Event added successfully", "calendar_synced": calendar_synced}

@router.patch("/{project_id}")
async def update_project(
    project_id: str, 
    background_tasks: BackgroundTasks,
    update_data: dict = Body(...), 
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """UPDATE: Generic update for project fields"""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    old_project = await db.projects.find_one({"_id": ObjectId(project_id)})
    if not old_project:
        raise HTTPException(status_code=404, detail="Project not found")
        
    old_name = await _resolve_project_name(old_project, db)

    # Prevent updating immutable fields or fields handled by specific logic
    update_data.pop("_id", None)
    update_data.pop("events", None) 
    update_data.pop("assignments", None)  # Handled by dedicated endpoints
    update_data.pop("code", None) 
    update_data.pop("agency_id", None) 
    update_data["updated_on"] = datetime.now(timezone.utc)

    result = await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {"$set": update_data}
    )

    if result.matched_count == 0:
        logger.warning(f"Update project failed: not found", extra={"data": {"project_id": project_id}})
        raise HTTPException(status_code=404, detail="Project not found")
        
    # Check if project name changed and sync calendars
    updated_project = await db.projects.find_one({"_id": ObjectId(project_id)})
    if updated_project:
        new_name = await _resolve_project_name(updated_project, db)
        if old_name != new_name:
            for evt in updated_project.get("events", []):
                if evt.get("calendar_event_id"):
                    background_tasks.add_task(
                        sync_event_to_calendar,
                        db=db,
                        project_id=project_id,
                        event_id=evt.get("id"),
                        action="update",
                        project_vertical=updated_project.get("vertical"),
                        title=f"{evt.get('type', 'Event')} - {new_name}",
                        start_time=evt.get("start_date"),
                        end_time=evt.get("end_date"),
                        calendar_event_id=evt.get("calendar_event_id")
                    )

    logger.info(f"Project updated", extra={"data": {"project_id": project_id, "fields": list(update_data.keys())}})
    return {"message": "Project updated successfully"}

@router.patch("/{project_id}/events/{event_id}")
async def update_event(
    project_id: str, 
    event_id: str, 
    background_tasks: BackgroundTasks,
    update_data: dict = Body(...), 
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """UPDATE: Update a specific event within a project"""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")
        
    old_project = await db.projects.find_one({"_id": ObjectId(project_id)})
    if not old_project:
        raise HTTPException(status_code=404, detail="Project not found")
        
    old_event = next((e for e in old_project.get("events", []) if e.get("id") == event_id), None)
    if not old_event:
        raise HTTPException(status_code=404, detail="Event not found")
        
    needs_calendar_sync = False
    if not old_event.get("calendar_event_id"):
        needs_calendar_sync = True
    else:
        for field in ["type", "start_date", "end_date"]:
            if field in update_data:
                old_val = str(old_event.get(field, ""))
                new_val = str(update_data.get(field, ""))
                if old_val != new_val:
                    needs_calendar_sync = True
                    break

    # If deliverables are being updated, sync to Tasks using deliverable_id FK
    if "deliverables" in update_data:
        existing_tasks = await db.tasks.find({"project_id": project_id, "event_id": event_id, "category": "deliverable"}).to_list(length=None)
        existing_task_by_deliv_id = {t.get("deliverable_id"): t for t in existing_tasks if t.get("deliverable_id")}
        all_new_tasks = []
        event_type = old_event.get("type", "Event")

        for deliverable in update_data["deliverables"]:
            deliv_id = deliverable.get("id")
            existing_task = existing_task_by_deliv_id.get(deliv_id)

            if not existing_task:
                # New deliverable — create task
                task = TaskModel(
                    title=f"{deliverable.get('type', 'Deliverable')} ({event_type})",
                    description=f"Deliverable for {event_type}",
                    project_id=project_id,
                    event_id=event_id,
                    deliverable_id=deliv_id,
                    priority="medium",
                    due_date=deliverable.get('due_date'),
                    assigned_to=deliverable.get('incharge_id'),
                    studio_id=current_user.agency_id,
                    created_by=current_user.id,
                    type="project",
                    category="deliverable",
                    quantity=deliverable.get("quantity", 1),
                )
                all_new_tasks.append(task.model_dump())
            else:
                # Existing deliverable — update task fields
                old_qty = existing_task.get("quantity", 1)
                new_qty = deliverable.get("quantity", 1)
                await db.tasks.update_one(
                    {"id": existing_task["id"]},
                    {"$set": {
                        "due_date": deliverable.get('due_date'),
                        "quantity": new_qty,
                        "title": f"{deliverable.get('type', 'Deliverable')} ({event_type})",
                        "updated_at": datetime.now(timezone.utc),
                    }}
                )
                # Sync portal deliverables if quantity changed
                if old_qty != new_qty:
                    updated_task = await db.tasks.find_one({"id": existing_task["id"]})
                    await on_task_quantity_changed(db, updated_task, old_qty, new_qty, project_id)

        if all_new_tasks:
            await db.tasks.insert_many(all_new_tasks)
            for task_dict in all_new_tasks:
                await on_deliverable_task_created(db, task_dict, project_id)
            logger.info(f"Created synced tasks from deliverables", extra={"data": {"count": len(all_new_tasks), "project_id": project_id}})

    # Prefix keys with "events.$." to update the matched array element
    set_fields = {f"events.$.{k}": v for k, v in update_data.items()}
    set_fields["updated_on"] = datetime.now(timezone.utc)

    result = await db.projects.update_one(
        {"_id": ObjectId(project_id), "events.id": event_id},
        {"$set": set_fields}
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project or Event not found")

    # Sync to external calendar
    if needs_calendar_sync:
        updated_project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if updated_project:
            project_name = await _resolve_project_name(updated_project, db)
            for evt in updated_project.get("events", []):
                if evt.get("id") == event_id:
                    calendar_synced = await sync_event_to_calendar(
                        db=db,
                        project_id=project_id,
                        event_id=event_id,
                        action="update",
                        project_vertical=updated_project.get("vertical"),
                        title=f"{evt.get('type')} - {project_name}",
                        start_time=evt.get("start_date"),
                        end_time=evt.get("end_date"),
                        calendar_event_id=evt.get("calendar_event_id")
                    )
                    return {"message": "Event updated successfully", "calendar_synced": calendar_synced}

    return {"message": "Event updated successfully", "calendar_synced": False}

# Legacy Deliverable Endpoints Removed - Now handled via /api/tasks

@router.post("/{project_id}/events/{event_id}/assignments")
async def add_assignment(
    project_id: str, 
    event_id: str, 
    background_tasks: BackgroundTasks,
    assignment: AssignmentModel = Body(...), 
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """CREATE: Add an associate assignment to a specific event"""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    # Get project info for notification
    project = await db.projects.find_one({"_id": ObjectId(project_id)})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Find the event to get its type and date
    event_type = "Event"
    event_date = None
    for evt in project.get("events", []):
        if evt.get("id") == event_id:
            event_type = evt.get("type", "Event")
            event_date = evt.get("start_date")
            # Check for duplicate assignment
            existing_assignments = [str(a.get("associate_id")) for a in evt.get("assignments", [])]
            if str(assignment.associate_id) in existing_assignments:
                raise HTTPException(status_code=400, detail="Associate is already assigned to this event")
            break

    result = await db.projects.update_one(
        {"_id": ObjectId(project_id), "events.id": event_id},
        {"$push": {"events.$.assignments": assignment.model_dump()}, "$set": {"updated_on": datetime.now(timezone.utc)}}
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project or Event not found")

    # Send notification to the assigned associate
    await notify_associate_assignment(
        db, # Pass db dependency
        background_tasks,
        assignment.associate_id, 
        project.get("code", "Unknown"), 
        event_type,
        event_date,
        current_user.agency_id
    )

    # Sync Attendee to Calendar 
    if project:
        target_event = next((evt for evt in project.get("events", []) if evt.get("id") == event_id), None)
        if target_event and target_event.get("calendar_event_id"):
            if ObjectId.is_valid(assignment.associate_id):
                associate = await db.associates.find_one({"_id": ObjectId(assignment.associate_id)})
                if associate:
                    email = associate.get("email_id") or associate.get("email")
                    if email:
                        background_tasks.add_task(
                            sync_attendee_to_calendar,
                            db=db,
                            calendar_event_id=target_event.get("calendar_event_id"),
                            email=email,
                            action="add_attendee"
                        )

    return {"message": "Assignment added successfully", "id": assignment.id}

@router.patch("/{project_id}/events/{event_id}/assignments/{assignment_id}")
async def update_assignment(
    project_id: str, 
    event_id: str, 
    assignment_id: str, 
    update_data: dict = Body(...), 
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """UPDATE: deep nested update for an assignment"""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    # Prefix keys for arrayFilters
    set_fields = {f"events.$[evt].assignments.$[asn].{k}": v for k, v in update_data.items()}
    set_fields["updated_on"] = datetime.now(timezone.utc)

    result = await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {"$set": set_fields},
        array_filters=[{"evt.id": event_id}, {"asn.id": assignment_id}]
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")
    
    return {"message": "Assignment updated"}

@router.delete("/{project_id}/events/{event_id}/assignments/{assignment_id}")
async def delete_assignment(
    project_id: str, 
    event_id: str, 
    assignment_id: str, 
    background_tasks: BackgroundTasks,
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """DELETE: Remove an assignment"""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    # Sync Attendee Removal from Calendar before we delete the assignment from DB
    project_doc = await db.projects.find_one({"_id": ObjectId(project_id)})
    if project_doc:
        target_event = next((evt for evt in project_doc.get("events", []) if evt.get("id") == event_id), None)
        if target_event and target_event.get("calendar_event_id"):
            # Find the associate_id from the assignment we are deleting
            target_assignment = next((a for a in target_event.get("assignments", []) if a.get("id") == assignment_id), None)
            if target_assignment and target_assignment.get("associate_id") and ObjectId.is_valid(target_assignment.get("associate_id")):
                associate = await db.associates.find_one({"_id": ObjectId(target_assignment.get("associate_id"))})
                if associate:
                    email = associate.get("email_id") or associate.get("email")
                    if email:
                         background_tasks.add_task(
                             sync_attendee_to_calendar,
                             db=db,
                             calendar_event_id=target_event.get("calendar_event_id"),
                             email=email,
                             action="remove_attendee"
                         )

    result = await db.projects.update_one(
        {"_id": ObjectId(project_id), "events.id": event_id},
        {"$pull": {"events.$.assignments": {"id": assignment_id}}, "$set": {"updated_on": datetime.now(timezone.utc)}}
    )

    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Item not found or already deleted")

    return {"message": "Assignment deleted"}

# --- PROJECT-LEVEL ASSIGNMENTS (for non-event verticals) ---

@router.post("/{project_id}/assignments")
async def add_project_assignment(
    project_id: str,
    background_tasks: BackgroundTasks,
    assignment: AssignmentModel = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """CREATE: Add a team member assignment directly to a project (no event)"""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    project = await db.projects.find_one({"_id": ObjectId(project_id)})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    result = await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {"$push": {"assignments": assignment.model_dump()}, "$set": {"updated_on": datetime.now(timezone.utc)}}
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")

    # Send notification
    await notify_associate_assignment(
        db, background_tasks,
        assignment.associate_id,
        project.get("code", "Unknown"),
        "Project Assignment",
        datetime.now(timezone.utc),
        current_user.agency_id
    )

    return {"message": "Assignment added successfully", "id": assignment.id}

@router.patch("/{project_id}/assignments/{assignment_id}")
async def update_project_assignment(
    project_id: str,
    assignment_id: str,
    update_data: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """UPDATE: Update a project-level assignment"""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    set_fields = {f"assignments.$[asn].{k}": v for k, v in update_data.items()}
    set_fields["updated_on"] = datetime.now(timezone.utc)

    result = await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {"$set": set_fields},
        array_filters=[{"asn.id": assignment_id}]
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")

    return {"message": "Assignment updated"}

@router.delete("/{project_id}/assignments/{assignment_id}")
async def delete_project_assignment(
    project_id: str,
    assignment_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """DELETE: Remove a project-level assignment"""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    result = await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {"$pull": {"assignments": {"id": assignment_id}}, "$set": {"updated_on": datetime.now(timezone.utc)}}
    )

    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Item not found or already deleted")

    return {"message": "Assignment deleted"}

@router.get("/assigned/{associate_id}")
async def get_associate_schedule(associate_id: str, current_user: UserModel = Depends(get_current_user), db: ScopedDatabase = Depends(get_db)):
    """SEARCH: Find all projects where this associate is working"""
    query = {"$or": [
        {"events.assignments.associate_id": associate_id},
        {"assignments.associate_id": associate_id}
    ]}
    projects = await db.projects.find(query).to_list(1000)
    return parse_mongo_data(projects)


# ──────────────────────────────────────────────────────────────────────
# Portal Deliverables (Internal, Auth Required)
# ──────────────────────────────────────────────────────────────────────

@router.post("/{project_id}/portal-token")
async def generate_portal_token(
    project_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Lazy-generate portal_token for existing projects that don't have one."""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    project = await db.projects.find_one({"_id": ObjectId(project_id)})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.get("portal_token"):
        return {"portal_token": project["portal_token"]}

    token = secrets.token_urlsafe(32)
    await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {"$set": {"portal_token": token, "updated_on": datetime.now(timezone.utc)}}
    )
    return {"portal_token": token}


@router.post("/{project_id}/deliverables/upload-url")
async def get_upload_url(
    project_id: str,
    body: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Returns a presigned R2 PUT URL for direct browser upload."""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    file_name = body.get("file_name")
    content_type = body.get("content_type", "application/octet-stream")
    if not file_name:
        raise HTTPException(status_code=400, detail="file_name is required")

    import uuid
    r2_key = f"deliverables/{current_user.agency_id}/{project_id}/{uuid.uuid4()}_{file_name}"
    public_url = f"{config.R2_PUBLIC_URL}/{r2_key}" if config.R2_PUBLIC_URL else ""

    presigned_url = generate_presigned_put_url(r2_key, content_type)

    return {
        "upload_url": presigned_url,
        "r2_key": r2_key,
        "r2_url": public_url,
    }


@router.post("/{project_id}/sync-deliverables")
async def sync_deliverables_from_events(
    project_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Reconcile portal deliverables with deliverable tasks (ID-based)."""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    project = await db.projects.find_one({"_id": ObjectId(project_id)})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    result = await reconcile_project(db, project_id)
    return result


@router.post("/{project_id}/deliverables")
async def create_deliverable(
    project_id: str,
    body: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Create a new portal deliverable for a project."""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    deliverable = PortalDeliverableModel(
        title=body.get("title", "Untitled"),
        description=body.get("description", ""),
        event_id=body.get("event_id"),
    )

    result = await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {
            "$push": {"portal_deliverables": deliverable.model_dump()},
            "$set": {"updated_on": datetime.now(timezone.utc)},
        }
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")

    return deliverable.model_dump()


@router.get("/{project_id}/deliverables")
async def list_deliverables(
    project_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """List all portal deliverables for a project."""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    project = await db.projects.find_one(
        {"_id": ObjectId(project_id)},
        {"portal_deliverables": 1, "portal_token": 1, "events": 1}
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    events_summary = [
        {"id": e.get("id"), "type": e.get("type")}
        for e in project.get("events", [])
    ]

    return {
        "deliverables": project.get("portal_deliverables", []),
        "portal_token": project.get("portal_token"),
        "events": events_summary,
    }


@router.patch("/{project_id}/deliverables/{deliverable_id}")
async def update_deliverable(
    project_id: str,
    deliverable_id: str,
    body: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Update a portal deliverable (title, description, status)."""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    set_fields = {}
    for field in ["title", "description", "status", "event_id"]:
        if field in body:
            set_fields[f"portal_deliverables.$[d].{field}"] = body[field]
    set_fields["portal_deliverables.$[d].updated_on"] = datetime.now(timezone.utc)
    set_fields["updated_on"] = datetime.now(timezone.utc)

    result = await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {"$set": set_fields},
        array_filters=[{"d.id": deliverable_id}]
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")

    return {"message": "Deliverable updated"}


@router.delete("/{project_id}/deliverables/{deliverable_id}")
async def delete_deliverable(
    project_id: str,
    deliverable_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Remove a deliverable and delete all associated R2 objects."""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    # Fetch files to delete from R2
    project = await db.projects.find_one(
        {"_id": ObjectId(project_id)},
        {"portal_deliverables": 1}
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    deliverable = next(
        (d for d in project.get("portal_deliverables", []) if d["id"] == deliverable_id),
        None
    )
    if deliverable:
        for f in deliverable.get("files", []):
            delete_r2_object(f["r2_key"])

    result = await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {
            "$pull": {"portal_deliverables": {"id": deliverable_id}},
            "$set": {"updated_on": datetime.now(timezone.utc)},
        }
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Deliverable not found")

    return {"message": "Deliverable deleted"}


@router.post("/{project_id}/deliverables/{deliverable_id}/files")
async def add_file_to_deliverable(
    project_id: str,
    deliverable_id: str,
    background_tasks: BackgroundTasks,
    body: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Add a file to an existing deliverable (called after presigned upload completes)."""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    content_type = body.get("content_type", "application/octet-stream")
    is_image = content_type.startswith("image/")
    is_video = content_type.startswith("video/")

    file_entry = DeliverableFile(
        file_name=body["file_name"],
        content_type=content_type,
        r2_key=body["r2_key"],
        r2_url=body["r2_url"],
        thumbnail_status="pending" if (is_image or is_video) else "n/a",
        watermark_status="pending" if is_video else "n/a",
    )

    now = datetime.now(timezone.utc)
    result = await db.projects.update_one(
        {"_id": ObjectId(project_id), "portal_deliverables.id": deliverable_id},
        {
            "$push": {"portal_deliverables.$.files": file_entry.model_dump()},
            "$set": {
                "portal_deliverables.$.updated_on": now,
                "updated_on": now,
            },
        }
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project or deliverable not found")

    # Auto-transition Pending -> Uploaded via sync service
    await on_portal_file_added(db, project_id, deliverable_id)

    # Queue thumbnail generation for image/video files
    if is_image or is_video:
        # Fetch agency_id for R2 path
        project = await db.projects.find_one({"_id": ObjectId(project_id)}, {"agency_id": 1, "portal_watermark_enabled": 1, "portal_watermark_text": 1})
        agency_id = project.get("agency_id", "default") if project else "default"
        from services.media_processing import process_thumbnail, process_watermark
        background_tasks.add_task(process_thumbnail, project_id, deliverable_id, file_entry.id, body["r2_key"], content_type, agency_id)

        # Queue watermark for videos if watermark is enabled on the project
        if is_video and project and project.get("portal_watermark_enabled"):
            watermark_text = project.get("portal_watermark_text") or "Protected"
            background_tasks.add_task(process_watermark, project_id, deliverable_id, file_entry.id, body["r2_key"], watermark_text, agency_id)

    return file_entry.model_dump()


@router.delete("/{project_id}/deliverables/{deliverable_id}/files/{file_id}")
async def delete_file_from_deliverable(
    project_id: str,
    deliverable_id: str,
    file_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Remove a single file from a deliverable and delete from R2."""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    # Find the file to get the r2_key
    project = await db.projects.find_one(
        {"_id": ObjectId(project_id)},
        {"portal_deliverables": 1}
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    deliverable = next(
        (d for d in project.get("portal_deliverables", []) if d["id"] == deliverable_id),
        None
    )
    if deliverable:
        file_entry = next((f for f in deliverable.get("files", []) if f["id"] == file_id), None)
        if file_entry:
            delete_r2_object(file_entry["r2_key"])
            if file_entry.get("thumbnail_r2_key"):
                delete_r2_object(file_entry["thumbnail_r2_key"])
            if file_entry.get("watermark_r2_key"):
                delete_r2_object(file_entry["watermark_r2_key"])

    remaining_files = len([f for f in deliverable.get("files", []) if f["id"] != file_id]) if deliverable else 0
    now = datetime.now(timezone.utc)

    result = await db.projects.update_one(
        {"_id": ObjectId(project_id), "portal_deliverables.id": deliverable_id},
        {
            "$pull": {"portal_deliverables.$.files": {"id": file_id}},
            "$set": {
                "portal_deliverables.$.updated_on": now,
                "updated_on": now,
            },
        }
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="File not found")

    # Auto-transition Uploaded -> Pending if last file removed
    await on_portal_file_removed(db, project_id, deliverable_id, remaining_files)

    return {"message": "File deleted"}


@router.post("/{project_id}/deliverables/{deliverable_id}/feedback")
async def add_team_feedback(
    project_id: str,
    deliverable_id: str,
    body: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Add team feedback to a deliverable."""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    feedback = FeedbackEntry(
        message=body["message"],
        author_type="team",
        author_name=current_user.name,
        file_id=body.get("file_id"),
    )

    result = await db.projects.update_one(
        {"_id": ObjectId(project_id), "portal_deliverables.id": deliverable_id},
        {
            "$push": {"portal_deliverables.$.feedback": feedback.model_dump()},
            "$set": {
                "portal_deliverables.$.updated_on": datetime.now(timezone.utc),
                "updated_on": datetime.now(timezone.utc),
            },
        }
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project or deliverable not found")

    return feedback.model_dump()


# --- Portal Settings ---

@router.patch("/{project_id}/portal-settings")
async def update_portal_settings(
    project_id: str,
    background_tasks: BackgroundTasks,
    body: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Update portal settings (watermark, default download limit)."""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    project = await db.projects.find_one({"_id": ObjectId(project_id)})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    updates = {"updated_on": datetime.now(timezone.utc)}
    was_watermark_enabled = project.get("portal_watermark_enabled", False)
    new_watermark_enabled = body.get("portal_watermark_enabled")

    if "portal_watermark_text" in body:
        updates["portal_watermark_text"] = body["portal_watermark_text"]
    if "portal_default_download_limit" in body:
        updates["portal_default_download_limit"] = body["portal_default_download_limit"]
    if new_watermark_enabled is not None:
        updates["portal_watermark_enabled"] = new_watermark_enabled

    await db.projects.update_one({"_id": ObjectId(project_id)}, {"$set": updates})

    # Handle watermark toggle side effects
    if new_watermark_enabled is not None and new_watermark_enabled != was_watermark_enabled:
        portal_deliverables = project.get("portal_deliverables", [])
        now = datetime.now(timezone.utc)

        if new_watermark_enabled:
            # Toggling ON: disable downloads on video deliverables, queue watermark processing
            watermark_text = body.get("portal_watermark_text") or project.get("portal_watermark_text") or "Protected"
            agency_id = project.get("agency_id", "default")

            for pd in portal_deliverables:
                has_video = any(f.get("content_type", "").startswith("video/") for f in pd.get("files", []))
                if has_video:
                    await db.projects.update_one(
                        {"_id": ObjectId(project_id), "portal_deliverables.id": pd["id"]},
                        {"$set": {
                            "portal_deliverables.$.downloads_disabled": True,
                            "portal_deliverables.$.updated_on": now,
                        }}
                    )
                    # Queue watermark for unwatermarked video files
                    from services.media_processing import process_watermark
                    for f in pd.get("files", []):
                        if f.get("content_type", "").startswith("video/") and f.get("watermark_status") in ("pending", "failed", None):
                            background_tasks.add_task(
                                process_watermark, project_id, pd["id"], f["id"],
                                f["r2_key"], watermark_text, agency_id
                            )
        else:
            # Toggling OFF: re-enable downloads on deliverables that were auto-disabled
            for pd in portal_deliverables:
                if pd.get("downloads_disabled"):
                    has_video = any(f.get("content_type", "").startswith("video/") for f in pd.get("files", []))
                    if has_video:
                        await db.projects.update_one(
                            {"_id": ObjectId(project_id), "portal_deliverables.id": pd["id"]},
                            {"$set": {
                                "portal_deliverables.$.downloads_disabled": False,
                                "portal_deliverables.$.updated_on": now,
                            }}
                        )

    return {"message": "Portal settings updated"}


# --- Download Limits ---

@router.patch("/{project_id}/deliverables/{deliverable_id}/download-settings")
async def update_download_settings(
    project_id: str,
    deliverable_id: str,
    body: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Set download limit and/or disable downloads for a deliverable."""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    set_fields = {"portal_deliverables.$.updated_on": datetime.now(timezone.utc), "updated_on": datetime.now(timezone.utc)}
    if "max_downloads" in body:
        set_fields["portal_deliverables.$.max_downloads"] = body["max_downloads"]
    if "downloads_disabled" in body:
        set_fields["portal_deliverables.$.downloads_disabled"] = body["downloads_disabled"]

    result = await db.projects.update_one(
        {"_id": ObjectId(project_id), "portal_deliverables.id": deliverable_id},
        {"$set": set_fields}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project or deliverable not found")

    return {"message": "Download settings updated"}


@router.post("/{project_id}/deliverables/{deliverable_id}/reset-downloads")
async def reset_download_count(
    project_id: str,
    deliverable_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Reset download count to 0 for a deliverable."""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    result = await db.projects.update_one(
        {"_id": ObjectId(project_id), "portal_deliverables.id": deliverable_id},
        {"$set": {
            "portal_deliverables.$.download_count": 0,
            "portal_deliverables.$.updated_on": datetime.now(timezone.utc),
            "updated_on": datetime.now(timezone.utc),
        }}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project or deliverable not found")

    return {"message": "Download count reset"}


# --- File Versioning ---

@router.post("/{project_id}/deliverables/{deliverable_id}/files/{file_id}/replace")
async def replace_file(
    project_id: str,
    deliverable_id: str,
    file_id: str,
    background_tasks: BackgroundTasks,
    body: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Replace a file with a new version. Old file is deleted from R2, metadata saved to history."""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    project = await db.projects.find_one(
        {"_id": ObjectId(project_id)},
        {"portal_deliverables": 1, "agency_id": 1, "portal_watermark_enabled": 1, "portal_watermark_text": 1}
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    deliverable = next(
        (d for d in project.get("portal_deliverables", []) if d["id"] == deliverable_id),
        None
    )
    if not deliverable:
        raise HTTPException(status_code=404, detail="Deliverable not found")

    file_entry = next((f for f in deliverable.get("files", []) if f["id"] == file_id), None)
    if not file_entry:
        raise HTTPException(status_code=404, detail="File not found")

    from models.project import FileVersion

    # Save current file metadata to version history
    version_entry = FileVersion(
        version=file_entry.get("version", 1),
        file_name=file_entry["file_name"],
        content_type=file_entry["content_type"],
        uploaded_by=file_entry.get("uploaded_by"),
        uploaded_on=file_entry.get("uploaded_on", datetime.now(timezone.utc)),
        change_notes=body.get("change_notes", ""),
    )

    # Delete old file, thumbnail, and watermark from R2
    delete_r2_object(file_entry["r2_key"])
    if file_entry.get("thumbnail_r2_key"):
        delete_r2_object(file_entry["thumbnail_r2_key"])
    if file_entry.get("watermark_r2_key"):
        delete_r2_object(file_entry["watermark_r2_key"])

    new_version = file_entry.get("version", 1) + 1
    content_type = body.get("content_type", "application/octet-stream")
    is_image = content_type.startswith("image/")
    is_video = content_type.startswith("video/")
    now = datetime.now(timezone.utc)

    # Update file with new data using array filters
    update_fields = {
        "portal_deliverables.$[d].files.$[f].file_name": body["file_name"],
        "portal_deliverables.$[d].files.$[f].content_type": content_type,
        "portal_deliverables.$[d].files.$[f].r2_key": body["r2_key"],
        "portal_deliverables.$[d].files.$[f].r2_url": body["r2_url"],
        "portal_deliverables.$[d].files.$[f].uploaded_on": now,
        "portal_deliverables.$[d].files.$[f].version": new_version,
        "portal_deliverables.$[d].files.$[f].thumbnail_r2_key": None,
        "portal_deliverables.$[d].files.$[f].thumbnail_r2_url": None,
        "portal_deliverables.$[d].files.$[f].thumbnail_status": "pending" if (is_image or is_video) else "n/a",
        "portal_deliverables.$[d].files.$[f].watermark_r2_key": None,
        "portal_deliverables.$[d].files.$[f].watermark_r2_url": None,
        "portal_deliverables.$[d].files.$[f].watermark_status": "pending" if is_video else "n/a",
        "updated_on": now,
    }

    await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {
            "$set": update_fields,
            "$push": {"portal_deliverables.$[d].files.$[f].previous_versions": version_entry.model_dump()},
        },
        array_filters=[{"d.id": deliverable_id}, {"f.id": file_id}],
    )

    # Optionally reset download count
    if body.get("reset_downloads"):
        await db.projects.update_one(
            {"_id": ObjectId(project_id), "portal_deliverables.id": deliverable_id},
            {"$set": {"portal_deliverables.$.download_count": 0}}
        )

    # Queue thumbnail/watermark processing
    agency_id = project.get("agency_id", "default")
    if is_image or is_video:
        from services.media_processing import process_thumbnail, process_watermark
        background_tasks.add_task(process_thumbnail, project_id, deliverable_id, file_id, body["r2_key"], content_type, agency_id)
        if is_video and project.get("portal_watermark_enabled"):
            watermark_text = project.get("portal_watermark_text") or "Protected"
            background_tasks.add_task(process_watermark, project_id, deliverable_id, file_id, body["r2_key"], watermark_text, agency_id)

    return {"message": "File replaced", "version": new_version}


@router.get("/{project_id}/deliverables/{deliverable_id}/files/{file_id}/versions")
async def get_file_versions(
    project_id: str,
    deliverable_id: str,
    file_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Get version history for a file (metadata only, no download links)."""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    project = await db.projects.find_one(
        {"_id": ObjectId(project_id)},
        {"portal_deliverables": 1}
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    deliverable = next(
        (d for d in project.get("portal_deliverables", []) if d["id"] == deliverable_id),
        None
    )
    if not deliverable:
        raise HTTPException(status_code=404, detail="Deliverable not found")

    file_entry = next((f for f in deliverable.get("files", []) if f["id"] == file_id), None)
    if not file_entry:
        raise HTTPException(status_code=404, detail="File not found")

    return {
        "current_version": file_entry.get("version", 1),
        "previous_versions": file_entry.get("previous_versions", []),
    }


@router.post("/{project_id}/deliverables/{deliverable_id}/files/{file_id}/watermark")
async def toggle_file_watermark(
    project_id: str,
    deliverable_id: str,
    file_id: str,
    body: dict = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Enable or disable visual watermark overlay on a specific video file."""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    enabled = body.get("enabled", True)
    watermark_text = body.get("watermark_text")

    project = await db.projects.find_one(
        {"_id": ObjectId(project_id)},
        {"portal_deliverables": 1}
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    deliverable = next(
        (d for d in project.get("portal_deliverables", []) if d["id"] == deliverable_id), None
    )
    if not deliverable:
        raise HTTPException(status_code=404, detail="Deliverable not found")

    file_entry = next((f for f in deliverable.get("files", []) if f["id"] == file_id), None)
    if not file_entry:
        raise HTTPException(status_code=404, detail="File not found")

    new_status = "done" if enabled else "n/a"
    update_fields = {"portal_deliverables.$[d].files.$[f].watermark_status": new_status}

    # Persist watermark text at project level when enabling
    if enabled and watermark_text:
        await db.projects.update_one(
            {"_id": ObjectId(project_id)},
            {"$set": {"portal_watermark_text": watermark_text.strip()}},
        )

    await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {"$set": update_fields},
        array_filters=[{"d.id": deliverable_id}, {"f.id": file_id}],
    )
    return {"message": "Watermark enabled" if enabled else "Watermark removed"}


# --- Portal Analytics ---

@router.get("/{project_id}/portal-analytics")
async def get_portal_analytics(
    project_id: str,
    days: int = Query(default=30, ge=1, le=365),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Get portal analytics for a project."""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    from database import portal_analytics_collection
    from datetime import timedelta

    since = datetime.now(timezone.utc) - timedelta(days=days)

    cursor = portal_analytics_collection.find({
        "project_id": project_id,
        "timestamp": {"$gte": since},
    }).sort("timestamp", -1)

    events = await cursor.to_list(length=10000)

    total_visits = sum(1 for e in events if e.get("event_type") == "visit")
    unique_ips = len(set(e.get("ip_address") for e in events if e.get("event_type") == "visit" and e.get("ip_address")))
    total_downloads = sum(1 for e in events if e.get("event_type") == "file_download")

    # Build deliverable id -> title lookup from project
    project = await db.projects.find_one(
        {"_id": ObjectId(project_id)},
        {"portal_deliverables": 1}
    )
    deliverable_title_map = {
        d["id"]: d.get("title", d["id"])
        for d in (project or {}).get("portal_deliverables", [])
    }

    # Per-deliverable download counts keyed by title
    deliverable_downloads = {}
    for e in events:
        if e.get("event_type") == "file_download" and e.get("deliverable_id"):
            did = e["deliverable_id"]
            label = deliverable_title_map.get(did, did)
            deliverable_downloads[label] = deliverable_downloads.get(label, 0) + 1

    # Recent activity (last 10)
    recent = []
    for e in events[:10]:
        recent.append({
            "event_type": e.get("event_type"),
            "deliverable_id": e.get("deliverable_id"),
            "deliverable_title": deliverable_title_map.get(e.get("deliverable_id"), e.get("deliverable_id")),
            "file_name": e.get("file_name"),
            "timestamp": e.get("timestamp"),
            "ip_address": e.get("ip_address"),
        })

    # Timeline: group by date
    timeline = {}
    for e in events:
        date_str = e.get("timestamp").strftime("%Y-%m-%d") if e.get("timestamp") else "unknown"
        if date_str not in timeline:
            timeline[date_str] = {"date": date_str, "visits": 0, "downloads": 0}
        if e.get("event_type") == "visit":
            timeline[date_str]["visits"] += 1
        elif e.get("event_type") == "file_download":
            timeline[date_str]["downloads"] += 1

    return {
        "total_visits": total_visits,
        "unique_visitors": unique_ips,
        "total_downloads": total_downloads,
        "deliverable_downloads": deliverable_downloads,
        "timeline": sorted(timeline.values(), key=lambda x: x["date"]),
        "recent_activity": recent,
    }
