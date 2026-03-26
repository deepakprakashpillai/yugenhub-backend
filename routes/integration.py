# routes/integration.py
# Read-only API endpoints for n8n and external integrations.
# Authenticated via API key (X-API-Key header), scoped per agency_id query param.

import re
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional
from datetime import datetime, timezone
from bson import ObjectId
from middleware.db_guard import ScopedDatabase
from routes.deps import get_integration_db
from logging_config import get_logger

router = APIRouter(prefix="/api/integration", tags=["Integration"])
logger = get_logger("integration")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _clean_id(doc: dict) -> dict:
    """Convert MongoDB _id to string."""
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


def _clean_list(docs: list) -> list:
    return [_clean_id(d) for d in docs]


def _build_search_or(search: str) -> list[dict]:
    """Build the $or conditions for searching across project metadata fields.
    Centralised to avoid duplicating these 8 conditions in multiple places."""
    safe = re.escape(search)
    return [
        {"metadata.client_name": {"$regex": safe, "$options": "i"}},
        {"code": {"$regex": safe, "$options": "i"}},
        {"metadata.groom_name": {"$regex": safe, "$options": "i"}},
        {"metadata.bride_name": {"$regex": safe, "$options": "i"}},
        {"metadata.child_name": {"$regex": safe, "$options": "i"}},
        {"metadata.event_name": {"$regex": safe, "$options": "i"}},
        {"metadata.company_name": {"$regex": safe, "$options": "i"}},
        {"events.type": {"$regex": safe, "$options": "i"}},
    ]


