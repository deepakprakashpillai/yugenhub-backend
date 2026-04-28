from fastapi import APIRouter, Depends
from models.user import UserModel
# REMOVED raw collection imports
from routes.deps import get_current_user, get_db, get_user_verticals
from middleware.db_guard import ScopedDatabase
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from logging_config import get_logger

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])
logger = get_logger("dashboard")

# Helper to parse objectid
def parse_mongo_data(data):
    if isinstance(data, list):
        return [parse_mongo_data(item) for item in data]
    if isinstance(data, dict):
        if "_id" in data:
            data["_id"] = str(data["_id"])
        if "project_id" in data:
            data["project_id"] = str(data["project_id"])
        return {k: parse_mongo_data(v) for k, v in data.items()}
    if isinstance(data, datetime):
        if data.tzinfo is None:
            data = data.replace(tzinfo=timezone.utc)
        return data.isoformat()
    return data


@router.get("/stats")
async def get_dashboard_stats(current_user: UserModel = Depends(get_current_user), db: ScopedDatabase = Depends(get_db)):
    """Enriched stats for both Admin and Member views"""
    # current_agency_id handled by db wrapper
    now = datetime.now(timezone.utc)
    
    # RBAC: Scope to user's allowed verticals
    user_verticals = await get_user_verticals(current_user, db)
    vertical_filter = {"vertical": {"$in": user_verticals}}
    
    # Base Stats (Scoped by verticals)
    active_projects = await db.projects.count_documents({
        **vertical_filter,
        "status": {"$ne": "completed"}
    })
    
    # Personal Stats (For Member View)
    my_tasks_due_today = await db.tasks.count_documents({
        "assigned_to": current_user.id,
        "status": {"$ne": "done"},
        "due_date": {
            "$gte": now.replace(hour=0, minute=0, second=0, microsecond=0),
            "$lt": now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        }
    })
    
    # ScopedDB.aggregate PREPENDS standard match.
    # We just need the rest of pipeline.
    my_upcoming_events_pipeline = [
        {"$unwind": "$events"},
        {"$match": {
            "events.start_date": {"$gte": now},
            "events.assignments.associate_id": {"$exists": True} 
        }},
        {"$count": "count"}
    ]
    
    return {
        "active_projects": active_projects,
        "my_tasks_due_today": my_tasks_due_today
    }

@router.get("/attention")
async def get_attention_items(scope: str = "global", current_user: UserModel = Depends(get_current_user), db: ScopedDatabase = Depends(get_db)):
    """Status checks for Overdue, Blocked, and Risk items"""
    now = datetime.now(timezone.utc)
    items = []
    
    # Filter base
    task_query = {
        "status": {"$ne": "done"}
    }
    if scope == "me":
        task_query["assigned_to"] = current_user.id
        
    # 1. Overdue Deliverables/Tasks
    overdue_query = {**task_query, "due_date": {"$lt": now}}
    overdue_tasks = await db.tasks.find(overdue_query).sort("due_date", 1).limit(5).to_list(None)
    
    for t in overdue_tasks:
        project_id = t.get("project_id")
        due = t.get("due_date")
        if due and due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        days_overdue = (now - due).days if due else 0
        items.append({
            "type": "task",
            "id": t.get("id"),
            "title": t.get("title"),
            "reason": f"Overdue by {days_overdue} day{'s' if days_overdue != 1 else ''}",
            "priority": "high",
            "link": f"/projects/{project_id}" if project_id else "/tasks"
        })

    # 2. Blocked Tasks
    blocked_query = {**task_query, "status": "blocked"}
    blocked_tasks = await db.tasks.find(blocked_query).limit(5).to_list(None)

    for t in blocked_tasks:
        project_id = t.get("project_id")
        items.append({
            "type": "task",
            "id": t.get("id"),
            "title": t.get("title"),
            "reason": "Blocked",
            "priority": "medium",
            "link": f"/projects/{project_id}" if project_id else "/tasks"
        })

    # 3. Risk Events (Global Only for now, complexity reduction)
    if scope == "global":
        next_week = now + timedelta(days=7)
        risk_pipeline = [
            {"$unwind": "$events"},
            {"$match": {
                "events.start_date": {"$gte": now, "$lte": next_week},
                "$or": [
                    {"events.assignments": {"$size": 0}},
                    {"events.deliverables": {"$size": 0}}
                ]
            }},
            {"$limit": 5},
            {"$project": {
                "type": "$events.type",
                "start_date": "$events.start_date",
                "code": "$code",
                "assignments": "$events.assignments",
                "deliverables": "$events.deliverables"
            }}
        ]
        risk_events = await db.projects.aggregate(risk_pipeline).to_list(None)
        
        for e in risk_events:
            reason = []
            if not e.get("assignments"): reason.append("No Team")
            if not e.get("deliverables"): reason.append("No Deliverables")
            project_id = str(e.get("_id")) if e.get("_id") else None
            items.append({
                "type": "event",
                "id": project_id or "unknown",
                "title": f"{e.get('code')} - {e.get('type')}",
                "reason": ", ".join(reason),
                "priority": "high",
                "link": f"/projects/{project_id}" if project_id else "/calendar"
            })
            
    # Sort by priority (high first) and limit
    priority_map = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
    items.sort(key=lambda x: priority_map.get(x.get("priority"), 99))
    
    return items[:6]

