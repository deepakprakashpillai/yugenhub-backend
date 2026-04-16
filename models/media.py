from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from datetime import datetime, timezone
import uuid


class MediaFolder(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agency_id: str
    name: str
    parent_id: Optional[str] = None          # None = root level
    path: str = "/"                           # Materialised path, e.g. "/Deliverables/Project A/"
    is_system: bool = False                   # True = auto-created (cannot be renamed/deleted by users)
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True, extra="ignore")


class MediaItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agency_id: str
    folder_id: str                            # FK → MediaFolder.id
    name: str                                 # Display name (editable)
    r2_key: str
    r2_url: str
    content_type: str
    size_bytes: int = 0

    # Derived assets (populated by background media processing)
    thumbnail_r2_key: Optional[str] = None
    thumbnail_r2_url: Optional[str] = None
    thumbnail_status: str = "pending"         # pending | processing | done | failed | n/a
    preview_r2_key: Optional[str] = None
    preview_r2_url: Optional[str] = None
    preview_status: str = "n/a"              # pending | processing | done | failed | n/a
    watermark_r2_key: Optional[str] = None
    watermark_r2_url: Optional[str] = None
    watermark_status: str = "n/a"            # pending | processing | done | failed | n/a

    # Source tracking
    source: str = "direct"                   # direct | deliverable | album
    source_project_id: Optional[str] = None
    source_deliverable_id: Optional[str] = None
    source_album_id: Optional[str] = None

    # External sharing
    share_token: Optional[str] = None
    share_expires_at: Optional[datetime] = None

    # Upload lifecycle — item is created before R2 upload, activated after
    status: str = "pending"                  # pending | active

    uploaded_by: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True, extra="ignore")
