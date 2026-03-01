from fastapi import APIRouter, Body, HTTPException, Query, BackgroundTasks
import re # IMPORTED
from bson import ObjectId
from datetime import datetime
# REMOVED raw collection imports
from database import notifications_collection
from models.project import ProjectModel, EventModel, DeliverableModel, AssignmentModel
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

router = APIRouter(prefix="/api/projects", tags=["Projects"])
logger = get_logger("projects")

# --- HELPER: Notify Associate on Assignment ---
async def notify_associate_assignment(db: ScopedDatabase, background_tasks: BackgroundTasks, associate_id: str, project_code: str, event_type: str, event_date: datetime, agency_id: str):
    """
    Send a notification to an associate when they are assigned to an event.
    Looks up the associate's email, finds the corresponding user, and creates notification.
    """
    if not associate_id:
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
    now = datetime.now()
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
            # a. Create Tasks for Deliverables
            if event.deliverables:
                for deliverable in event.deliverables:
                    task = TaskModel(
                        title=f"{deliverable.type} ({event.type})",
                        description=f"Deliverable for {event.type}",
                        project_id=project_id,
                        event_id=event.id,
                        status="todo",
                        priority=deliverable.status.lower() if hasattr(deliverable, 'priority') else "medium", # Fallback
                        due_date=deliverable.due_date,
                        assigned_to=deliverable.incharge_id,
                        studio_id=current_user.agency_id,
                        created_by=current_user.id,
                        type="project",
                        category="deliverable"
                    )
                    all_new_tasks.append(task.model_dump())
            
            # b. Send Notifications for Assignments
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
        
        if all_new_tasks:
            await db.tasks.insert_many(all_new_tasks)
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
    limit: int = Query(12, le=50000),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """READ LIST: Get projects with pagination, filtering, and sorting"""
    # Helper for robust date parsing inside the endpoint
    def parse_event_date(date_val):
        if not date_val: return None
        if isinstance(date_val, datetime):
            return date_val.replace(tzinfo=None)
        if isinstance(date_val, str):
            try:
                return datetime.fromisoformat(date_val.replace('Z', '+00:00')).replace(tzinfo=None)
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
        now = datetime.now()
        
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

                # Check if event is in the past
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
                nows = datetime.now()
                future_events = [parse_event_date(e.get("start_date")) for e in p.get("events", [])]
                future_dates = [d for d in future_events if d and d > nows]
                return min(future_dates) if future_dates else datetime(9999, 12, 31)
            
            val = p.get("created_on")
            if isinstance(val, datetime):
                return val.replace(tzinfo=None)
            elif isinstance(val, str):
                try:
                    return datetime.fromisoformat(val.replace("Z", "+00:00")).replace(tzinfo=None)
                except ValueError:
                    return datetime.min
            return datetime.min

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
            now = datetime.now()
            future_events = [parse_event_date(e.get("start_date")) for e in p.get("events", [])]
            future_dates = [d for d in future_events if d and d > now]
            return min(future_dates) if future_dates else datetime(9999, 12, 31)

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
async def delete_project(id: str, current_user: UserModel = Depends(get_current_user), db: ScopedDatabase = Depends(get_db)):
    """DELETE: Remove an entire project"""
    if not ObjectId.is_valid(id):
        raise HTTPException(status_code=400, detail="Invalid ID")
    
    # Cascade Delete Tasks (db.tasks uses studio_id automatically)
    await db.tasks.delete_many({"project_id": id})

    # RBAC: Check vertical access before deleting
    project = await db.projects.find_one({"_id": ObjectId(id)})
    if project:
        user_verticals = await get_user_verticals(current_user, db)
        if project.get("vertical") and project["vertical"] not in user_verticals:
            raise HTTPException(status_code=404, detail="Project not found")

    result = await db.projects.delete_one({"_id": ObjectId(id)})
    
    if result.deleted_count == 0:
        logger.warning(f"Delete project failed: not found", extra={"data": {"project_id": id}})
        raise HTTPException(status_code=404, detail="Project not found")
    
    logger.info(f"Project deleted", extra={"data": {"project_id": id}})
    return {"message": "Project and associated tasks deleted successfully"}

@router.delete("/{project_id}/events/{event_id}")
async def delete_event(project_id: str, event_id: str, current_user: UserModel = Depends(get_current_user), db: ScopedDatabase = Depends(get_db)):
    """DELETE: Remove an event from a project"""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")
    
    # Cascade Delete Tasks linked to this Event
    await db.tasks.delete_many({"event_id": event_id, "project_id": project_id})

    result = await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {"$pull": {"events": {"id": event_id}}, "$set": {"updated_on": datetime.now()}}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Project or Event not found")
    
    return {"message": "Event and associated tasks deleted successfully"}

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
    now = datetime.now()
    start_of_month = datetime(now.year, now.month, 1)
    if now.month == 12:
        next_month = datetime(now.year + 1, 1, 1)
    else:
        next_month = datetime(now.year, now.month + 1, 1)
    
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
    event: EventModel = Body(...), 
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """UPDATE: Add a new event (like a Reception) to an existing Project"""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    result = await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {"$push": {"events": event.model_dump()}, "$set": {"updated_on": datetime.now()}}
    )

    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")

    # --- SYNC: Create Tasks for Deliverables ---
    # Deliverables should be treated as Tasks for progress tracking
    if event.deliverables:
        new_tasks = []
        project_code = "UNKNOWN"
        
        # We need project details for the task
        project_doc = await db.projects.find_one({"_id": ObjectId(project_id)})
        project_code = project_doc.get("code", "PROJECT") if project_doc else "PROJECT"
        
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

    return {"message": "Event added successfully"}

