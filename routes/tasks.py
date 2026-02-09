from fastapi import APIRouter, Body, HTTPException, Depends, Query
from typing import List, Optional, Dict, Any
from datetime import datetime
from database import tasks_collection, task_history_collection, projects_collection, notifications_collection
from models.task import TaskModel, TaskHistoryModel
from models.notification import NotificationModel
from models.user import UserModel
from routes.deps import get_current_user
import uuid

router = APIRouter(prefix="/api/tasks", tags=["Tasks"])

# --- HELPERS ---

def parse_mongo_data(data):
    if isinstance(data, list):
        return [parse_mongo_data(item) for item in data]
    if isinstance(data, dict):
        if "_id" in data:
            data["_id"] = str(data["_id"])
        return {k: parse_mongo_data(v) for k, v in data.items()}
    return data

async def log_history(task_id: str, user_id: str, changes: Dict[str, Any], comment: str = None):
    """Log changes to task history"""
    history_entries = []
    timestamp = datetime.now()
    
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
            timestamp=timestamp
        )
        history_entries.append(entry.model_dump())
    
    if history_entries:
        await task_history_collection.insert_many(history_entries)

# --- ENDPOINTS ---

@router.post("/", status_code=201)
async def create_task(
    task: TaskModel = Body(...),
    current_user: UserModel = Depends(get_current_user)
):
    """Create a new task (Owner/Admin only)"""
    if current_user.role not in ["owner", "admin"]:
        raise HTTPException(status_code=403, detail="Only Owners and Admins can create tasks")
    
    task.created_by = current_user.id
    task.studio_id = current_user.agency_id
    
    # Validation: Project Tasks need Project ID
    if task.type == 'project' and not task.project_id:
        raise HTTPException(status_code=400, detail="Project tasks must have a project_id")
    
    result = await tasks_collection.insert_one(task.model_dump())
    created_task = await tasks_collection.find_one({"_id": result.inserted_id})
    
    # Log creation
    await log_history(task.id, current_user.id, {"creation": (None, "Task Created")})
    
    # --- NOTIFICATION LOGIC (CREATE) ---
    if task.assigned_to and task.assigned_to != current_user.id:
        notification = NotificationModel(
            user_id=task.assigned_to,
            type="task_assigned",
            title="New User Task",
            message=f"You have been assigned to: {task.title}",
            resource_type="task",
            resource_id=task.id
        )
        await notifications_collection.insert_one(notification.model_dump())
    
    return parse_mongo_data(created_task)

@router.get("/")
async def list_tasks(
    project_id: Optional[str] = None,
    type: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    assigned_to: Optional[str] = None,
    completed: Optional[bool] = None,
    has_project: Optional[bool] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(100, le=500),
    search: Optional[str] = None,
    current_user: UserModel = Depends(get_current_user)
):
    """List tasks with robust filtering and pagination"""
    # Build Aggregation Pipeline
    pipeline = []

    # 1. Match Stage (Filters)
    match_stage = {"studio_id": current_user.agency_id}
    
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

    if status and status != 'all':
        match_stage["status"] = status
    
    if completed is True:
        match_stage["status"] = "done"
    elif completed is False:
        match_stage["status"] = {"$ne": "done"}

    if assigned_to:
        match_stage["assigned_to"] = assigned_to

    pipeline.append({"$match": match_stage})

    # 2. Lookup Project Details (for project context)
    pipeline.append({
        "$lookup": {
            "from": "projects",
            "localField": "project_id",
            "foreignField": "id", # Assuming project 'id' is used, check if _id or id
            "as": "project_info"
        }
    })
    
    # 3. Add Fields from Project (Unwind optional, preserve null)
    pipeline.append({
        "$addFields": {
            "project_name": {"$arrayElemAt": ["$project_info.title", 0]},
            "client_name": {"$arrayElemAt": ["$project_info.metadata.client_name", 0]},
            "project_color": {"$arrayElemAt": ["$project_info.color", 0]} # If project has color
        }
    })
    
    # 4. Remove project_info array to keep clean
    pipeline.append({"$project": {"project_info": 0}})

    # 5. Sort
    sort_stage = {"created_at": -1} # Default
    if not completed:
        # TODO: Advanced sort logic if needed
        pass
    pipeline.append({"$sort": sort_stage})

    # 6. Pagination (Facet for total count and data)
    skip = (page - 1) * limit  # Calculate skip for pagination
    pipeline.append({
        "$facet": {
            "metadata": [{"$count": "total"}],
            "data": [{"$skip": skip}, {"$limit": limit}]
        }
    })

    result = await tasks_collection.aggregate(pipeline).to_list(1)
    
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
    current_user: UserModel = Depends(get_current_user)
):
    """Update task (Owner/Admin or Assignee)"""
    existing_task = await tasks_collection.find_one({"id": task_id, "studio_id": current_user.agency_id})
    if not existing_task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Permissions
    is_owner_admin = current_user.role in ["owner", "admin"]
    is_assignee = existing_task.get("assigned_to") == current_user.id
    
    if not (is_owner_admin or is_assignee):
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
    await tasks_collection.update_one(
        {"id": task_id},
        {"$set": update_data}
    )
    
    # Log History
    await log_history(task_id, current_user.id, changes, comment)
    
    # --- NOTIFICATION LOGIC (UPDATE) ---
    if "assigned_to" in changes:
        old_assignee, new_assignee = changes["assigned_to"]
        if new_assignee and new_assignee != current_user.id:
            # Determine latest title
            current_title = update_data.get("title") or existing_task.get("title", "Untitled Task")
            
            # Notify the new assignee
            notification = NotificationModel(
                user_id=new_assignee,
                type="task_assigned",
                title="New Task Assigned",
                message=f"You have been assigned to: {current_title}",
                resource_type="task",
                resource_id=task_id
            )
            await notifications_collection.insert_one(notification.model_dump())
    
    updated_task = await tasks_collection.find_one({"id": task_id})
    return parse_mongo_data(updated_task)

@router.delete("/{task_id}")
async def delete_task(
    task_id: str,
    current_user: UserModel = Depends(get_current_user)
):
    """Hard delete task (Owner/Admin only)"""
    if current_user.role not in ["owner", "admin"]:
        raise HTTPException(status_code=403, detail="Only Owners and Admins can delete tasks")
        
    result = await tasks_collection.delete_one({"id": task_id, "studio_id": current_user.agency_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")
        
    return {"message": "Task deleted successfully"}

@router.get("/{task_id}/history")
async def get_task_history(
    task_id: str,
    current_user: UserModel = Depends(get_current_user)
):
    """Get history for a specific task"""
    history = await task_history_collection.find({"task_id": task_id}).sort("timestamp", -1).to_list(100)
    return parse_mongo_data(history)
