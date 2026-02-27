# models/client.py
from pydantic import BaseModel, Field, EmailStr, ConfigDict, field_validator
from typing import Optional, Literal
from datetime import datetime

class ClientModel(BaseModel):
    id: Optional[str] = None
    agency_id: str = "default"
    name: str
    phone: str
    email: Optional[EmailStr] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    
    total_projects: int = 0
    
    # Enum translation
    type: Literal['Lead', 'Active Client', 'Legacy', 'Agency'] = 'Lead'
    
    # Mongoose timestamps: true equivalent
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @field_validator('email', mode='before')
    @classmethod
    def empty_str_to_none(cls, v):
        if v == "":
            return None
        return v

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        extra='ignore'
    )