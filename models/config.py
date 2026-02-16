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


class SubCategory(BaseModel):
    id: str
    name: str


class FinanceCategory(BaseModel):
    id: str
    name: str
    type: str  # 'income' or 'expense'
    subcategories: List[SubCategory] = []


class AgencyConfigModel(BaseModel):
    agency_id: str = "default"

    # Organisation info
    org_name: Optional[str] = "My Agency"
    org_email: Optional[str] = ""
    org_phone: Optional[str] = ""

    # Theme config
    theme_mode: Optional[str] = "dark"  # light, dark, system
    accent_color: Optional[str] = "#ef4444"  # Default red-500

    # Workflow config
    status_options: List[Dict[str, str]] = []
    lead_sources: List[str] = []
    deliverable_types: List[str] = []

    # Finance config
    finance_categories: List[FinanceCategory] = []

    # Verticals
    verticals: List[Vertical] = []