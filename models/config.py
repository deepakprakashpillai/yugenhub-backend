from pydantic import BaseModel, Field
from typing import List, Dict, Any

class VerticalField(BaseModel):
    name: str
    label: str
    type: str
    options: List[str] = []

class Vertical(BaseModel):
    id: str
    label: str
    description: str
    fields: List[VerticalField]

class AgencyConfigModel(BaseModel):
    agency_id: str = "default" # For future SaaS multi-tenancy
    status_options: List[Dict[str, str]]
    lead_sources: List[str]
    deliverable_types: List[str]
    verticals: List[Vertical]