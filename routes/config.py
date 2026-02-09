from fastapi import APIRouter, Body, HTTPException
from database import configs_collection
from models.config import AgencyConfigModel
from routes.deps import get_current_user
from models.user import UserModel
from fastapi import Depends
from database import configs_collection

# Helper function to parse MongoDB data, assuming it handles _id conversion
# This function is not provided in the original code, but is implied by the change.
# For the purpose of this edit, we'll define a simple one.
def parse_mongo_data(data: dict) -> dict:
    if "_id" in data:
        data["_id"] = str(data["_id"])
    return data

router = APIRouter(prefix="/api/config", tags=["Configuration"])

@router.get("/")
async def get_config(current_user: UserModel = Depends(get_current_user)):
    current_agency_id = current_user.agency_id
    config = await configs_collection.find_one({"agency_id": current_agency_id})
    if not config:
        return {"agency_id": current_agency_id, "verticals": []} # Return empty default
    return parse_mongo_data(config)

@router.post("/init")
async def initialize_config(config: AgencyConfigModel = Body(...), current_user: UserModel = Depends(get_current_user)):
    # Override agency_id with user's specific agency
    current_agency_id = current_user.agency_id
    config.agency_id = current_agency_id
    
    await configs_collection.update_one(
        {"agency_id": current_agency_id},
        {"$set": config.model_dump()},
        upsert=True
    )
    return {"message": "Config initialized successfully"}