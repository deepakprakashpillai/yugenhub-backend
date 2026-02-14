from fastapi import APIRouter, Body, HTTPException, status, Query
from bson import ObjectId
from typing import get_args
from database import users_collection 
# Keeping users_collection for now as sync_inhouse_to_user might need global access or we refactor it too.
# Actually, sync_inhouse_to_user creates a user. Creating a user should probably be done via db.users 
# BUT `db` variable is local to endpoints. The helper needs `db` passed to it.

from models.associate import AssociateModel
from routes.deps import get_current_user, get_db
from models.user import UserModel
from fastapi import Depends
from middleware.db_guard import ScopedDatabase
from logging_config import get_logger
from datetime import datetime
import uuid

router = APIRouter(
    prefix="/api/associates",
    tags=["Associates"]
)
logger = get_logger("associates")

# --- HELPER: Sync In-house Associate to User ---
# --- HELPER: Sync In-house Associate to User ---
async def sync_inhouse_to_user(db: ScopedDatabase, associate_data: dict, agency_id: str):
    """
    If associate is In-house and has email, ensure a User record exists.
    This allows them to log in via Google Sign-In.
    """
    employment_type = associate_data.get("employment_type")
    email = associate_data.get("email_id")
    
    if employment_type != "In-house":
        return  # Only sync In-house associates
    
    if not email:
        return  # No email, can't create user
    
    # Check if user already exists
    # db.users is scoped to agency_id. 
    # If a user exists in another agency with same email, what happens?
    # Currently User model has agency_id. So a user belongs to ONE agency.
    # If we want multi-agency users, we need a different architecture.
    # For now, we assume unique email globally? Or scoped? 
    # Auth checks global users collection usually.
    # But here we are creating a USER for THIS agency. 
    # If we use db.users.find_one({"email": email}), it adds agency_id filter.
    # So it checks if user exists IN THIS AGENCY.
    
    existing_user = await db.users.find_one({"email": email})
    if existing_user:
        return  # User already exists in this agency
    
    # Create new user
    new_user = {
        "id": str(uuid.uuid4()),
        "google_id": "",  # Will be set on first Google Sign-In
        "email": email,
        "name": associate_data.get("name", "Unknown"),
        "picture": None,
        # agency_id will be injected by db.users.insert_one
        "role": "member",
        "created_at": datetime.now(),
        "last_login": datetime.now()
    }
    
    await db.users.insert_one(new_user)
    logger.info(f"Created user for in-house associate", extra={"data": {"email": email, "agency_id": agency_id}})

