from fastapi import APIRouter, Depends
from models.user import UserModel
from database import (
    projects_collection, 
    tasks_collection, 
    task_history_collection,
    users_collection,
    clients_collection,
    associates_collection
)
from routes.deps import get_current_user
from datetime import datetime, timedelta
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
        if isinstance(data, datetime):
            return data.isoformat()
        return {k: parse_mongo_data(v) for k, v in data.items()}
    return data


@router.get("/stats")
async def get_dashboard_stats(current_user: UserModel = Depends(get_current_user)):
    """Enriched stats for both Admin and Member views"""
    current_agency_id = current_user.agency_id
    now = datetime.now()
    
    # Base Stats (Global)
    active_projects = await projects_collection.count_documents({
        "agency_id": current_agency_id,
        "status": {"$ne": "completed"}
    })
    
    # Personal Stats (For Member View)
    my_tasks_due_today = await tasks_collection.count_documents({
        "studio_id": current_agency_id,
        "assigned_to": current_user.id,
        "status": {"$ne": "done"},
        "due_date": {
            "$gte": datetime(now.year, now.month, now.day),
            "$lt": datetime(now.year, now.month, now.day) + timedelta(days=1)
        }
    })
    
    my_upcoming_events_pipeline = [
        {"$match": {"agency_id": current_agency_id}},
        {"$unwind": "$events"},
        {"$match": {
            "events.start_date": {"$gte": now},
            "events.assignments.associate_id": {"$exists": True} # Simplified check
            # In a real scenario, we'd filter by associate_id matching user, 
            # but user->associate link is loose. For now, just count future events?
            # Actually, let's keep it simple: Count ALL future events for Admin, 
            # and later we filter in /schedule for Members.
        }},
        {"$count": "count"}
    ]
    
    return {
        "active_projects": active_projects,
        "my_tasks_due_today": my_tasks_due_today
    }

@router.get("/attention")
async def get_attention_items(scope: str = "global", current_user: UserModel = Depends(get_current_user)):
    """Status checks for Overdue, Blocked, and Risk items"""
    current_agency_id = current_user.agency_id
    now = datetime.now()
    items = []
    
    # Filter base
    task_query = {
        "studio_id": current_agency_id, 
        "status": {"$ne": "done"}
    }
    if scope == "me":
        task_query["assigned_to"] = current_user.id
        
    # 1. Overdue Deliverables/Tasks
    overdue_query = {**task_query, "due_date": {"$lt": now}}
    overdue_tasks = await tasks_collection.find(overdue_query).sort("due_date", 1).limit(5).to_list(None)
    
    for t in overdue_tasks:
        items.append({
            "type": "task",
            "id": t.get("id"),
            "title": t.get("title"),
            "reason": f"Overdue by {(now - t.get('due_date')).days} days",
            "priority": "high",
            "link": "/tasks" 
        })
        
    # 2. Blocked Tasks
    blocked_query = {**task_query, "status": "blocked"}
    blocked_tasks = await tasks_collection.find(blocked_query).limit(5).to_list(None)
    
    for t in blocked_tasks:
        items.append({
            "type": "task",
            "id": t.get("id"),
            "title": t.get("title"),
            "reason": "Blocked",
            "priority": "medium",
            "link": "/tasks"
        })

    # 3. Risk Events (Global Only for now, complexity reduction)
    if scope == "global":
        next_week = now + timedelta(days=7)
        risk_pipeline = [
            {"$match": {"agency_id": current_agency_id}},
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
        risk_events = await projects_collection.aggregate(risk_pipeline).to_list(None)
        
        for e in risk_events:
            reason = []
            if not e.get("assignments"): reason.append("No Team")
            if not e.get("deliverables"): reason.append("No Deliverables")
            
            items.append({
                "type": "event",
                "id": str(e.get("_id") or "unknown"),
                "title": f"{e.get('code')} - {e.get('type')}",
                "reason": ", ".join(reason),
                "priority": "high",
                "link": f"/projects/{e.get('code')}"
            })
            
    # Sort by priority (high first) and limit
    priority_map = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
    items.sort(key=lambda x: priority_map.get(x.get("priority"), 99))
    
    return items[:6]

@router.get("/workload")
async def get_workload_stats(scope: str = "global", current_user: UserModel = Depends(get_current_user)):
    """Workload intelligence"""
    current_agency_id = current_user.agency_id
    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day)
    week_end = today_start + timedelta(days=7)
    
    if scope == "me":
        # Personal Stats
        due_today = await tasks_collection.count_documents({
            "studio_id": current_agency_id, "assigned_to": current_user.id, "status": {"$ne": "done"},
            "due_date": {"$gte": today_start, "$lt": today_start + timedelta(days=1)}
        })
        due_week = await tasks_collection.count_documents({
            "studio_id": current_agency_id, "assigned_to": current_user.id, "status": {"$ne": "done"},
            "due_date": {"$gte": today_start, "$lte": week_end}
        })
        overdue = await tasks_collection.count_documents({
            "studio_id": current_agency_id, "assigned_to": current_user.id, "status": {"$ne": "done"},
            "due_date": {"$lt": now}
        })
        return {"due_today": due_today, "due_week": due_week, "overdue": overdue}
        
    else:
        # Team Load (Admin only)
        # Find users with high load
        users = await users_collection.find({"agency_id": current_agency_id}).to_list(None)
        user_ids = [u.get("id") for u in users]
        
        pipeline = [
            {"$match": {
                "studio_id": current_agency_id,
                "assigned_to": {"$in": user_ids},
                "status": {"$ne": "done"}
            }},
            {"$group": {
                "_id": "$assigned_to",
                "urgent_count": {"$sum": {"$cond": [{"$eq": ["$priority", "urgent"]}, 1, 0]}},
                "overdue_count": {"$sum": {"$cond": [{"$lt": ["$due_date", now]}, 1, 0]}},
                "total_count": {"$sum": 1}
            }}
        ]
        
        load_stats = await tasks_collection.aggregate(pipeline).to_list(None)
        load_map = {stat["_id"]: stat for stat in load_stats}
        
        alerts = []
        for u in users:
            uid = u.get("id")
            stats = load_map.get(uid, {})
            urgent = stats.get("urgent_count", 0)
            overdue = stats.get("overdue_count", 0)
            
            if urgent >= 3 or overdue >= 1:
                summary = []
                if urgent >= 3: summary.append(f"{urgent} Urgent Tasks")
                if overdue >= 1: summary.append(f"{overdue} Overdue")
                
                alerts.append({
                    "user_name": u.get("name"),
                    "role": u.get("role"),
                    "overload_summary": ", ".join(summary)
                })
                
        return alerts