def _normalise_code(raw: str) -> str:
    """Best-effort normalisation of project codes.

    Users (and the LLM) often type codes with spaces or missing dashes:
      'KN 2026 1234'  -> 'KN-2026-1234'
      'kn-2026-0001'  -> 'KN-2026-0001'
      'KN20260001'    -> 'KN-2026-0001'  (continuous 10-char form)

    If the input already contains dashes it is just uppercased.
    """
    s = raw.strip().upper()
    if "-" in s:
        return s
    # Space-separated: 'KN 2026 1234'
    parts = s.split()
    if len(parts) == 3:
        prefix, year, seq = parts
        return f"{prefix}-{year}-{seq.zfill(4)}"
    # Continuous form: 'KN20260001' (2 letter + 4 year + 4 seq = 10)
    m = re.match(r"^([A-Z]{2})(\d{4})(\d{4})$", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return s  # fallback: return as-is uppercased


def _resolve_title(proj: dict, agency_config: Optional[dict]) -> str:
    """Resolve a human-readable project title from vertical title_template + metadata.

    Example: For a 'knots' vertical with template '{groom_name} & {bride_name}'
    and metadata {'groom_name': 'Aswin', 'bride_name': 'Priya'}, returns 'Aswin & Priya'.
    Falls back to client_name or project code.

    Accepts a pre-fetched agency_config dict to avoid N+1 DB queries when resolving
    titles for lists of projects.
    """
    metadata = proj.get("metadata", {})
    fallback = metadata.get("client_name") or proj.get("code", "Project")

    vertical_id = proj.get("vertical")
    if not vertical_id or not agency_config:
        return fallback

    v_id = vertical_id.lower()
    vertical_cfg = next((v for v in agency_config.get("verticals", []) if v.get("id", "").lower() == v_id), None)
    if not vertical_cfg or not vertical_cfg.get("title_template"):
        return fallback

    template = vertical_cfg["title_template"]
    lower_meta = {k.lower(): v for k, v in metadata.items()}

    def _replacer(match):
        key = match.group(1).lower()
        val = lower_meta.get(key, "")
        return str(val).strip() if val else ""

    resolved = re.sub(r"\{(\w+)\}", _replacer, template).strip()
    resolved = re.sub(r"^[&\s]+|[&\s]+$", "", resolved).strip()
    return resolved if resolved else fallback


# ─── Helpers (event summary) ────────────────────────────────────────────────

def _summarise_event(ev: dict) -> dict:
    """Strip heavy nested data from an event, keep useful summary."""
    return {
        "id": ev.get("id"),
        "type": ev.get("type"),
        "venue_name": ev.get("venue_name"),
        "venue_location": ev.get("venue_location"),
        "start_date": ev.get("start_date"),
        "end_date": ev.get("end_date"),
        "assignment_count": len(ev.get("assignments", [])),
        "deliverable_count": len(ev.get("deliverables", [])),
    }


def _summarise_project(proj: dict, agency_config: Optional[dict]) -> dict:
    """Return a lean project dict with event summaries, resolved title, and key metadata.
    Accepts pre-fetched agency_config to avoid N+1 DB lookups."""
    title = _resolve_title(proj, agency_config)
    metadata = proj.get("metadata", {})
    return {
        "_id": str(proj["_id"]) if "_id" in proj else None,
        "code": proj.get("code"),
        "project_name": title,
        "vertical": proj.get("vertical"),
        "client_id": proj.get("client_id"),
        "client_name": metadata.get("client_name"),
        "metadata": {k: v for k, v in metadata.items() if k != "client_name" and v},
        "status": proj.get("status"),
        "lead_source": proj.get("lead_source"),
        "events": [_summarise_event(e) for e in proj.get("events", [])],
        "assignment_count": len(proj.get("assignments", [])),
        "created_on": proj.get("created_on"),
        "updated_on": proj.get("updated_on"),
    }


# ─── Projects ───────────────────────────────────────────────────────────────

@router.get("/projects")
async def list_projects(
    vertical: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    db: ScopedDatabase = Depends(get_integration_db),
):
    """List projects with event summaries. Scope to a vertical if provided, else search globally. Use /projects/{id} for full details."""
    query = {}
    if vertical:
        # Agent might pass 'pluto' or 'Pluto', make it case-insensitive but exact match
        query["vertical"] = {"$regex": f"^{re.escape(vertical)}$", "$options": "i"}
    if status:
        query["status"] = status
    if search:
        query["$or"] = _build_search_or(search)

    skip = (page - 1) * limit
    cursor = db.projects.find(query).sort("_id", -1).skip(skip).limit(limit)
    projects = await cursor.to_list(length=limit)
    total = await db.projects.count_documents(query)

    # Fetch agency config ONCE for title resolution (eliminates N+1 DB queries)
    agency_config = await db.agency_configs.find_one({}) if projects else None

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "data": [_summarise_project(p, agency_config) for p in projects],
    }


@router.get("/projects/stats")
async def get_project_stats(
    vertical: Optional[str] = None,
    db: ScopedDatabase = Depends(get_integration_db),
):
    """Project overview stats: total, active, ongoing, this month."""
    base_query = {}
    if vertical:
        base_query["vertical"] = {"$regex": f"^{vertical}$", "$options": "i"}

    total = await db.projects.count_documents(base_query)

    active_query = {**base_query, "status": {"$nin": ["completed", "Completed", "archived", "Archived", "cancelled", "Cancelled"]}}
    active = await db.projects.count_documents(active_query)

    ongoing_query = {**base_query, "status": {"$in": ["ongoing", "Ongoing"]}}
    ongoing = await db.projects.count_documents(ongoing_query)

    now = datetime.now(timezone.utc)
    start_of_month = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    if now.month == 12:
        next_month = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_month = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)

    month_query = {
        **base_query,
        "events": {"$elemMatch": {"start_date": {"$gte": start_of_month, "$lt": next_month}}},
    }
    this_month = await db.projects.count_documents(month_query)

    return {"total": total, "active": active, "ongoing": ongoing, "this_month": this_month}


@router.get("/projects/{identifier}")
async def get_project(
    identifier: str,
    db: ScopedDatabase = Depends(get_integration_db),
):
    """Fetch a single project by its MongoDB _id OR its string code (e.g. KN-1234)."""
    if ObjectId.is_valid(identifier):
        project = await db.projects.find_one({"_id": ObjectId(identifier)})
    else:
        code = _normalise_code(identifier)
        project = await db.projects.find_one({"code": code})

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return _clean_id(project)


