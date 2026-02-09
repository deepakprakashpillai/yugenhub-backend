from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime

import uuid

class DeliverableModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str
    quantity: int = 1
    due_date: Optional[datetime] = None
    incharge_id: Optional[str] = None
    status: str = "Pending"
    notes: str = ""

class AssignmentModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    associate_id: str
    associate_name: Optional[str] = None
    role: str

class EventModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str
    venue_name: Optional[str] = None
    venue_location: Optional[str] = None
    start_date: datetime
    end_date: Optional[datetime] = None
    deliverables: List[DeliverableModel] = Field(default_factory=list)
    assignments: List[AssignmentModel] = Field(default_factory=list)
    notes: str = ""

class ProjectModel(BaseModel):
    code: str
    agency_id: str = "default"
    vertical: str
    client_id: str
    status: str = "enquiry"
    lead_source: str = "Other"
    events: List[EventModel] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_on: datetime = Field(default_factory=datetime.now)
    updated_on: datetime = Field(default_factory=datetime.now)

    model_config = ConfigDict(populate_by_name=True)