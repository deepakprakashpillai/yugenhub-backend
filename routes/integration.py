# routes/integration.py
# Read-only API endpoints for n8n and external integrations.
# Authenticated via API key (X-API-Key header), scoped per agency_id query param.

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
    """List projects with optional filters."""
    query = {}
    if vertical:
        query["vertical"] = vertical
    if status:
        query["status"] = status
    if search:
        query["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"code": {"$regex": search, "$options": "i"}},
        ]

    skip = (page - 1) * limit
    cursor = db.projects.find(query).sort("_id", -1).skip(skip).limit(limit)
    projects = await cursor.to_list(length=limit)
    total = await db.projects.count_documents(query)

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "data": _clean_list(projects),
    }


@router.get("/projects/stats")
async def get_project_stats(
    vertical: Optional[str] = None,
    db: ScopedDatabase = Depends(get_integration_db),
):
    """Project overview stats: total, active, ongoing, this month."""
    base_query = {}
    if vertical:
        base_query["vertical"] = vertical

    total = await db.projects.count_documents(base_query)

    active_query = {**base_query, "status": {"$nin": ["completed", "Completed", "archived", "Archived", "cancelled", "Cancelled"]}}
    active = await db.projects.count_documents(active_query)

    ongoing_query = {**base_query, "status": {"$in": ["ongoing", "Ongoing"]}}
    ongoing = await db.projects.count_documents(ongoing_query)

    now = datetime.now(timezone.utc)
    start_of_month = datetime(now.year, now.month, 1)
    if now.month == 12:
        next_month = datetime(now.year + 1, 1, 1)
    else:
        next_month = datetime(now.year, now.month + 1, 1)

    month_query = {
        **base_query,
        "events": {"$elemMatch": {"start_date": {"$gte": start_of_month, "$lt": next_month}}},
    }
    this_month = await db.projects.count_documents(month_query)

    return {"total": total, "active": active, "ongoing": ongoing, "this_month": this_month}


@router.get("/projects/{project_id}")
async def get_project(
    project_id: str,
    db: ScopedDatabase = Depends(get_integration_db),
):
    """Fetch a single project by its MongoDB _id."""
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid project ID")

    project = await db.projects.find_one({"_id": ObjectId(project_id)})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return _clean_id(project)


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
        query["type"] = client_type
    if search:
        query["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}},
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
    start_of_month = datetime(now.year, now.month, 1)
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
        query["primary_role"] = role
    if employment_type:
        query["employment_type"] = employment_type
    if search:
        query["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}},
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
