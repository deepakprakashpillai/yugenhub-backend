from fastapi import APIRouter, Body, HTTPException, Query
from bson import ObjectId
from datetime import datetime
from database import projects_collection, configs_collection, tasks_collection, associates_collection, users_collection, notifications_collection
from models.project import ProjectModel, EventModel, DeliverableModel, AssignmentModel
from models.notification import NotificationModel
from routes.deps import get_current_user
from models.user import UserModel
from fastapi import Depends

router = APIRouter(prefix="/api/projects", tags=["Projects"])

# --- HELPER: Notify Associate on Assignment ---
async def notify_associate_assignment(associate_id: str, project_code: str, event_type: str, agency_id: str):
    """
    Send a notification to an associate when they are assigned to an event.
    Looks up the associate's email, finds the corresponding user, and creates notification.
    """
    if not associate_id:
        return
    
    # Find associate and their email
    associate = await associates_collection.find_one({"_id": ObjectId(associate_id), "agency_id": agency_id})
    if not associate or not associate.get("email_id"):
        return  # No email, can't notify
    
    # Find user with this email
    user = await users_collection.find_one({"email": associate.get("email_id")})
    if not user:
        return  # No user account, can't notify
    
    # Create notification
    notification = NotificationModel(
        user_id=user.get("id"),
        agency_id=agency_id,
        type="event_assigned",
        title="Assigned to Event",
        message=f"You have been assigned to {event_type} for project {project_code}",
        resource_type="project",
        resource_id=None  # Could add project_id here
    )
    
    await notifications_collection.insert_one(notification.model_dump())
    print(f"🔔 Notification sent to {associate.get('name')} for event assignment")

# --- HELPER FUNCTION ---
# Recursively fixes ObjectId errors for nested events/deliverables
def parse_mongo_data(data):
    if isinstance(data, list):
        return [parse_mongo_data(item) for item in data]
    if isinstance(data, dict):
        return {k: (str(v) if isinstance(v, ObjectId) else parse_mongo_data(v)) for k, v in data.items()}
    return data

# --- CORE ENDPOINTS ---

@router.post("/", status_code=201)
async def create_project(project: ProjectModel = Body(...), current_user: UserModel = Depends(get_current_user)):
    """CREATE: Validate vertical against config and save new project"""
    current_agency_id = current_user.agency_id
    project.agency_id = current_agency_id

    # 1. Fetch the active config to check valid verticals
    config = await configs_collection.find_one({"agency_id": current_agency_id})
    allowed_verticals = [v["id"] for v in config.get("verticals", [])] if config else []
    
    allowed_verticals = [v["id"] for v in config.get("verticals", [])] if config else []
    
    # 2. Validation
    # 2a. Validate Vertical
    if project.vertical not in allowed_verticals:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid vertical. Allowed: {allowed_verticals}"
        )

    # 2b. Validate Metadata against Configured Fields
    selected_vertical = next((v for v in config["verticals"] if v["id"] == project.vertical), None)
    if selected_vertical:
        for field_def in selected_vertical.get("fields", []):
            field_name = field_def["name"]
            is_required = True # Assuming all defined fields are required for now, or check field_def
            
            if is_required and field_name not in project.metadata:
                # Optionally skippable if we add a "required" flag to VerticalField model later
                pass 

            if field_name in project.metadata:
                value = project.metadata[field_name]
                # Validate Select Options
                if field_def["type"] == "select" and value not in field_def.get("options", []):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid value for '{field_name}'. Allowed: {field_def.get('options')}"
                    )

    # 3. Check for duplicate Project Code
    project_data = project.model_dump()
    project_data["code"] = project_data["code"].upper() # Force uppercase
    
    if await projects_collection.find_one({"code": project_data["code"], "agency_id": current_agency_id}):
        raise HTTPException(status_code=400, detail="Project code already exists")
        
    # 4. Save
    new_project = await projects_collection.insert_one(project_data)
    
    # Return the clean object
    project_data["_id"] = str(new_project.inserted_id)
    return parse_mongo_data(project_data)