async def get_project_summary(
    identifier: str,
    db: ScopedDatabase = Depends(get_integration_db),
):
    """Agent-friendly project summary: returns structured, token-efficient data
    instead of the raw MongoDB document. Used by the agent's get_project_details(view='full')."""
    if ObjectId.is_valid(identifier):
        project = await db.projects.find_one({"_id": ObjectId(identifier)})
    else:
        code = _normalise_code(identifier)
        project = await db.projects.find_one({"code": code})

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    agency_config = await db.agency_configs.find_one({})
    return _summarise_project(project, agency_config)


@router.get("/projects/{identifier}/team")
async def get_project_team(
    identifier: str,
    db: ScopedDatabase = Depends(get_integration_db),
):
    """Token-saver: Fetch only event names, dates, and assigned associates."""
    query = {"_id": ObjectId(identifier)} if ObjectId.is_valid(identifier) else {"code": _normalise_code(identifier)}
    project = await db.projects.find_one(query, {"code": 1, "events.type": 1, "events.start_date": 1, "events.assignments": 1, "events.team_requirements": 1})

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    team_info = []
    for evt in project.get("events", []):
        start = evt.get("start_date").isoformat() if isinstance(evt.get("start_date"), datetime) else str(evt.get("start_date") or "TBD")
        assignments = [
            {
                "name": a.get("associate_name", "Unknown"),
                "role": a.get("role", "Unknown"),
                "tags": a.get("tags", [])
            }
            for a in evt.get("assignments", [])
        ]
        team_req = [{"role": r.get("role"), "count": r.get("count")} for r in evt.get("team_requirements", [])]
        entry = {
            "event": evt.get("type", "Unknown Event"),
            "date": start,
            "team": assignments
        }
        if team_req:
            entry["team_requirements"] = team_req
        team_info.append(entry)

    return {"project_code": project.get("code"), "schedule_team": team_info}

@router.get("/projects/{identifier}/schedule")
async def get_project_schedule(
    identifier: str,
    db: ScopedDatabase = Depends(get_integration_db),
):
    """Token-saver: Fetch only event names, dates, and venues."""
    query = {"_id": ObjectId(identifier)} if ObjectId.is_valid(identifier) else {"code": _normalise_code(identifier)}
    project = await db.projects.find_one(query, {"code": 1, "events.type": 1, "events.start_date": 1, "events.end_date": 1, "events.venue_name": 1, "events.venue_location": 1})
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
        
    schedule = []
    for evt in project.get("events", []):
        start = evt.get("start_date").isoformat() if isinstance(evt.get("start_date"), datetime) else str(evt.get("start_date") or "TBD")
        end = evt.get("end_date").isoformat() if isinstance(evt.get("end_date"), datetime) else str(evt.get("end_date") or "TBD")
        schedule.append({
            "event": evt.get("type", "Unknown Event"),
            "start": start,
            "end": end,
            "venue": evt.get("venue_name", "TBD"),
            "location": evt.get("venue_location", "")
        })
        
    return {"project_code": project.get("code"), "schedule": schedule}

@router.get("/projects/{identifier}/deliverables")
async def get_pending_deliverables(
    identifier: str,
    db: ScopedDatabase = Depends(get_integration_db),
):
    """Token-saver: Fetch only pending deliverables for a project."""
    query = {"_id": ObjectId(identifier)} if ObjectId.is_valid(identifier) else {"code": _normalise_code(identifier)}
    project = await db.projects.find_one(query, {"code": 1, "events.type": 1, "events.deliverables": 1})
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
        
    pending = []
    for evt in project.get("events", []):
        for d in evt.get("deliverables", []):
            if d.get("status", "").lower() not in ["done", "completed"]:
                due = d.get("due_date").isoformat() if isinstance(d.get("due_date"), datetime) else str(d.get("due_date") or "TBD")
                pending.append({
                    "event": evt.get("type", "Unknown Event"),
                    "deliverable": d.get("type", "Unknown"),
                    "status": d.get("status", "Pending"),
                    "due": due
                })
        
    return {"project_code": project.get("code"), "pending_deliverables": pending}




