from fastapi import APIRouter, Body, HTTPException, Depends, Query
from typing import List, Optional, Dict, Any
from datetime import datetime
# REMOVED raw collection imports
from models.task import TaskModel, TaskHistoryModel
from models.notification import NotificationModel
from models.user import UserModel
from routes.deps import get_current_user, get_db
from middleware.db_guard import ScopedDatabase
from logging_config import get_logger
import uuid

router = APIRouter(prefix="/api/tasks", tags=["Tasks"])
logger = get_logger("tasks")

# --- HELPERS ---

def parse_mongo_data(data):
    if isinstance(data, list):
        return [parse_mongo_data(item) for item in data]
    if isinstance(data, dict):
        if "_id" in data:
            data["_id"] = str(data["_id"])
        return {k: parse_mongo_data(v) for k, v in data.items()}
    return data

async def log_history(db: ScopedDatabase, task_id: str, user_id: str, changes: Dict[str, Any], comment: str = None):
    """Log changes to task history"""
    history_entries = []
    timestamp = datetime.now()
    
    # We need studio_id. Since we are in an agency context (ScopedDB), we can access it from db.agency_id
    # However, db.agency_id is the string ID.
    studio_id = db.agency_id
    
    # specific comment for blocked status
    blocked_comment = comment if "status" in changes and changes["status"] == "blocked" else None
    
    # Generic comment if provided and not just for blocked
    general_comment = comment if not blocked_comment else None

    for field, (old_val, new_val) in changes.items():
        entry = TaskHistoryModel(
            task_id=task_id,
            changed_by=user_id,
            field=field,
            old_value=str(old_val) if old_val is not None else None,
            new_value=str(new_val) if new_val is not None else None,
            comment=blocked_comment if field == "status" and new_val == "blocked" else general_comment,
            studio_id=studio_id,
            timestamp=timestamp
        )
        history_entries.append(entry.model_dump())
    
    if history_entries:
        await db.task_history.insert_many(history_entries)

# --- ENDPOINTS ---