@router.patch("/{project_id}")
async def update_project(
    project_id: str, 
    update_data: dict = Body(...), 
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """UPDATE: Generic update for project fields"""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    # Prevent updating immutable fields or fields handled by specific logic
    update_data.pop("_id", None)
    update_data.pop("events", None) 
    update_data.pop("assignments", None)  # Handled by dedicated endpoints
    update_data.pop("code", None) 
    update_data.pop("agency_id", None) 
    update_data["updated_on"] = datetime.now()

    result = await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {"$set": update_data}
    )

    if result.matched_count == 0:
        logger.warning(f"Update project failed: not found", extra={"data": {"project_id": project_id}})
        raise HTTPException(status_code=404, detail="Project not found")

    logger.info(f"Project updated", extra={"data": {"project_id": project_id, "fields": list(update_data.keys())}})
    return {"message": "Project updated successfully"}

@router.patch("/{project_id}/events/{event_id}")
async def update_event(
    project_id: str, 
    event_id: str, 
    update_data: dict = Body(...), 
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """UPDATE: Update a specific event within a project"""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    # If deliverables are being updated, we need to sync them to Tasks
    # Find existing tasks for this event
    if "deliverables" in update_data:
        existing_tasks = await db.tasks.find({"project_id": project_id, "event_id": event_id, "category": "deliverable"}).to_list(length=None)
        existing_task_deliverable_ids = [t.get("metadata", {}).get("deliverable_id") for t in existing_tasks]
        all_new_tasks = []
        project_code = "UNKNOWN"
        project_doc = await db.projects.find_one({"_id": ObjectId(project_id)})
        event_type = "Event"
        if project_doc:
            project_code = project_doc.get("code", "PROJECT")
            for evt in project_doc.get("events", []):
                if evt.get("id") == event_id:
                    event_type = evt.get("type", "Event")
                    break

        for deliverable in update_data["deliverables"]:
            # If this is a new deliverable (doesn't exist in tasks or is newly generated)
            deliv_id = deliverable.get("id")
            
            # Very basic sync: just create tasks for deliverables that don't have one yet.
            # In a full sync, we'd also update/delete tasks, but since deliverables
            # are usually managed via TaskModal now, this is mainly for the "Edit Event" modal fallback.
            if deliv_id not in existing_task_deliverable_ids:
                task = TaskModel(
                    title=f"{deliverable.get('type', 'Deliverable')} ({event_type})",
                    description=f"Deliverable for {event_type}",
                    project_id=project_id,
                    event_id=event_id,
                    status=deliverable.get('status', 'Pending').lower(),
                    priority="medium",
                    due_date=deliverable.get('due_date'),
                    assigned_to=deliverable.get('incharge_id'),
                    studio_id=current_user.agency_id,
                    created_by=current_user.id,
                    type="project",
                    category="deliverable",
                    quantity=deliverable.get("quantity", 1),
                    metadata={"deliverable_id": deliv_id}
                )
                all_new_tasks.append(task.model_dump())
            else:
                await db.tasks.update_one(
                    {"project_id": project_id, "event_id": event_id, "category": "deliverable", "metadata.deliverable_id": deliv_id},
                    {"$set": {
                        "status": deliverable.get('status', 'Pending').lower(),
                        "due_date": deliverable.get('due_date'),
                        "quantity": deliverable.get("quantity", 1),
                        "title": f"{deliverable.get('type', 'Deliverable')} ({event_type})",
                        "updated_on": datetime.now()
                    }}
                )
        
        if all_new_tasks: # Changed from new_tasks to all_new_tasks
            await db.tasks.insert_many(all_new_tasks) # Changed from new_tasks to all_new_tasks
            logger.info(f"Created synced tasks from deliverables", extra={"data": {"count": len(all_new_tasks), "project_id": project_id}})

    # Prefix keys with "events.$." to update the matched array element
    set_fields = {f"events.$.{k}": v for k, v in update_data.items()}
    set_fields["updated_on"] = datetime.now()

    result = await db.projects.update_one(
        {"_id": ObjectId(project_id), "events.id": event_id},
        {"$set": set_fields}
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project or Event not found")

    return {"message": "Event updated successfully"}

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
        {"$push": {"events.$.assignments": assignment.model_dump()}, "$set": {"updated_on": datetime.now()}}
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
    set_fields["updated_on"] = datetime.now()

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
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """DELETE: Remove an assignment"""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    result = await db.projects.update_one(
        {"_id": ObjectId(project_id), "events.id": event_id},
        {"$pull": {"events.$.assignments": {"id": assignment_id}}, "$set": {"updated_on": datetime.now()}}
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
        {"$push": {"assignments": assignment.model_dump()}, "$set": {"updated_on": datetime.now()}}
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")

    # Send notification
    await notify_associate_assignment(
        db, background_tasks,
        assignment.associate_id,
        project.get("code", "Unknown"),
        "Project Assignment",
        datetime.now(),
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
    set_fields["updated_on"] = datetime.now()

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
        {"$pull": {"assignments": {"id": assignment_id}}, "$set": {"updated_on": datetime.now()}}
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