@router.get("/workload")
async def get_workload_stats(scope: str = "global", current_user: UserModel = Depends(get_current_user), db: ScopedDatabase = Depends(get_db)):
    """Workload intelligence"""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = today_start + timedelta(days=7)
    
    if scope == "me":
        # Personal Stats
        due_today = await db.tasks.count_documents({
            "assigned_to": current_user.id, "status": {"$ne": "done"},
            "due_date": {"$gte": today_start, "$lt": today_start + timedelta(days=1)}
        })
        due_week = await db.tasks.count_documents({
            "assigned_to": current_user.id, "status": {"$ne": "done"},
            "due_date": {"$gte": today_start, "$lte": week_end}
        })
        overdue = await db.tasks.count_documents({
            "assigned_to": current_user.id, "status": {"$ne": "done"},
            "due_date": {"$lt": now}
        })
        return {"due_today": due_today, "due_week": due_week, "overdue": overdue}
        
    else:
        # Team Load — top 6 users by workload with full task details
        users = await db.users.find({}).to_list(None)
        user_ids = [u.get("id") for u in users]
        user_map = {u.get("id"): u for u in users}

        tasks = await db.tasks.find({
            "assigned_to": {"$in": user_ids},
            "status": {"$ne": "done"}
        }).to_list(None)

        # Collect unique project_ids to resolve names
        project_ids = list({t.get("project_id") for t in tasks if t.get("project_id")})
        projects_raw = await db.projects.find({"id": {"$in": project_ids}}).to_list(None)
        project_map = {}
        for p in projects_raw:
            pid = p.get("id")
            project_map[pid] = {
                "code": p.get("code", ""),
                "name": p.get("metadata", {}).get("project_type", p.get("code", "Project"))
            }

        # Group tasks by user
        user_tasks: dict = {uid: [] for uid in user_ids}
        for t in tasks:
            uid = t.get("assigned_to")
            if uid in user_tasks:
                user_tasks[uid].append(t)

        result = []
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)

        for uid, utasks in user_tasks.items():
            if not utasks:
                continue
            u = user_map.get(uid, {})

            overdue_count = 0
            urgent_count = 0
            in_progress_count = 0
            due_today_count = 0
            task_list = []

            def _sort_key(x):
                d = x.get("due_date")
                if d:
                    aware = d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
                    return (0 if aware < now else 1, aware)
                return (1, datetime.max.replace(tzinfo=timezone.utc))

            for t in sorted(utasks, key=_sort_key):
                due = t.get("due_date")
                if due:
                    if due.tzinfo is None:
                        due = due.replace(tzinfo=timezone.utc)
                    is_overdue = due < now
                    is_due_today = today_start <= due < today_end
                else:
                    is_overdue = False
                    is_due_today = False

                if is_overdue:
                    overdue_count += 1
                if t.get("priority") == "urgent":
                    urgent_count += 1
                if t.get("status") == "in_progress":
                    in_progress_count += 1
                if is_due_today:
                    due_today_count += 1

                proj = project_map.get(t.get("project_id"), {})
                task_list.append({
                    "id": t.get("id"),
                    "title": t.get("title", ""),
                    "project_id": t.get("project_id"),
                    "project_code": proj.get("code", ""),
                    "project_name": proj.get("name", ""),
                    "due_date": parse_mongo_data(due) if due else None,
                    "priority": t.get("priority", "medium"),
                    "status": t.get("status", "todo"),
                    "type": t.get("type", "internal"),
                    "is_overdue": is_overdue,
                })

            load_score = overdue_count * 3 + urgent_count * 2 + len(utasks)
            result.append({
                "user_id": uid,
                "user_name": u.get("name", "Unknown"),
                "role": u.get("role", "member"),
                "stats": {
                    "total": len(utasks),
                    "overdue": overdue_count,
                    "urgent": urgent_count,
                    "in_progress": in_progress_count,
                    "due_today": due_today_count,
                },
                "tasks": task_list,
                "load_score": load_score,
            })

        result.sort(key=lambda x: x["load_score"], reverse=True)
        for r in result:
            del r["load_score"]
        return result[:6]


