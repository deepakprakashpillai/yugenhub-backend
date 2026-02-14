from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid
from .project import DeliverableModel, AssignmentModel

class TemplateEventModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str
    venue_name: Optional[str] = None
    venue_location: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    deliverables: List[DeliverableModel] = Field(default_factory=list)
    assignments: List[AssignmentModel] = Field(default_factory=list)
    notes: str = ""

class ProjectTemplateModel(BaseModel):
    id: Optional[str] = Field(None, alias="_id")
    agency_id: Optional[str] = None
    vertical: str
    name: str
    description: Optional[str] = None
    events: List[TemplateEventModel] = []
    metadata: Dict[str, Any] = {}
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "vertical": "knots",
                "name": "Standard Hindu Wedding",
                "description": "Includes Haldi, Mehendi, Sangeet, and Wedding",
                "events": [],
                "metadata": {"religion": "Hindu"}
            }
        }