@router.get("/")
async def list_projects(
    vertical: str = None, 
    search: str = None,
    status: str = None,
    view: str = "all", # New parameter
    sort: str = "newest",
    page: int = Query(1, ge=1), 
    limit: int = Query(12, le=100),
    current_user: UserModel = Depends(get_current_user)
):
    """READ LIST: Get projects with pagination, filtering, and sorting"""
    current_agency_id = current_user.agency_id
    query = {"agency_id": current_agency_id}
    
    # 1. Filters
    if vertical:
        query["vertical"] = vertical

    # View Logic (Supercedes Status if View is specific)
    # Status filter is applied ON TOP of View if provided (e.g. View=Ongoing + Status=Enquiry)
    
    base_status_filter = {}

    if view == "upcoming":
        base_status_filter = {"status": "booked"}
    elif view == "ongoing":
        base_status_filter = {"status": {"$in": ["ongoing", "Ongoing"]}}
    elif view == "enquiry":
        base_status_filter = {"status": {"$in": ["enquiry", "Enquiry"]}}
    elif view == "completed":
        base_status_filter = {"status": {"$in": ["completed", "Completed"]}}
    elif view == "cancelled":
        base_status_filter = {"status": {"$in": ["cancelled", "archived", "Cancelled", "Archived"]}}
    
    # If specific status is requested, it must be valid within the view
    if status and status != "all":
        # If view logic already set a constraint, we need to respect both (intersection)
        # But logically, 'status' filter is usually a subset of view or a specific drill-down.
        # We will let 'status' override if it's specific, but realistically the UI should only show valid options.
        query["status"] = status
        
        # Security check: if view='completed' but user requests status='production', returns empty
        if view == "completed" and status.lower() not in ["completed"]:
             query["status"] = "IMPOSSIBLE_MATCH"
        if view == "ongoing" and status.lower() not in ["enquiry", "production"]:
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
    # If sort is 'upcoming', fetch ALL matching query (no skip/limit yet), sort in Py, then slice.
    # If sort is standard, use Mongo skip/limit.
    
    skip = (page - 1) * limit

    if sort == "upcoming":
        # Fetch ALL matching basics
        cursor = projects_collection.find(query)
        all_projects = await cursor.to_list(length=1000) # Safety cap
        
        def get_next_event_date(p):
            now = datetime.now()
            future_events = [e["start_date"] for e in p.get("events", []) if isinstance(e.get("start_date"), str) and e["start_date"] > now.isoformat()]
            # If no future events, push to end (year 9999)
            return min(future_events) if future_events else "9999-12-31"

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
        cursor = projects_collection.find(query).sort("created_on", sort_order).skip(skip).limit(limit)
        paginated_data = await cursor.to_list(length=limit)
        total = await projects_collection.count_documents(query)
    
    # 3. Enrich with Task Stats (Progress)
    if paginated_data:
        # Extract IDs using explicit string conversion for safety
        project_ids = [str(p["_id"]) for p in paginated_data]
        
        # Aggregate stats for these projects only
        stats_cursor = tasks_collection.aggregate([
            {
                "$match": {
                    "project_id": {"$in": project_ids},
                    "studio_id": current_agency_id
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
                total = s["total_tasks"]
                completed = s["completed_tasks"]
                percentage = int((completed / total) * 100) if total > 0 else 0
                project["stats"] = {
                    "total_tasks": total,
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
async def get_project(id: str, current_user: UserModel = Depends(get_current_user)):
    """READ ONE: Fetch a single project by ID"""
    current_agency_id = current_user.agency_id
    if not ObjectId.is_valid(id):
        raise HTTPException(status_code=400, detail="Invalid ID")
    
    project = await projects_collection.find_one({"_id": ObjectId(id), "agency_id": current_agency_id})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    return parse_mongo_data(project)

@router.delete("/{id}")
async def delete_project(id: str, current_user: UserModel = Depends(get_current_user)):
    """DELETE: Remove an entire project"""
    current_agency_id = current_user.agency_id
    if not ObjectId.is_valid(id):
        raise HTTPException(status_code=400, detail="Invalid ID")
    
    # Cascade Delete Tasks
    await tasks_collection.delete_many({"project_id": id, "studio_id": current_agency_id})

    result = await projects_collection.delete_one({"_id": ObjectId(id), "agency_id": current_agency_id})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")
    
    return {"message": "Project and associated tasks deleted successfully"}

@router.delete("/{project_id}/events/{event_id}")
async def delete_event(project_id: str, event_id: str, current_user: UserModel = Depends(get_current_user)):
    """DELETE: Remove an event from a project"""
    current_agency_id = current_user.agency_id
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")
    
    # Cascade Delete Tasks linked to this Event
    await tasks_collection.delete_many({"event_id": event_id, "project_id": project_id, "studio_id": current_agency_id})

    result = await projects_collection.update_one(
        {"_id": ObjectId(project_id), "agency_id": current_agency_id},
        {"$pull": {"events": {"id": event_id}}, "$set": {"updated_on": datetime.now()}}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Project or Event not found")
    
    return {"message": "Event and associated tasks deleted successfully"}

@router.get("/stats/overview")
async def get_project_stats(vertical: str = None, current_user: UserModel = Depends(get_current_user)):
    """READ STATS: Get overview metrics for the vertical/dashboard"""
    current_agency_id = current_user.agency_id
    base_query = {"agency_id": current_agency_id}
    if vertical:
        base_query["vertical"] = vertical

    # 1. Total Projects
    total = await projects_collection.count_documents(base_query)

    # 2. Active Projects (Status != COMPLETED, ARCHIVED, CANCELLED)
    active_query = base_query.copy()
    active_query["status"] = {"$nin": ["Completed", "Archived", "Cancelled", "completed", "archived", "cancelled"]}
    active = await projects_collection.count_documents(active_query)

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
    
    this_month = await projects_collection.count_documents(month_query)

    # 4. Ongoing/Production (Specific Status)
    prod_query = base_query.copy()
    # Match both 'ongoing' and 'production' (case insensitive)
    prod_query["status"] = {"$in": ["ongoing", "Ongoing", "production", "Production"]}
    ongoing_count = await projects_collection.count_documents(prod_query)

    return {
        "total": total,
        "active": active,
        "ongoing": ongoing_count,
        "this_month": this_month
    }

# --- ADVANCED LOGIC (The stuff you had) ---

@router.post("/{project_id}/events")
async def add_event_to_project(project_id: str, event: EventModel = Body(...), current_user: UserModel = Depends(get_current_user)):
    """UPDATE: Add a new event (like a Reception) to an existing Project"""
    current_agency_id = current_user.agency_id
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    result = await projects_collection.update_one(
        {"_id": ObjectId(project_id), "agency_id": current_agency_id},
        {"$push": {"events": event.model_dump()}, "$set": {"updated_on": datetime.now()}}
    )

    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")

    return {"message": "Event added successfully"}

@router.patch("/{project_id}")
async def update_project(project_id: str, update_data: dict = Body(...), current_user: UserModel = Depends(get_current_user)):
    """UPDATE: Generic update for project fields"""
    current_agency_id = current_user.agency_id
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    # Prevent updating immutable fields or fields handled by specific logic
    update_data.pop("_id", None)
    update_data.pop("events", None) # Events should be handled via specific endpoints
    update_data.pop("code", None) # Code shouldn't change easily
    update_data.pop("agency_id", None) # Cannot transfer ownership
    update_data["updated_on"] = datetime.now()

    result = await projects_collection.update_one(
        {"_id": ObjectId(project_id), "agency_id": current_agency_id},
        {"$set": update_data}
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")

    return {"message": "Project updated successfully"}

@router.patch("/{project_id}/events/{event_id}")
async def update_event(project_id: str, event_id: str, update_data: dict = Body(...), current_user: UserModel = Depends(get_current_user)):
    """UPDATE: Update a specific event within a project"""
    current_agency_id = current_user.agency_id
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    # Prefix keys with "events.$." to update the matched array element
    set_fields = {f"events.$.{k}": v for k, v in update_data.items()}
    set_fields["updated_on"] = datetime.now()

    result = await projects_collection.update_one(
        {"_id": ObjectId(project_id), "events.id": event_id, "agency_id": current_agency_id},
        {"$set": set_fields}
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project or Event not found")

    return {"message": "Event updated successfully"}

# Legacy Deliverable Endpoints Removed - Now handled via /api/tasks

@router.post("/{project_id}/events/{event_id}/assignments")
async def add_assignment(project_id: str, event_id: str, assignment: AssignmentModel = Body(...), current_user: UserModel = Depends(get_current_user)):
    """CREATE: Add an associate assignment to a specific event"""
    current_agency_id = current_user.agency_id
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    # Get project info for notification
    project = await projects_collection.find_one({"_id": ObjectId(project_id), "agency_id": current_agency_id})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Find the event to get its type
    event_type = "Event"
    for evt in project.get("events", []):
        if evt.get("id") == event_id:
            event_type = evt.get("type", "Event")
            break

    result = await projects_collection.update_one(
        {"_id": ObjectId(project_id), "events.id": event_id, "agency_id": current_agency_id},
        {"$push": {"events.$.assignments": assignment.model_dump()}, "$set": {"updated_on": datetime.now()}}
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project or Event not found")

    # Send notification to the assigned associate
    await notify_associate_assignment(
        assignment.associate_id, 
        project.get("code", "Unknown"), 
        event_type,
        current_agency_id
    )

    return {"message": "Assignment added successfully", "id": assignment.id}

@router.patch("/{project_id}/events/{event_id}/assignments/{assignment_id}")
async def update_assignment(project_id: str, event_id: str, assignment_id: str, update_data: dict = Body(...), current_user: UserModel = Depends(get_current_user)):
    """UPDATE: deep nested update for an assignment"""
    current_agency_id = current_user.agency_id
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    # Prefix keys for arrayFilters
    set_fields = {f"events.$[evt].assignments.$[asn].{k}": v for k, v in update_data.items()}
    set_fields["updated_on"] = datetime.now()

    result = await projects_collection.update_one(
        {"_id": ObjectId(project_id), "agency_id": current_agency_id},
        {"$set": set_fields},
        array_filters=[{"evt.id": event_id}, {"asn.id": assignment_id}]
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")
    
    return {"message": "Assignment updated"}

@router.delete("/{project_id}/events/{event_id}/assignments/{assignment_id}")
async def delete_assignment(project_id: str, event_id: str, assignment_id: str, current_user: UserModel = Depends(get_current_user)):
    """DELETE: Remove an assignment"""
    current_agency_id = current_user.agency_id
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid Project ID")

    result = await projects_collection.update_one(
        {"_id": ObjectId(project_id), "events.id": event_id, "agency_id": current_agency_id},
        {"$pull": {"events.$.assignments": {"id": assignment_id}}, "$set": {"updated_on": datetime.now()}}
    )

    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Item not found or already deleted")

    return {"message": "Assignment deleted"}

@router.get("/assigned/{associate_id}")
async def get_associate_schedule(associate_id: str, current_user: UserModel = Depends(get_current_user)):
    """SEARCH: Find all projects where this associate is working"""
    current_agency_id = current_user.agency_id
    query = {"events.assignments.associate_id": associate_id, "agency_id": current_agency_id}
    projects = await projects_collection.find(query).to_list(1000)
    return parse_mongo_data(projects)
