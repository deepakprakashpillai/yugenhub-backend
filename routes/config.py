from fastapi import APIRouter, Body, HTTPException, Depends
# REMOVED raw collection imports
from models.config import AgencyConfigModel
from routes.deps import get_current_user, get_db
from models.user import UserModel
from middleware.db_guard import ScopedDatabase
from logging_config import get_logger

# Helper function to parse MongoDB data, assuming it handles _id conversion
# This function is not provided in the original code, but is implied by the change.
# For the purpose of this edit, we'll define a simple one.
def parse_mongo_data(data: dict) -> dict:
    if "_id" in data:
        data["_id"] = str(data["_id"])
    return data

router = APIRouter(prefix="/api/config", tags=["Configuration"])
logger = get_logger("config")

@router.get("")
async def get_config(current_user: UserModel = Depends(get_current_user), db: ScopedDatabase = Depends(get_db)):
    # ScopedDB automatically filters by agency_id
    config = await db.agency_configs.find_one({})
    if not config:
        return {"agency_id": current_user.agency_id, "verticals": []} # Return empty default
    return parse_mongo_data(config)

@router.post("/init")
async def initialize_config(
    config: AgencyConfigModel = Body(...), 
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    # Override agency_id with user's specific agency
    current_agency_id = current_user.agency_id
    config.agency_id = current_agency_id
    
    # ScopedDB injection works on update too
    await db.agency_configs.update_one(
        {},
        {"$set": config.model_dump()},
        upsert=True
    )
    logger.info(f"Config initialized/updated", extra={"data": {"agency_id": current_agency_id}})
    return {"message": "Config initialized successfully"}