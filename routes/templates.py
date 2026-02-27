from fastapi import APIRouter, Body, HTTPException, Depends, Query
from bson import ObjectId
from datetime import datetime
from typing import List, Optional

from database import projects_collection # Fallback if needed, but we use db
from models.template import ProjectTemplateModel
from models.user import UserModel
from routes.deps import get_current_user, get_db, require_role, get_user_verticals
from middleware.db_guard import ScopedDatabase
from logging_config import get_logger

router = APIRouter(prefix="/api/templates", tags=["Templates"])
logger = get_logger("templates")

def parse_mongo_data(data):
    if isinstance(data, list):
        return [parse_mongo_data(item) for item in data]
    if isinstance(data, dict):
        return {k: (str(v) if isinstance(v, ObjectId) else parse_mongo_data(v)) for k, v in data.items()}
    return data

@router.post("", status_code=201)
async def create_template(
    template_data: dict = Body(...),
    current_user: UserModel = Depends(require_role("owner", "admin")),
    db: ScopedDatabase = Depends(get_db)
):
    """CREATE: Create a new template or save existing project as template"""
    
    # CASE 1: Save from existing Project
    if "project_id" in template_data and template_data["project_id"]:
        project_id = template_data["project_id"]
        if not ObjectId.is_valid(project_id):
            raise HTTPException(status_code=400, detail="Invalid Project ID")
        
        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        # Sanitize Data
        events = project.get("events", [])
        sanitized_events = []
        for evt in events:
            # Create a clean event copy
            clean_evt = {
                "id": evt.get("id"), # Keep ID structure or gen new? Better keep for internal consistency if rels exist
                "type": evt.get("type"),
                "notes": evt.get("notes"),
                "venue_name": "", # Clear specific info
                "venue_location": "",
                "start_date": None, # Clear dates
                "end_date": None,
                "start_time": None,
                "end_time": None,
                "assignments": [], # Clear team
                "deliverables": []
            }
            
            # Sanitize Deliverables
            for dev in evt.get("deliverables", []):
                clean_evt["deliverables"].append({
                    "id": dev.get("id"),
                    "type": dev.get("type"),
                    "quantity": dev.get("quantity", 1),
                    "notes": dev.get("notes"),
                    "status": "pending",
                    "due_date": None
                })
                
            sanitized_events.append(clean_evt)

        # Construct Template
        new_template = ProjectTemplateModel(
            agency_id=current_user.agency_id,
            vertical=project.get("vertical"),
            name=template_data.get("name", f"Template from {project.get('code')}"),
            description=template_data.get("description", ""),
            events=sanitized_events,
            metadata=project.get("metadata", {}) # Keep metadata keys/values? Maybe user wants to keep defaults
        )
        
    # CASE 2: Create from Scratch (Manual)
    else:
        # Validate minimal fields
        if not template_data.get("name") or not template_data.get("vertical"):
             raise HTTPException(status_code=400, detail="Name and Vertical are required")
             
        new_template = ProjectTemplateModel(
            agency_id=current_user.agency_id,
            vertical=template_data.get("vertical"),
            name=template_data.get("name"),
            description=template_data.get("description"),
            events=template_data.get("events", []),
            metadata=template_data.get("metadata", {})
        )

    # Save
    data = new_template.model_dump(by_alias=True, exclude={"id"})
    result = await db.templates.insert_one(data)
    
    logger.info(f"Template created", extra={"data": {"name": new_template.name, "vertical": new_template.vertical}})
    
    data["_id"] = str(result.inserted_id)
    return parse_mongo_data(data)

@router.get("")
async def list_templates(
    vertical: Optional[str] = None,
    current_user: UserModel = Depends(get_current_user),
    db: ScopedDatabase = Depends(get_db)
):
    """READ: List templates, filtered by user's vertical access"""
    query = {}
    
    # RBAC: Scope to user's allowed verticals
    user_verticals = await get_user_verticals(current_user, db)
    if vertical:
        if vertical not in user_verticals:
            return []
        query["vertical"] = vertical
    else:
        query["vertical"] = {"$in": user_verticals}
    
    cursor = db.templates.find(query).sort("created_at", -1)
    templates = await cursor.to_list(length=100)
    
    return parse_mongo_data(templates)

@router.patch("/{template_id}")
async def update_template(
    template_id: str,
    update_data: dict = Body(...),
    current_user: UserModel = Depends(require_role("owner", "admin")),
    db: ScopedDatabase = Depends(get_db)
):
    """UPDATE: Modify an existing template"""
    if not ObjectId.is_valid(template_id):
        raise HTTPException(status_code=400, detail="Invalid Template ID")
        
    update_data.pop("_id", None)
    update_data.pop("agency_id", None)
    update_data["updated_at"] = datetime.now()
    
    result = await db.templates.update_one(
        {"_id": ObjectId(template_id)},
        {"$set": update_data}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Template not found")
        
    return {"message": "Template updated successfully"}

@router.delete("/{template_id}")
async def delete_template(
    template_id: str,
    current_user: UserModel = Depends(require_role("owner", "admin")),
    db: ScopedDatabase = Depends(get_db)
):
    """DELETE: Remove a template"""
    if not ObjectId.is_valid(template_id):
        raise HTTPException(status_code=400, detail="Invalid Template ID")
        
    result = await db.templates.delete_one({"_id": ObjectId(template_id)})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Template not found")
        
    return {"message": "Template deleted successfully"}