@router.get("/pipeline")
async def get_project_pipeline(current_user: UserModel = Depends(get_current_user), db: ScopedDatabase = Depends(get_db)):
    """Get active project distribution by Vertical"""
    # RBAC: Scope to user's allowed verticals
    user_verticals = await get_user_verticals(current_user, db)
    pipeline = [
        {"$match": {
            "vertical": {"$in": user_verticals},
            "status": {"$ne": "completed"}
        }},
        {"$group": {"_id": "$vertical", "count": {"$sum": 1}}}
    ]
    results = await db.projects.aggregate(pipeline).to_list(None)
    # Format: [{"name": "Weddings", "value": 5}, ...]
    # Normalize ID if null
    return [{"id": r["_id"] or "other", "name": (r["_id"] or "Other").title(), "value": r["count"]} for r in results]

@router.get("/schedule")
async def get_upcoming_schedule(current_user: UserModel = Depends(get_current_user), db: ScopedDatabase = Depends(get_db)):
    """Get upcoming events list with Client and Team details"""
    now = datetime.now(timezone.utc)
    next_2_weeks = now + timedelta(days=14) 
    
    # RBAC: Scope to user's allowed verticals
    user_verticals = await get_user_verticals(current_user, db)
    
    pipeline = [
        {"$match": {"vertical": {"$in": user_verticals}}},
        {"$unwind": "$events"},
        {"$match": {
            "events.start_date": {"$gte": now, "$lte": next_2_weeks}
        }},
        {"$sort": {"events.start_date": 1}},
        {"$limit": 10},
        {"$project": {
            "type": "$events.type",
            "start_date": "$events.start_date",
            "end_date": "$events.end_date",
            "venue_name": "$events.venue_name",
            "venue_location": "$events.venue_location",
            "assignments": "$events.assignments",
            "project_code": "$code",
            "project_id": {"$toString": "$_id"},
            "description": "$events.description",
            "client_id": "$client_id"
        }}
    ]
    
    events = await db.projects.aggregate(pipeline).to_list(10)
    
    # Collect IDs for batch fetch
    client_ids = []
    associate_ids = []
    
    for event in events:
        if event.get("client_id"):
            try:
                client_ids.append(ObjectId(event["client_id"]))
            except Exception:
                pass
        for assign in event.get("assignments", []):
            if assign.get("associate_id"):
                associate_ids.append(assign["associate_id"])
                
    client_ids = list(set(client_ids))
    associate_ids = list(set(associate_ids))
    
    # Fetch Clients
    clients = await db.clients.find({"_id": {"$in": client_ids}}).to_list(None)
    client_map = {str(c["_id"]): c.get("name", "Unknown Client") for c in clients}
    
    # Fetch Associates
    associate_oids = []
    for aid in associate_ids:
        try:
            associate_oids.append(ObjectId(aid))
        except Exception:
            pass
            
    associates = await db.associates.find({"_id": {"$in": associate_oids}}).to_list(None)
    associate_map = {str(a["_id"]): a.get("name", "Unknown Associate") for a in associates}
    
    # Enrich Events
    for event in events:
        if "client_id" in event:
             event["client_name"] = client_map.get(str(event["client_id"]), "")
             
        enriched_assignments = []
        for assignment in event.get("assignments", []):
             a_id = assignment.get("associate_id")
             enriched_assignments.append({
                 "role": assignment.get("role"),
                 "associate_name": associate_map.get(str(a_id), "Unknown")
             })
        event["assignment_details"] = enriched_assignments

    return parse_mongo_data(events)

