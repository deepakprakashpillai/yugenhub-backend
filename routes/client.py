# routes/client.py
from fastapi import APIRouter, Body, HTTPException, status, Query
from typing import get_args
from database import clients_collection
from models.client import ClientModel
from routes.deps import get_current_user
from models.user import UserModel
from fastapi import Depends
from database import clients_collection
from bson import ObjectId
from logging_config import get_logger

router = APIRouter(
    prefix="/api/clients",
    tags=["Clients"]
)
logger = get_logger("clients")

@router.get("")
async def get_clients(
    search: str = Query(None, description="Search by name or phone"),
    client_type: str = Query(None, alias="type", description="Filter by Lead, Active, etc."),
    sort: str = Query(None, description="Sort options: projects_desc, projects_asc, newest, oldest"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(10, ge=1, le=100, description="Items per page"),
    current_user: UserModel = Depends(get_current_user)
):
    current_agency_id = current_user.agency_id
    query = {"agency_id": current_agency_id}

    # 1. Search Logic
    if search:
        query["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}},
            {"location": {"$regex": search, "$options": "i"}}
        ]

    # 2. Filter Logic
    if client_type:
        query["type"] = client_type

    # 3. Pagination & Sorting Logic
    skip = (page - 1) * limit
    
    # Define Sort
    sort_criteria = [("_id", -1)] # Default to newest (by ObjectID/creation)
    
    if sort:
        if sort == "projects_desc":
            sort_criteria = [("total_projects", -1)]
        elif sort == "projects_asc":
            sort_criteria = [("total_projects", 1)]
        elif sort == "newest":
            sort_criteria = [("created_at", -1)]
        elif sort == "oldest":
            sort_criteria = [("created_at", 1)]

    # Execute query
    cursor = clients_collection.find(query).sort(sort_criteria).skip(skip).limit(limit)
    clients = await cursor.to_list(length=limit)
    
    # 4. Total Count
    total_count = await clients_collection.count_documents(query)

    for c in clients:
        c["_id"] = str(c["_id"])
        
    return {
        "total": total_count,
        "page": page,
        "limit": limit,
        "total_pages": (total_count + limit - 1) // limit,
        "data": clients
    }

    return {
        "client_types": list(client_types)
    }

@router.get("/stats")
async def get_client_stats(current_user: UserModel = Depends(get_current_user)):
    """READ STATS: Get overview metrics for Clients"""
    current_agency_id = current_user.agency_id
    base_query = {"agency_id": current_agency_id}

    # 1. Total Clients
    total = await clients_collection.count_documents(base_query)

    # 2. Active Clients
    active_query = base_query.copy()
    active_query["type"] = "Active Client"
    active = await clients_collection.count_documents(active_query)

    # 3. New This Month
    from datetime import datetime
    now = datetime.now()
    start_of_month = datetime(now.year, now.month, 1)
    
    month_query = base_query.copy()
    month_query["created_at"] = {"$gte": start_of_month} # Note: Check if model uses created_at or created_on. Model says created_at.
    this_month = await clients_collection.count_documents(month_query)

    return {
        "total": total,
        "active": active,
        "this_month": this_month
    }

@router.get("/{id}")
async def get_client(id: str, current_user: UserModel = Depends(get_current_user)):
    """READ ONE: Fetch a single client by ID"""
    if not ObjectId.is_valid(id):
        raise HTTPException(status_code=400, detail="Invalid ID format")
    
    current_agency_id = current_user.agency_id
    client = await clients_collection.find_one({"_id": ObjectId(id), "agency_id": current_agency_id})
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
        
    client["_id"] = str(client["_id"])
    return client

@router.post("", status_code=201)
async def create_client(client: ClientModel = Body(...), current_user: UserModel = Depends(get_current_user)):
    """CREATE: Add a new client"""
    current_agency_id = current_user.agency_id
    client.agency_id = current_agency_id # Auto-assign agency
    
    result = await clients_collection.insert_one(client.model_dump())
    logger.info(f"Client created", extra={"data": {"id": str(result.inserted_id), "name": client.name}})
    return {"message": "Client created successfully", "id": str(result.inserted_id)}

@router.patch("/{client_id}")
async def update_client(client_id: str, update_data: dict = Body(...), current_user: UserModel = Depends(get_current_user)):
    """UPDATE: Modify an existing client"""
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid Client ID format")

    current_agency_id = current_user.agency_id
    updated_client = await clients_collection.find_one_and_update(
        {"_id": ObjectId(client_id), "agency_id": current_agency_id},
        {"$set": update_data},
        return_document=True
    )

    if not updated_client:
        logger.warning(f"Client update failed: not found", extra={"data": {"client_id": client_id}})
        raise HTTPException(status_code=404, detail="Client not found")

    logger.info(f"Client updated", extra={"data": {"client_id": client_id, "fields": list(update_data.keys())}})
    updated_client["_id"] = str(updated_client["_id"])
    return updated_client

@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_client(client_id: str, current_user: UserModel = Depends(get_current_user)):
    """DELETE: Remove a client"""
    if not ObjectId.is_valid(client_id):
        raise HTTPException(status_code=400, detail="Invalid Client ID format")

    current_agency_id = current_user.agency_id
    delete_result = await clients_collection.delete_one({"_id": ObjectId(client_id), "agency_id": current_agency_id})

    if delete_result.deleted_count == 0:
        logger.warning(f"Client deletion failed: not found", extra={"data": {"client_id": client_id}})
        raise HTTPException(status_code=404, detail="Client not found")
    
    logger.info(f"Client deleted", extra={"data": {"client_id": client_id}})
    return None