# ─── Clients ────────────────────────────────────────────────────────────────

@router.get("/clients")
async def list_clients(
    client_type: Optional[str] = Query(None, alias="type"),
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    db: ScopedDatabase = Depends(get_integration_db),
):
    """List clients with optional filters."""
    query = {}
    if client_type:
        query["type"] = {"$regex": f"^{re.escape(client_type)}$", "$options": "i"}
    if search:
        safe = re.escape(search)
        query["$or"] = [
            {"name": {"$regex": safe, "$options": "i"}},
            {"phone": {"$regex": safe, "$options": "i"}},
        ]

    skip = (page - 1) * limit
    cursor = db.clients.find(query).sort("_id", -1).skip(skip).limit(limit)
    clients = await cursor.to_list(length=limit)
    total = await db.clients.count_documents(query)

    return {"total": total, "page": page, "limit": limit, "data": _clean_list(clients)}


@router.get("/clients/stats")
async def get_client_stats(db: ScopedDatabase = Depends(get_integration_db)):
    """Client overview stats."""
    total = await db.clients.count_documents({})

    active = await db.clients.count_documents({"type": "Active Client"})

    now = datetime.now(timezone.utc)
    start_of_month = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    this_month = await db.clients.count_documents({"created_at": {"$gte": start_of_month}})

    return {"total": total, "active": active, "this_month": this_month}


# ─── Associates ─────────────────────────────────────────────────────────────

@router.get("/associates")
async def list_associates(
    role: Optional[str] = None,
    employment_type: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    db: ScopedDatabase = Depends(get_integration_db),
):
    """List associates with optional filters."""
    query = {}
    if role:
        query["primary_role"] = {"$regex": f"^{re.escape(role)}$", "$options": "i"}
    if employment_type:
        query["employment_type"] = {"$regex": f"^{re.escape(employment_type)}$", "$options": "i"}
    if search:
        safe = re.escape(search)
        query["$or"] = [
            {"name": {"$regex": safe, "$options": "i"}},
            {"phone": {"$regex": safe, "$options": "i"}},
        ]

    skip = (page - 1) * limit
    cursor = db.associates.find(query).sort("_id", -1).skip(skip).limit(limit)
    associates = await cursor.to_list(length=limit)
    total = await db.associates.count_documents(query)

    return {"total": total, "page": page, "limit": limit, "data": _clean_list(associates)}


@router.get("/associates/stats")
async def get_associate_stats(db: ScopedDatabase = Depends(get_integration_db)):
    """Associate overview stats."""
    total = await db.associates.count_documents({})
    inhouse = await db.associates.count_documents({"employment_type": "In-house"})
    freelance = await db.associates.count_documents({"employment_type": "Freelance"})

    return {"total": total, "inhouse": inhouse, "freelance": freelance}



@router.get("/associates/contact")
async def get_associate_contact(
    search: str = Query(..., description="Name or partial name to search for"),
    db: ScopedDatabase = Depends(get_integration_db),
):
    """Token-saver: Fetch only contact info for associates matching a name."""
    safe = re.escape(search)
    query = {"name": {"$regex": safe, "$options": "i"}}
    cursor = db.associates.find(query, {"name": 1, "email_id": 1, "email": 1, "phone": 1, "primary_role": 1}).limit(5)
    associates = await cursor.to_list(length=5)
    
    contact_list = []
    for a in associates:
        contact_list.append({
            "name": a.get("name"),
            "role": a.get("primary_role", "Associate"),
            "phone": a.get("phone", "N/A"),
            "email": a.get("email_id") or a.get("email") or "N/A"
        })
        
    return {"contacts": contact_list}

