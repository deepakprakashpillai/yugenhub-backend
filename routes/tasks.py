from fastapi import APIRouter, Body, HTTPException, Depends, Query, BackgroundTasks
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone
from bson import ObjectId
import re
# REMOVED raw collection imports
from models.task import TaskModel, TaskHistoryModel
from models.notification import NotificationModel
from models.user import UserModel
from routes.deps import get_current_user, get_db, get_user_verticals
from middleware.db_guard import ScopedDatabase
from logging_config import get_logger
from config import config
from utils.email import send_task_assignment_email
from utils.push import send_push_notification
from services.communication_generator import enqueue_message as enqueue_wa_message
from models.communication import TASK_ASSIGNED
from services.deliverable_sync import (
    extract_title_base, build_deliverable_title,
    on_deliverable_task_created, on_task_status_changed,
    on_task_quantity_changed, on_task_title_changed, on_task_deleted,
)
from services.task_history import log_history
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
    if isinstance(data, datetime):
        if data.tzinfo is None:
            data = data.replace(tzinfo=timezone.utc)
        return data.isoformat()
    return data

async def resolve_associate_assignment(db: ScopedDatabase, task_data: dict):
    """
    Resolve assigned_associate_id into assigned_to and incharge_user_id.
    Modifies task_data in-place. Only applies to deliverable tasks.
    Returns the associate doc if found, else None.
    """
    associate_id = task_data.get("assigned_associate_id")
    if not associate_id:
        return None

    if task_data.get("category") != "deliverable":
        raise HTTPException(status_code=400, detail="Associate assignment is only available for deliverable tasks")

    try:
        associate = await db.associates.find_one({"_id": ObjectId(associate_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid associate ID")

    if not associate:
        raise HTTPException(status_code=404, detail="Associate not found")

    task_data["assigned_associate_name"] = associate["name"]

    if associate.get("linked_user_id"):
        # Associate has a YugenHub account — assign directly, no incharge needed
        task_data["assigned_to"] = associate["linked_user_id"]
        task_data["incharge_user_id"] = None
    else:
        # Freelancer without an account — incharge is required
        incharge_id = task_data.get("incharge_user_id")
        if not incharge_id:
            raise HTTPException(status_code=400, detail="incharge_user_id is required when assigning to an associate without a YugenHub account")
        task_data["assigned_to"] = incharge_id

    return associate


# --- ENDPOINTS ---

@router.get("/grouped")
async def list_tasks_grouped(
    category: Optional[str] = None,
    has_project: Optional[bool] = None,
    priority: Optional[str] = None,
    assigned_to: Optional[str] = None,
    search: Optional[str] = None,
    project_id: Optional[str] = None,
    context: Optional[str] = Query("tasks_page"),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """Return tasks grouped by status for Kanban board view"""
    
    # Base match (RBAC + filters)
    match_stage = {}
    
    if current_user.role.lower() == 'member':
        if context == "project_page" and project_id:
            pass
        else:
            match_stage["$or"] = [
                {"assigned_to": current_user.id},
                {"incharge_user_id": current_user.id}
            ]
    elif assigned_to:
        match_stage["assigned_to"] = assigned_to

    if search:
        match_stage["title"] = {"$regex": re.escape(search), "$options": "i"}
    if project_id:
        match_stage["project_id"] = project_id
    elif has_project is True:
        match_stage["project_id"] = {"$ne": None}
    elif has_project is False:
        match_stage["project_id"] = None
    if category:
        match_stage["category"] = category
    if priority and priority != 'all':
        match_stage["priority"] = priority

    now = datetime.now(timezone.utc)
    thirty_days_ago = now - timedelta(days=30)

    # RBAC: Get user's allowed verticals for filtering project-linked tasks
    user_verticals = await get_user_verticals(current_user, db)

    pipeline = [
        {"$match": match_stage},
        # For done tasks, only include last 30 days
        {"$match": {
            "$or": [
                {"status": {"$ne": "done"}},
                {"status": "done", "updated_at": {"$gte": thirty_days_ago}}
            ]
        }},
        # Lookup project details
        {"$addFields": {"project_oid": {
            "$cond": {
                "if": {"$and": [{"$ne": ["$project_id", None]}, {"$ne": ["$project_id", ""]}]},
                "then": {"$toObjectId": "$project_id"},
                "else": None
            }
        }}},
        {"$lookup": {
            "from": "projects",
            "localField": "project_oid",
            "foreignField": "_id",
            "as": "project_info"
        }},
        # RBAC: Filter out tasks linked to verticals user can't access
        {"$match": {
            "$or": [
                {"project_info": {"$size": 0}},  # Standalone tasks (no project) are always visible
                {"project_info.vertical": {"$in": user_verticals}}  # Project's vertical is allowed
            ]
        }},
        {"$addFields": {
            "project_name": {"$arrayElemAt": ["$project_info.title", 0]},
            "project_code": {"$arrayElemAt": ["$project_info.code", 0]},
            "project_vertical": {"$arrayElemAt": ["$project_info.vertical", 0]},
            "client_name": {"$arrayElemAt": ["$project_info.metadata.client_name", 0]},
            "project_color": {"$arrayElemAt": ["$project_info.color", 0]},
            "is_overdue": {
                "$and": [
                    {"$ne": ["$due_date", None]},
                    {"$lt": ["$due_date", now]},
                    {"$ne": ["$status", "done"]}
                ]
            },
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
        }},
        {"$project": {"project_info": 0, "project_oid": 0}},
        # Sort within each group: priority desc, then due_date asc
        {"$sort": {"priority_score": -1, "due_date": 1}},
        # Group by status
        {"$group": {
            "_id": "$status",
            "tasks": {"$push": "$$ROOT"},
            "count": {"$sum": 1},
            "overdue_count": {"$sum": {"$cond": [{"$eq": ["$is_overdue", True]}, 1, 0]}}
        }}
    ]

    result = await db.tasks.aggregate(pipeline).to_list(10)
    
    # Build structured response
    statuses = ['todo', 'in_progress', 'review', 'blocked', 'done']
    groups = {}
    total = 0
    total_overdue = 0
    total_unassigned = 0
    
    for s in statuses:
        group_data = next((g for g in result if g["_id"] == s), None)
        if group_data:
            tasks = parse_mongo_data(group_data["tasks"])
            groups[s] = {
                "tasks": tasks,
                "count": group_data["count"],
                "overdue_count": group_data["overdue_count"]
            }
            total += group_data["count"]
            total_overdue += group_data["overdue_count"]
            total_unassigned += sum(1 for t in tasks if not t.get("assigned_to"))
        else:
            groups[s] = {"tasks": [], "count": 0, "overdue_count": 0}

    return {
        "groups": groups,
        "summary": {
            "total": total,
            "overdue": total_overdue,
            "unassigned": total_unassigned
        }
    }


@router.post("", status_code=201)
async def create_task(
    background_tasks: BackgroundTasks,
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

    # Resolve associate assignment (modifies task fields in-place)
    if task.assigned_associate_id:
        task_dict = task.model_dump()
        await resolve_associate_assignment(db, task_dict)
        task = TaskModel(**task_dict)

    # Build canonical title for event-linked deliverable tasks
    if task.category == "deliverable" and task.event_id and task.project_id:
        project_doc = await db.projects.find_one(
            {"_id": ObjectId(task.project_id)},
            {"events": 1}
        )
        if project_doc:
            event = next((e for e in project_doc.get("events", []) if e.get("id") == task.event_id), None)
            if event and event.get("type"):
                task.deliverable_type = task.title  # preserve type before name substitution overwrites it
                display = task.name or task.title
                task.title = build_deliverable_title(display, event["type"])

    result = await db.tasks.insert_one(task.model_dump())
    created_task = await db.tasks.find_one({"_id": result.inserted_id})

    # Log creation
    await log_history(db, task.id, current_user.id, {"creation": (None, "Task Created")})

    # Auto-create portal deliverables for deliverable tasks
    if task.category == "deliverable" and task.project_id:
        await on_deliverable_task_created(db, task.model_dump(), task.project_id)
    
    # --- NOTIFICATION LOGIC (CREATE) ---
    if task.assigned_to and task.assigned_to != current_user.id:
        # 1. Get Project Details if exists
        project_title = None
        project = None
        if task.project_id:
            try:
                project = await db.projects.find_one({"_id": ObjectId(task.project_id)})
            except Exception:
                project = await db.projects.find_one({"id": task.project_id})
            if project:
                project_title = project.get("code", "")

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
        # 4. Trigger Email
        try:
            assignee_data = await db.users.find_one({"id": task.assigned_to})
            if assignee_data and assignee_data.get("email"):
                org_config = await db.agency_configs.find_one({})
                org_name = org_config.get("org_name", "My Agency") if org_config else "My Agency"
                background_tasks.add_task(
                    send_task_assignment_email,
                    to_email=assignee_data["email"],
                    org_name=org_name,
                    task_title=task.title,
                    assigner_name=assigner_name,
                    project_title=project_title,
                    due_date=task.due_date,
                    frontend_url=config.FRONTEND_URL
                )
        except Exception as e:
            logger.error(f"Failed to queue task assignment email: {e}")
            
        # 5. Send Push Notification
        background_tasks.add_task(
            send_push_notification,
            db=db,
            user_id=task.assigned_to,
            title="New Assignment",
            message=message,
            url=f"/tasks?taskId={task.id}"
        )

        # 6. WhatsApp — notify the project's client
        if task.project_id and project:
            client_id = project.get("client_id")
            if client_id:
                org_config_wa = await db.agency_configs.find_one({})
                agency_name_wa = (org_config_wa or {}).get("org_name", "")
                background_tasks.add_task(
                    enqueue_wa_message,
                    db=db,
                    agency_id=current_user.agency_id,
                    alert_type=TASK_ASSIGNED,
                    recipient_client_id=client_id,
                    source={"kind": "task", "id": task.id},
                    render_ctx={
                        "task_title": task.title,
                        "project_code": project.get("code", ""),
                        "due_date": task.due_date,
                        "agency_name": agency_name_wa,
                    },
                )

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
    limit: int = Query(100, le=1000),
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
            match_stage["$or"] = [
                {"assigned_to": current_user.id},
                {"incharge_user_id": current_user.id}
            ]
    elif assigned_to:
        # Only allow filtering by assigned_to if NOT a member (Admins/Owners)
        match_stage["assigned_to"] = assigned_to

    if search:
        match_stage["title"] = {"$regex": re.escape(search), "$options": "i"}
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
            "project_oid": {
            "$cond": {
                "if": {"$and": [{"$ne": ["$project_id", None]}, {"$ne": ["$project_id", ""]}]},
                "then": {"$toObjectId": "$project_id"},
                "else": None
            }
        }
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

    # RBAC: Filter out tasks linked to verticals user can't access
    user_verticals = await get_user_verticals(current_user, db)
    pipeline.append({
        "$match": {
            "$or": [
                {"project_info": {"$size": 0}},  # Standalone tasks always visible
                {"project_info.vertical": {"$in": user_verticals}}
            ]
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
    background_tasks: BackgroundTasks,
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
    is_incharge = existing_task.get("incharge_user_id") == current_user.id

    if not (is_owner_admin or is_assignee or is_incharge):
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

    # Resolve associate assignment when being updated
    if "assigned_associate_id" in update_data:
        new_associate_id = update_data["assigned_associate_id"]
        if new_associate_id:
            # Merge with existing task to provide category context for validation
            merged = {**existing_task, **update_data}
            await resolve_associate_assignment(db, merged)
            # Inject resolved fields back into update_data
            update_data["assigned_to"] = merged["assigned_to"]
            update_data["assigned_associate_name"] = merged["assigned_associate_name"]
            update_data["incharge_user_id"] = merged.get("incharge_user_id")
            # Reconcile assigned_to in changes: pre-resolve computation may have captured
            # a false change (frontend sends assigned_to:null in associate mode).
            # After resolution we know the actual new value, so correct changes accordingly.
            old_assigned_to = existing_task.get("assigned_to")
            new_assigned_to = merged["assigned_to"]
            if old_assigned_to != new_assigned_to:
                changes["assigned_to"] = (old_assigned_to, new_assigned_to)
            elif "assigned_to" in changes:
                del changes["assigned_to"]  # Remove false entry from pre-resolve null payload
        else:
            # Clearing associate — also clear related fields
            update_data["assigned_associate_name"] = None
            update_data["incharge_user_id"] = None

    # Regenerate canonical title for deliverable tasks when name or type changes
    if (existing_task.get("category") == "deliverable"
            and existing_task.get("event_id")
            and existing_task.get("project_id")
            and ("name" in update_data or "title" in update_data)):
        project_doc = await db.projects.find_one(
            {"_id": ObjectId(existing_task["project_id"])},
            {"events": 1}
        )
        if project_doc:
            event = next(
                (e for e in project_doc.get("events", []) if e.get("id") == existing_task["event_id"]),
                None
            )
            if event and event.get("type"):
                new_name = update_data.get("name") if "name" in update_data else existing_task.get("name")
                # "title" from the frontend is the deliverable type; fall back to stored deliverable_type
                if "title" in update_data:
                    new_type_base = update_data["title"]
                else:
                    new_type_base = existing_task.get("deliverable_type") or extract_title_base(existing_task.get("title", ""))
                display = new_name if new_name else new_type_base
                update_data["title"] = build_deliverable_title(display, event["type"])
                update_data["deliverable_type"] = new_type_base  # keep type field in sync
                if "title" not in changes or changes["title"][1] != update_data["title"]:
                    changes["title"] = (existing_task.get("title"), update_data["title"])

    # Update DB — only write valid model fields to prevent arbitrary field injection
    filtered_update = {k: v for k, v in update_data.items() if k in valid_fields}
    # Ensure due_date is stored as a BSON Date, not a string. If stored as a string,
    # MongoDB's $lt comparisons with a Date value always return true (string type sorts
    # before Date type in BSON order), causing all such tasks to appear overdue.
    if "due_date" in filtered_update and isinstance(filtered_update["due_date"], str):
        try:
            filtered_update["due_date"] = datetime.fromisoformat(filtered_update["due_date"])
        except (ValueError, TypeError):
            filtered_update["due_date"] = None
    filtered_update["updated_at"] = datetime.now(timezone.utc)
    await db.tasks.update_one(
        {"id": task_id},
        {"$set": filtered_update}
    )
    
    # Log History
    await log_history(db, task_id, current_user.id, changes, comment)

    # --- DELIVERABLE SYNC ---
    if existing_task.get("category") == "deliverable":
        updated_task = await db.tasks.find_one({"id": task_id})
        if "status" in changes:
            old_s, new_s = changes["status"]
            await on_task_status_changed(db, updated_task, old_s, new_s)
        if "quantity" in changes:
            old_q, new_q = changes["quantity"]
            project_id = existing_task.get("project_id")
            if project_id:
                await on_task_quantity_changed(db, updated_task, old_q, new_q, project_id)
        if "title" in changes and existing_task.get("project_id"):
            new_base = updated_task.get("name") or extract_title_base(updated_task.get("title", ""))
            await on_task_title_changed(db, updated_task, new_base, existing_task["project_id"])

    # --- NOTIFICATION LOGIC (UPDATE) ---
    if "assigned_to" in changes:
        old_assignee, new_assignee = changes["assigned_to"]
        if new_assignee and new_assignee != current_user.id:
            # Determine latest title
            current_title = update_data.get("title") or existing_task.get("title", "Untitled Task")
            
            # 1. Get Project Details
            project_id = existing_task.get("project_id")
            project_title = None
            project = None
            if project_id:
                try:
                    project = await db.projects.find_one({"_id": ObjectId(project_id)})
                except Exception:
                    project = await db.projects.find_one({"id": project_id})
                if project:
                    project_title = project.get("code", "")

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
            # 4. Trigger Email
            try:
                assignee_data = await db.users.find_one({"id": new_assignee})
                if assignee_data and assignee_data.get("email"):
                    org_config = await db.agency_configs.find_one({})
                    org_name = org_config.get("org_name", "My Agency") if org_config else "My Agency"
                    background_tasks.add_task(
                        send_task_assignment_email,
                        to_email=assignee_data["email"],
                        org_name=org_name,
                        task_title=current_title,
                        assigner_name=assigner_name,
                        project_title=project_title,
                        due_date=due_date,
                        frontend_url=config.FRONTEND_URL
                    )
            except Exception as e:
                logger.error(f"Failed to queue task reassignment email: {e}")
                
            # 5. Send Push Notification
            background_tasks.add_task(
                send_push_notification,
                db=db,
                user_id=new_assignee,
                title="New Task Assigned",
                message=message,
                url=f"/tasks?taskId={task_id}"
            )

            # 6. WhatsApp — notify project client on reassignment
            reassign_project_id = existing_task.get("project_id")
            if reassign_project_id:
                try:
                    reassign_project = await db.projects.find_one({"_id": ObjectId(reassign_project_id)})
                except Exception:
                    reassign_project = await db.projects.find_one({"id": reassign_project_id})
                if reassign_project:
                    reassign_client_id = reassign_project.get("client_id")
                    if reassign_client_id:
                        org_cfg_wa = await db.agency_configs.find_one({})
                        background_tasks.add_task(
                            enqueue_wa_message,
                            db=db,
                            agency_id=current_user.agency_id,
                            alert_type=TASK_ASSIGNED,
                            recipient_client_id=reassign_client_id,
                            source={"kind": "task", "id": task_id},
                            render_ctx={
                                "task_title": current_title,
                                "project_code": reassign_project.get("code", ""),
                                "due_date": due_date,
                                "agency_name": (org_cfg_wa or {}).get("org_name", ""),
                            },
                        )

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

    # Fetch before deletion for sync cleanup
    existing_task = await db.tasks.find_one({"id": task_id})
    if not existing_task:
        raise HTTPException(status_code=404, detail="Task not found")

    result = await db.tasks.delete_one({"id": task_id})
    if result.deleted_count == 0:
        logger.warning(f"Task deletion failed: not found", extra={"data": {"task_id": task_id}})
        raise HTTPException(status_code=404, detail="Task not found")

    # Clean up linked portal deliverables
    if existing_task.get("category") == "deliverable":
        await on_task_deleted(db, existing_task)

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
