from fastapi import APIRouter, Depends, HTTPException
from typing import List
from models.user import UserModel
from routes.deps import get_current_user, get_db
from middleware.db_guard import ScopedDatabase
from logging_config import get_logger

router = APIRouter(prefix="/api/users", tags=["Users"])
logger = get_logger("users")

def parse_mongo_data(data):
    if isinstance(data, list):
        return [parse_mongo_data(item) for item in data]
    if isinstance(data, dict):
        if "_id" in data:
            data["_id"] = str(data["_id"])
        return {k: parse_mongo_data(v) for k, v in data.items()}
    return data

@router.get("", response_model=List[dict])
async def list_users(current_user: UserModel = Depends(get_current_user), db: ScopedDatabase = Depends(get_db)):
    """List all users for assignment dropdowns"""
    # In the future, might want to restrict this or filter by agency
    users = await db.users.find({}).to_list(1000)
    return parse_mongo_data(users)