# ─── Events (flat view) ────────────────────────────────────────────────────

@router.get("/events")
async def list_events(
    vertical: Optional[str] = None,
    search: Optional[str] = None,
    from_date: Optional[str] = Query(None, description="ISO date string, e.g. 2026-03-01"),
    to_date: Optional[str] = Query(None, description="ISO date string, e.g. 2026-04-01"),
    unassigned_only: bool = Query(False, description="If true, only return events with no assignments"),
    limit: int = Query(100, ge=1, le=500),
    db: ScopedDatabase = Depends(get_integration_db),
):
    """Flat listing of events across projects with project context.

    Useful for answering: upcoming events, events near a date, events without team, etc.
    The `search` parameter matches against event type, client name, and metadata name fields
    (groom_name, bride_name, child_name, event_name).
    """
    # Build the match stage
    match: dict = {"status": {"$nin": ["completed", "Completed", "archived", "Archived", "cancelled", "Cancelled"]}}
    if vertical:
        match["vertical"] = {"$regex": f"^{re.escape(vertical)}$", "$options": "i"}
    if search:
        match["$or"] = _build_search_or(search)

    pipeline: list = [
        {"$match": match},
        {"$unwind": "$events"},
    ]

    # Post-unwind: filter to only matching events (by event type) or all events
    # of name-matched projects. Reuses the shared search helper but operates on
    # the unwound event structure.
    if search:
        search_or = _build_search_or(search)
        pipeline.append({"$match": {"$or": search_or}})

    # Date-range filter on the unwound event
    event_match: dict = {}
    if from_date:
        # Ensure aware datetime
        dt = datetime.fromisoformat(from_date)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        event_match["events.start_date"] = {"$gte": dt}
    if to_date:
        to_dt = datetime.fromisoformat(to_date)
        if to_dt.tzinfo is None:
            to_dt = to_dt.replace(tzinfo=timezone.utc)
        if len(to_date) == 10:
            # If only date is provided, make it inclusive of the whole day
            from datetime import timedelta
            to_dt += timedelta(days=1)
        event_match.setdefault("events.start_date", {})["$lt"] = to_dt
    if unassigned_only:
        event_match["events.assignments"] = {"$size": 0}
    if event_match:
        pipeline.append({"$match": event_match})

    pipeline.extend([
        {"$sort": {"events.start_date": 1}},
        {"$facet": {
            "metadata": [{"$count": "total"}],
            "data": [
                {"$limit": limit},
                {"$project": {
                    "_id": 0,
                    "project_id": {"$toString": "$_id"},
                    "project_code": "$code",
                    "client_id": "$client_id",
                    "client_name": "$metadata.client_name",
                    "groom_name": "$metadata.groom_name",
                    "bride_name": "$metadata.bride_name",
                    "vertical": "$vertical",
                    "project_status": "$status",
                    "event_id": "$events.id",
                    "event_type": "$events.type",
                    "venue_name": "$events.venue_name",
                    "venue_location": "$events.venue_location",
                    "start_date": "$events.start_date",
                    "end_date": "$events.end_date",
                    "assignment_count": {"$size": {"$ifNull": ["$events.assignments", []]}},
                    "deliverable_count": {"$size": {"$ifNull": ["$events.deliverables", []]}},
                }}
            ]
        }}
    ])

    cursor = db.projects.aggregate(pipeline)
    results = await cursor.to_list(length=1)
    
    if not results or not results[0]["data"]:
        return {"total": 0, "data": []}
        
    total = results[0]["metadata"][0]["total"] if results[0]["metadata"] else 0
    return {"total": total, "data": results[0]["data"]}


# ─── Associate Assignments ──────────────────────────────────────────────────

