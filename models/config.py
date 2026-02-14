from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional


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
    agency_id: str = "default"

    # Organisation info
    org_name: Optional[str] = "My Agency"
    org_email: Optional[str] = ""
    org_phone: Optional[str] = ""

    # Workflow config
    status_options: List[Dict[str, str]] = []
    lead_sources: List[str] = []
    deliverable_types: List[str] = []

    # Verticals
    verticals: List[Vertical] = []