# models/associate.py
from pydantic import BaseModel, Field, EmailStr, ConfigDict, field_validator
from typing import Optional, Literal, Any
from datetime import datetime
from bson import ObjectId

class AssociateModel(BaseModel):
    id: Optional[str] = None
    # Keep your fields exactly the same as before...
    agency_id: str = "default"
    name: str
    phone_number: str
    email_id: Optional[EmailStr] = None
    base_city: Optional[str] = "Not Set"
    primary_role: Literal['Photographer', 'Cinematographer', 'Editor', 'Drone Pilot', 'Lead', 'Assistant']
    employment_type: Literal['In-house', 'Freelance', 'Contract'] = 'Freelance'
    is_active: bool = True
    linked_user_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)

    @field_validator("email_id", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: Any) -> Any:
        if v == "":
            return None
        return v

    # NEW Pydantic V2 Syntax
    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str},
        extra="ignore"
    )