@router.get("")
async def get_associates(
    search: str = Query(None, description="Search by name, phone, or city"),
    role: str = Query(None, description="Filter by primary role"),
    employment_type: str = Query(None, description="Filter by employment type"),
    status: str = Query(None, description="Filter by status: active or inactive"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(10, ge=1, le=50000, description="Items per page"),
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    # query = {"agency_id": current_agency_id} -> Handled by db wrapper
    query = {}
    
    # 1. Search Logic
    if search:
        query["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"phone_number": {"$regex": search, "$options": "i"}},
            {"base_city": {"$regex": search, "$options": "i"}}
        ]
    
    # 2. Filter Logic
    if role:
        query["primary_role"] = role
        
    if employment_type:
        query["employment_type"] = employment_type

    if status:
        if status.lower() == "active":
            query["is_active"] = True
        elif status.lower() == "inactive":
            query["is_active"] = False

    # 3. Pagination Logic
    skip = (page - 1) * limit

    # Execute query with skip and limit
    cursor = db.associates.find(query).skip(skip).limit(limit)
    associates = await cursor.to_list(length=limit)
    
    # 4. Total Count (Very important for the frontend to know how many pages exist)
    total_count = await db.associates.count_documents(query)

    for a in associates:
        a["_id"] = str(a["_id"])
        
    return {
        "total": total_count,
        "page": page,
        "limit": limit,
        "total_pages": (total_count + limit - 1) // limit,
        "data": associates
    }

    return {
        "roles": list(roles),
        "employment_types": list(employment_types)
    }

@router.get("/stats")
async def get_associate_stats(current_user: UserModel = Depends(get_current_user), db: ScopedDatabase = Depends(get_db)):
    """READ STATS: Get overview metrics for Associates"""
    # current_agency_id handled by db wrapper
    base_query = {}

    # 1. Total Associates
    total = await db.associates.count_documents(base_query)

    # 2. Active Associates
    active_query = base_query.copy()
    active_query["is_active"] = True
    active = await db.associates.count_documents(active_query)

    # 3. New This Month
    from datetime import datetime
    now = datetime.now()
    start_of_month = datetime(now.year, now.month, 1)
    
    month_query = base_query.copy()
    month_query["created_at"] = {"$gte": start_of_month}
    this_month = await db.associates.count_documents(month_query)

    return {
        "total": total,
        "active": active,
        "this_month": this_month
    }

@router.get("/{id}")
async def get_associate(id: str, current_user: UserModel = Depends(get_current_user), db: ScopedDatabase = Depends(get_db)):
    """READ ONE: Fetch a single associate by ID"""
    if not ObjectId.is_valid(id):
        raise HTTPException(status_code=400, detail="Invalid ID format")
    
    associate = await db.associates.find_one({"_id": ObjectId(id)})
    if not associate:
        raise HTTPException(status_code=404, detail="Associate not found")
        
    associate["_id"] = str(associate["_id"])
    return associate

@router.post("", status_code=201)
async def create_associate(
    associate: AssociateModel = Body(...), 
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """CREATE: Add a new associate"""
    associate.agency_id = current_user.agency_id  # Auto-assign agency
    
    # Validate: In-house associates must have email
    if associate.employment_type == "In-house" and not associate.email_id:
        raise HTTPException(
            status_code=400, 
            detail="In-house associates must have an email address for login."
        )
    
    associate_data = associate.model_dump()
    result = await db.associates.insert_one(associate_data)
    
    # Sync to Users collection if In-house
    await sync_inhouse_to_user(db, associate_data, current_user.agency_id)
    
    logger.info(f"Associate created", extra={"data": {"id": str(result.inserted_id), "name": associate.name, "type": associate.employment_type}})
    return {"message": "Associate created successfully", "id": str(result.inserted_id)}

@router.patch("/{associate_id}")
async def update_associate(
    associate_id: str, 
    update_data: dict = Body(...), 
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """UPDATE: Modify an existing associate"""
    if not ObjectId.is_valid(associate_id):
        raise HTTPException(status_code=400, detail="Invalid Associate ID format")

    # Validate: If changing to In-house, email is required
    if update_data.get("employment_type") == "In-house":
        # Get existing associate to check email
        existing = await db.associates.find_one({"_id": ObjectId(associate_id)})
        new_email = update_data.get("email_id") or (existing.get("email_id") if existing else None)
        if not new_email:
            raise HTTPException(
                status_code=400, 
                detail="In-house associates must have an email address for login."
            )
    
    updated_doc = await db.associates.find_one_and_update(
        {"_id": ObjectId(associate_id)},
        {"$set": update_data},
        return_document=True
    )

    if not updated_doc:
        logger.warning(f"Associate update failed: not found", extra={"data": {"associate_id": associate_id}})
        raise HTTPException(status_code=404, detail="Associate not found")
    
    # Sync to Users collection if In-house
    await sync_inhouse_to_user(db, updated_doc, current_user.agency_id)

    logger.info(f"Associate updated", extra={"data": {"associate_id": associate_id, "fields": list(update_data.keys())}})
    updated_doc["_id"] = str(updated_doc["_id"])
    return updated_doc

@router.delete("/{associate_id}")
async def delete_associate(associate_id: str, current_user: UserModel = Depends(get_current_user), db: ScopedDatabase = Depends(get_db)):
    """DELETE: Remove an associate"""
    if not ObjectId.is_valid(associate_id):
        raise HTTPException(status_code=400, detail="Invalid Associate ID format")

    delete_result = await db.associates.delete_one({"_id": ObjectId(associate_id)})

    if delete_result.deleted_count == 0:
        logger.warning(f"Associate deletion failed: not found", extra={"data": {"associate_id": associate_id}})
        raise HTTPException(status_code=404, detail="Associate not found")
    
    logger.info(f"Associate deleted", extra={"data": {"associate_id": associate_id}})
    