@router.post("", status_code=201)
async def create_task(
    task: TaskModel = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Create a new task (Owner/Admin only)"""
    if current_user.role not in ["owner", "admin"]:
        logger.warning(f"Task creation denied: insufficient role", extra={"data": {"role": current_user.role}})
        raise HTTPException(status_code=403, detail="Only Owners and Admins can create tasks")
    
    task.created_by = current_user.id
    task.studio_id = current_user.agency_id
    
    # Validation: Project Tasks need Project ID
    if task.type == 'project' and not task.project_id:
        raise HTTPException(status_code=400, detail="Project tasks must have a project_id")
    
    result = await db.tasks.insert_one(task.model_dump())
    created_task = await db.tasks.find_one({"_id": result.inserted_id})
    
    # Log creation
    await log_history(db, task.id, current_user.id, {"creation": (None, "Task Created")})
    
    # --- NOTIFICATION LOGIC (CREATE) ---
    if task.assigned_to and task.assigned_to != current_user.id:
        # 1. Get Project Details if exists
        project_title = None
        if task.project_id:
            project = await db.projects.find_one({"id": task.project_id})
            if project:
                project_title = project.get("title")

        # 2. Construct Rich Message
        assigner_name = current_user.name
        due_str = f" (Due: {task.due_date.strftime('%b %d')})" if task.due_date else ""
        
        message = f"**{assigner_name}** assigned you a new task: **{task.title}**"
        if project_title:
            message += f" in **{project_title}**"
        message += due_str

        # 3. Create Notification with Metadata
        notification = NotificationModel(
            user_id=task.assigned_to,
            type="task_assigned",
            title="New Assignment",
            message=message,
            resource_type="task",
            resource_id=task.id,
            metadata={
                "project_id": task.project_id,
                "project_title": project_title,
                "assigner_name": assigner_name,
                "assigner_id": current_user.id,
                "due_date": task.due_date.isoformat() if task.due_date else None
            }
        )
        await db.notifications.insert_one(notification.model_dump())
        logger.info(f"Task assignment notification sent", extra={"data": {"task_id": task.id, "assignee": task.assigned_to}})
    
    logger.info(f"Task created", extra={"data": {"task_id": task.id, "title": task.title, "type": task.type, "project_id": task.project_id}})
    return parse_mongo_data(created_task)

@router.get("")
async def list_tasks(
    project_id: Optional[str] = None,
    type: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    assigned_to: Optional[str] = None,
    completed: Optional[bool] = None,
    has_project: Optional[bool] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(100, le=50000),
    search: Optional[str] = None,
    sort_by: Optional[str] = Query("created_at", description="Field to sort by: created_at, due_date, priority"),
    order: Optional[str] = Query("desc", description="Sort order: asc or desc"),
    context: Optional[str] = Query("tasks_page", description="'tasks_page' or 'project_page'"),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """List tasks with robust filtering, sorting, and pagination"""
    
    # Build Aggregation Pipeline
    pipeline = []

    # 1. Match Stage (Filters)
    # ScopedDB.aggregate() handles studio_id/agency_id injection.
    # But we need to construct the match stage for other filters.
    # NOTE: Since we are using pipeline, db.tasks.aggregate() will PREPEND the scope match.
    # So we don't need to add studio_id here manually IF we rely on the wrapper.
    # BUT, 'tasks' collection uses 'studio_id', wrapper heuristic sets it correctly.
    
    match_stage = {} 
    
    # RBAC: Force assignment filter for members (unless viewing a project's tasks)
    if current_user.role.lower() == 'member':
        if context == "project_page" and project_id:
            pass  # Allow member to see all tasks in the project
        else:
            match_stage["assigned_to"] = current_user.id
    elif assigned_to:
        # Only allow filtering by assigned_to if NOT a member (Admins/Owners)
        match_stage["assigned_to"] = assigned_to

    if search:
        match_stage["title"] = {"$regex": search, "$options": "i"}
    if project_id:
        match_stage["project_id"] = project_id
    elif has_project is True:
        match_stage["project_id"] = {"$ne": None}
    elif has_project is False:
        match_stage["project_id"] = None

    if type:
        match_stage["type"] = type
    if category:
        match_stage["category"] = category

    # Apply completed filter first, then status filter to allow status to override
    if completed is True:
        match_stage["status"] = "done"
    elif completed is False:
        match_stage["status"] = {"$ne": "done"}

    if status and status != 'all':
        match_stage["status"] = status
    
    if priority and priority != 'all':
        match_stage["priority"] = priority

    pipeline.append({"$match": match_stage})

    # 2. Lookup Project Details (for project context)
    # Convert string project_id to ObjectId for lookup
    pipeline.append({
        "$addFields": {
            "project_oid": {"$toObjectId": "$project_id"}
        }
    })
    
    pipeline.append({
        "$lookup": {
            "from": "projects",
            "localField": "project_oid",
            "foreignField": "_id",
            "as": "project_info"
        }
    })
    
    # 3. Add Fields from Project + Priority Score
    pipeline.append({
        "$addFields": {
            "project_name": {"$arrayElemAt": ["$project_info.title", 0]},
            "project_code": {"$arrayElemAt": ["$project_info.code", 0]},
            "project_vertical": {"$arrayElemAt": ["$project_info.vertical", 0]},
            "client_name": {"$arrayElemAt": ["$project_info.metadata.client_name", 0]},
            "project_color": {"$arrayElemAt": ["$project_info.color", 0]},
            "priority_score": {
                "$switch": {
                    "branches": [
                        {"case": {"$eq": ["$priority", "urgent"]}, "then": 4},
                        {"case": {"$eq": ["$priority", "high"]}, "then": 3},
                        {"case": {"$eq": ["$priority", "medium"]}, "then": 2},
                        {"case": {"$eq": ["$priority", "low"]}, "then": 1}
                    ],
                    "default": 0
                }
            }
        }
    })
    
    # 4. Remove project_info array and temporary oid
    pipeline.append({"$project": {"project_info": 0, "project_oid": 0}})

    # 5. Sort (Compound: Primary sort + Priority as tiebreaker)
    sort_direction = -1 if order == "desc" else 1
    primary_sort_field = sort_by if sort_by in ["created_at", "due_date", "priority_score"] else "created_at"
    
    if sort_by == "priority":
        primary_sort_field = "priority_score"
    
    if sort_by == "due_date":
        pipeline.append({
            "$addFields": {
                "due_date_day": {
                    "$dateToString": {"format": "%Y-%m-%d", "date": "$due_date"}
                }
            }
        })
        primary_sort_field = "due_date_day"
    
    sort_stage = {primary_sort_field: sort_direction, "priority_score": -1}
    pipeline.append({"$sort": sort_stage})

    # 6. Pagination (Facet for total count and data)
    skip = (page - 1) * limit 
    pipeline.append({
        "$facet": {
            "metadata": [{"$count": "total"}],
            "data": [{"$skip": skip}, {"$limit": limit}]
        }
    })

    result = await db.tasks.aggregate(pipeline).to_list(1)
    
    data = result[0]["data"]
    total = result[0]["metadata"][0]["total"] if result[0]["metadata"] else 0

    return {
        "data": parse_mongo_data(data),
        "total": total,
        "page": page,
        "limit": limit
    }

@router.patch("/{task_id}")
async def update_task(
    task_id: str,
    update_data: Dict[str, Any] = Body(...),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Update task (Owner/Admin or Assignee)"""
    existing_task = await db.tasks.find_one({"id": task_id})
    if not existing_task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Permissions
    is_owner_admin = current_user.role in ["owner", "admin"]
    is_assignee = existing_task.get("assigned_to") == current_user.id
    
    if not (is_owner_admin or is_assignee):
        logger.warning(f"Task update denied: not authorized", extra={"data": {"task_id": task_id, "role": current_user.role}})
        raise HTTPException(status_code=403, detail="Not authorized to update this task")
    
    # Blocked Status Logic
    new_status = update_data.get("status")
    comment = update_data.pop("comment", None) # Extract comment from payload
    
    if new_status == "blocked" and not comment:
        raise HTTPException(status_code=400, detail="A comment is required when blocking a task")
        
    # Calculate Changes
    changes = {}
    valid_fields = TaskModel.model_fields.keys()
    
    for key, new_val in update_data.items():
        if key in valid_fields:
            old_val = existing_task.get(key)
            if old_val != new_val:
                changes[key] = (old_val, new_val)
    
    if not changes:
        return parse_mongo_data(existing_task)
        
    # Update DB
    update_data["updated_at"] = datetime.now()
    await db.tasks.update_one(
        {"id": task_id},
        {"$set": update_data}
    )
    
    # Log History
    await log_history(db, task_id, current_user.id, changes, comment)
    
    # --- NOTIFICATION LOGIC (UPDATE) ---
    if "assigned_to" in changes:
        old_assignee, new_assignee = changes["assigned_to"]
        if new_assignee and new_assignee != current_user.id:
            # Determine latest title
            current_title = update_data.get("title") or existing_task.get("title", "Untitled Task")
            
            # 1. Get Project Details
            project_id = existing_task.get("project_id")
            project_title = None
            if project_id:
                project = await db.projects.find_one({"id": project_id})
                if project:
                    project_title = project.get("title")

            # 2. Construct Rich Message
            assigner_name = current_user.name
            due_date = update_data.get("due_date", existing_task.get("due_date"))
            if isinstance(due_date, str):
                try: 
                    due_date = datetime.fromisoformat(due_date.replace('Z', '+00:00'))
                except: pass

            due_str = f" (Due: {due_date.strftime('%b %d')})" if due_date and isinstance(due_date, datetime) else ""
            
            message = f"**{assigner_name}** assigned you: **{current_title}**"
            if project_title:
                message += f" in **{project_title}**"
            message += due_str
            
            # 3. Create Notification with Metadata
            notification = NotificationModel(
                user_id=new_assignee,
                type="task_assigned",
                title="New Task Assigned",
                message=message,
                resource_type="task",
                resource_id=task_id,
                 metadata={
                    "project_id": project_id,
                    "project_title": project_title,
                    "assigner_name": assigner_name,
                    "assigner_id": current_user.id,
                    "due_date": due_date.isoformat() if due_date and isinstance(due_date, datetime) else None
                }
            )
            await db.notifications.insert_one(notification.model_dump())
            logger.info(f"Task reassignment notification sent", extra={"data": {"task_id": task_id, "new_assignee": new_assignee}})
    
    logger.info(f"Task updated", extra={"data": {"task_id": task_id, "fields_changed": list(changes.keys())}})
    updated_task = await db.tasks.find_one({"id": task_id})
    return parse_mongo_data(updated_task)

@router.delete("/{task_id}")
async def delete_task(
    task_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Hard delete task (Owner/Admin only)"""
    if current_user.role not in ["owner", "admin"]:
        logger.warning(f"Task deletion denied: insufficient role", extra={"data": {"task_id": task_id, "role": current_user.role}})
        raise HTTPException(status_code=403, detail="Only Owners and Admins can delete tasks")
        
    result = await db.tasks.delete_one({"id": task_id})
    if result.deleted_count == 0:
        logger.warning(f"Task deletion failed: not found", extra={"data": {"task_id": task_id}})
        raise HTTPException(status_code=404, detail="Task not found")
    
    logger.info(f"Task deleted", extra={"data": {"task_id": task_id}})
    return {"message": "Task deleted successfully"}

@router.get("/{task_id}/history")
async def get_task_history(
    task_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Get history for a specific task"""
    # Guardrail: db.task_history uses studio_id filter automatically
    history = await db.task_history.find({"task_id": task_id}).sort("timestamp", -1).to_list(100)
    return parse_mongo_data(history)
