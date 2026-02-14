from pydantic import BaseModel, Field, EmailStr, ConfigDict
from typing import Optional, Literal
from datetime import datetime
import uuid

class UserModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    google_id: str
    email: EmailStr
    name: str
    picture: Optional[str] = None
    phone: Optional[str] = None
    agency_id: str
    role: Literal['owner', 'admin', 'member'] = "owner"
    created_at: datetime = Field(default_factory=datetime.now)
    last_login: datetime = Field(default_factory=datetime.now)

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True
    )
