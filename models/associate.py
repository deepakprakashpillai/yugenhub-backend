# models/associate.py
from pydantic import BaseModel, Field, EmailStr, ConfigDict # Add ConfigDict here
from typing import Optional, Literal
from datetime import datetime
from bson import ObjectId

class AssociateModel(BaseModel):
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

    # NEW Pydantic V2 Syntax
    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str}
    )