@router.get("/pipeline")
async def get_project_pipeline(current_user: UserModel = Depends(get_current_user)):
    """Get active project distribution by Vertical"""
    pipeline = [
        {"$match": {
            "agency_id": current_user.agency_id,
            "status": {"$ne": "completed"} # Exclude completed
        }},
        {"$group": {"_id": "$vertical", "count": {"$sum": 1}}}
    ]
    results = await projects_collection.aggregate(pipeline).to_list(None)
    # Format: [{"name": "Weddings", "value": 5}, ...]
    # Normalize ID if null
    return [{"name": (r["_id"] or "Other").title(), "value": r["count"]} for r in results]

@router.get("/schedule")
async def get_upcoming_schedule(current_user: UserModel = Depends(get_current_user)):
    """Get upcoming events list with Client and Team details"""
    current_agency_id = current_user.agency_id
    now = datetime.now()
    next_2_weeks = now + timedelta(days=14) 
    
    pipeline = [
        {"$match": {"agency_id": current_agency_id}},
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
            "location": "$events.location",
            "assignments": "$events.assignments",
            "project_code": "$code",
            "description": "$events.description",
            "client_id": "$client_id"
        }}
    ]
    
    events = await projects_collection.aggregate(pipeline).to_list(10)
    
    # Collect IDs for batch fetch
    client_ids = []
    associate_ids = []
    
    for event in events:
        if event.get("client_id"):
            try:
                client_ids.append(ObjectId(event["client_id"]))
            except:
                pass
        for assign in event.get("assignments", []):
            if assign.get("associate_id"):
                associate_ids.append(assign["associate_id"])
                
    client_ids = list(set(client_ids))
    associate_ids = list(set(associate_ids))
    
    # Fetch Clients
    clients = await clients_collection.find({"_id": {"$in": client_ids}}).to_list(None)
    client_map = {str(c["_id"]): c.get("name", "Unknown Client") for c in clients}
    
    # Fetch Associates
    associate_oids = []
    for aid in associate_ids:
        try:
            associate_oids.append(ObjectId(aid))
        except:
            pass
            
    associates = await associates_collection.find({"_id": {"$in": associate_oids}}).to_list(None)
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
async def get_recent_activity(limit: int = 10, current_user: UserModel = Depends(get_current_user)):
    """Get recent activity logs with titles and user names"""
    cursor = task_history_collection.find().sort("timestamp", -1).limit(limit)
    logs = await cursor.to_list(length=limit)
    
    # Enrich with task titles and user names
    task_ids = list(set([log.get("task_id") for log in logs if log.get("task_id")]))
    user_ids = list(set([log.get("changed_by") for log in logs if log.get("changed_by")]))
    
    tasks = await tasks_collection.find({"id": {"$in": task_ids}}).to_list(None)
    users = await users_collection.find({"id": {"$in": user_ids}}).to_list(None)
    
    task_map = {t["id"]: t.get("title", "Unknown Task") for t in tasks}
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
            
        activity.append({
            "id": str(log["_id"]),
            "user_name": user_map.get(u_id, "System"),
            "task_title": task_map.get(t_id, t_id),
            "action": action_text,
            "timestamp": log.get("timestamp")
        })
        
    return activity
