from pydantic import BaseModel, Field, EmailStr, ConfigDict
from typing import Optional, Literal, List
from datetime import datetime
import uuid

class UserModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    google_id: Optional[str] = None
    email: EmailStr
    name: str
    picture: Optional[str] = None
    phone: Optional[str] = None
    agency_id: str
    role: Literal['owner', 'admin', 'member'] = "owner"
    status: str = "active"
    created_at: datetime = Field(default_factory=datetime.now)
    last_login: Optional[datetime] = None
    # RBAC: Per-user access control
    allowed_verticals: List[str] = Field(default_factory=list)  # Empty = all verticals
    finance_access: bool = False  # Explicit finance module access (owner/admin get it by default via role)
    can_manage_team: bool = False # Granular permission for Admins to invite/remove/edit members

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        extra="ignore"
    )