@router.get("/activity")
async def get_recent_activity(limit: int = 10, current_user: UserModel = Depends(get_current_user), db: ScopedDatabase = Depends(get_db)):
    """Get recent activity logs with titles and user names"""
    # 1. Get all task IDs for this agency to filter history
    # ScopedDB enforces filtered list here.
    agency_tasks = await db.tasks.find(
        {}, 
        {"id": 1}
    ).to_list(None)
    
    if not agency_tasks:
        return []
        
    agency_task_ids = [t["id"] for t in agency_tasks]
    
    # 2. Query history for these tasks only
    # db.task_history adds default filter.
    # In my middleware I set ScopedCollection to use 'studio_id' for task_history.
    # However, Task History also has 'task_id'.
    # Filtering by 'task_id' that belongs to agency is good, but filtering by 'studio_id' is better (direct).
    # Since we added 'studio_id' to TaskHistoryModel, future logs will have it. 
    # OLD logs might not.
    # So we MUST keep the task_id IN filter for backwards compatibility for a bit, OR just rely on studio_id if populated.
    # BUT wait: middleware ALWAYS injects studio_id.
    # IF old logs don't have studio_id, they will NOT be returned by ScopedDatabase(..., studio_id=...).find()
    # PREVIOUSLY we didn't have studio_id in history.
    # So for OLD logs, we might miss them if we force studio_id filter.
    
    # FIX: middleware injection is strict. If data doesn't have the field, it won't match.
    # This means OLD history logs (without studio_id) will VANISH from the dashboard.
    # This is acceptable for "Security Guardrails" - fail closed. 
    # Users will lose visibility of old history but new history is secure.
    
    # However, if we want to show old history, we'd need to bypass scoped DB or backfill.
    # Given the instructions, we should stick to the secure implementation.
    
    cursor = db.task_history.find(
        {"task_id": {"$in": agency_task_ids}}
    ).sort("timestamp", -1).limit(limit)
    
    logs = await cursor.to_list(length=limit)
    
    # Enrich with task titles and user names
    task_ids = list(set([log.get("task_id") for log in logs if log.get("task_id")]))
    user_ids = list(set([log.get("changed_by") for log in logs if log.get("changed_by")]))
    
    tasks = await db.tasks.find({"id": {"$in": task_ids}}).to_list(None)
    users = await db.users.find({"id": {"$in": user_ids}}).to_list(None)
    
    task_map = {t["id"]: t.get("title", "Unknown Task") for t in tasks}
    task_project_map = {t["id"]: t.get("project_id") for t in tasks}
    user_map = {u["id"]: u.get("name", "Unknown User") for u in users}
    
    # Transform for frontend
    activity = []
    for log in logs:
        t_id = log.get("task_id")
        u_id = log.get("changed_by")
        
        # Determine strict action type
        field = log.get("field")
        old_val = log.get("old_value")
        new_val = log.get("new_value")
        
        action_text = f"Updated {field}"
        if field == "status":
            action_text = f"Changed status to {new_val}"
        elif field == "assigned_to":
            action_text = "Updated assignment"
            
        timestamp = log.get("timestamp")
        if isinstance(timestamp, datetime) and timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
            
        project_id = task_project_map.get(t_id)
        activity.append({
            "id": str(log["_id"]),
            "user_name": user_map.get(u_id, "System"),
            "task_id": t_id,
            "task_title": task_map.get(t_id, t_id),
            "project_id": project_id,
            "action": action_text,
            "timestamp": timestamp
        })
        
    return activity