@router.get("/associates/{associate_id}/assignments")
async def get_associate_assignments(
    associate_id: str,
    db: ScopedDatabase = Depends(get_integration_db),
):
    """All projects & events an associate is assigned to.

    Useful for answering: "What is Ashish working on?", "Which projects involve Ravi?"
    """
    if not ObjectId.is_valid(associate_id):
        raise HTTPException(status_code=400, detail="Invalid associate ID")

    # Check the associate exists and get their name
    associate = await db.associates.find_one({"_id": ObjectId(associate_id)})
    if not associate:
        raise HTTPException(status_code=404, detail="Associate not found")

    # Find projects with this associate in event-level OR project-level assignments
    query = {
        "$or": [
            {"events.assignments.associate_id": associate_id},
            {"assignments.associate_id": associate_id},
        ]
    }
    cursor = db.projects.find(query)
    projects = await cursor.to_list(length=None)

    results = []
    for proj in projects:
        proj_base = {
            "project_id": str(proj["_id"]),
            "project_code": proj.get("code"),
            "client_name": proj.get("metadata", {}).get("client_name"),
            "vertical": proj.get("vertical"),
            "project_status": proj.get("status"),
        }

        # Event-level assignments
        for ev in proj.get("events", []):
            for asn in ev.get("assignments", []):
                if asn.get("associate_id") == associate_id:
                    results.append({
                        **proj_base,
                        "scope": "event",
                        "event_type": ev.get("type"),
                        "start_date": ev.get("start_date"),
                        "end_date": ev.get("end_date"),
                        "role": asn.get("role"),
                    })

        # Project-level assignments
        for asn in proj.get("assignments", []):
            if asn.get("associate_id") == associate_id:
                results.append({
                    **proj_base,
                    "scope": "project",
                    "event_type": None,
                    "start_date": None,
                    "end_date": None,
                    "role": asn.get("role"),
                })

    return {
        "associate_id": associate_id,
        "associate_name": associate.get("name"),
        "total": len(results),
        "data": results,
    }


# ─── Verticals ──────────────────────────────────────────────────────────────

@router.get("/verticals")
async def list_verticals(db: ScopedDatabase = Depends(get_integration_db)):
    """List all verticals that have at least one project."""
    pipeline = [
        {"$group": {"_id": "$vertical", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    cursor = db.projects.aggregate(pipeline)
    results = await cursor.to_list(length=None)
    return {"data": [{"vertical": r["_id"], "project_count": r["count"]} for r in results]}


# ─── Dashboard ──────────────────────────────────────────────────────────────

@router.get("/dashboard/stats")
async def get_dashboard_stats(db: ScopedDatabase = Depends(get_integration_db)):
    """High-level dashboard stats."""
    active_projects = await db.projects.count_documents(
        {"status": {"$nin": ["completed", "Completed", "archived", "Archived", "cancelled", "Cancelled"]}}
    )
    total_clients = await db.clients.count_documents({})
    total_associates = await db.associates.count_documents({})
    pending_tasks = await db.tasks.count_documents({"status": {"$ne": "done"}})

    return {
        "active_projects": active_projects,
        "total_clients": total_clients,
        "total_associates": total_associates,
        "pending_tasks": pending_tasks,
    }


# ─── Finance ────────────────────────────────────────────────────────────────

@router.get("/finance/overview")
async def get_finance_overview(db: ScopedDatabase = Depends(get_integration_db)):
    """Finance summary: income, expenses, net profit, receivables."""
    pipeline = [{"$group": {"_id": "$type", "total": {"$sum": "$amount"}}}]
    cursor = db.transactions.aggregate(pipeline)
    totals = {doc["_id"]: doc["total"] for doc in await cursor.to_list(length=None)}

    income = totals.get("income", 0.0)
    expenses = totals.get("expense", 0.0)

    ledger_cursor = db.ledgers.aggregate([
        {"$group": {"_id": None, "total_balance": {"$sum": "$balance_amount"}}}
    ])
    receivables_doc = await ledger_cursor.to_list(length=1)
    receivables = receivables_doc[0]["total_balance"] if receivables_doc else 0.0

    return {
        "income": income,
        "expenses": expenses,
        "net_profit": income - expenses,
        "outstanding_receivables": receivables,
    }
