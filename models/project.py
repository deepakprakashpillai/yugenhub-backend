from pydantic import BaseModel, Field, ConfigDict, model_validator
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

import uuid

class DeliverableModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str
    quantity: int = 1
    due_date: Optional[datetime] = None
    incharge_id: Optional[str] = None
    notes: str = ""

class AssignmentModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    associate_id: str
    associate_name: Optional[str] = None
    role: str
    tags: List[str] = Field(default_factory=list)

class TeamRequirement(BaseModel):
    role: str
    count: int = 0

class EventModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str
    venue_name: Optional[str] = None
    venue_location: Optional[str] = None
    start_date: datetime
    end_date: Optional[datetime] = None
    calendar_event_id: Optional[str] = None
    deliverables: List[DeliverableModel] = Field(default_factory=list)
    assignments: List[AssignmentModel] = Field(default_factory=list)
    team_requirements: List[TeamRequirement] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode='after')
    def validate_date_order(self):
        if self.end_date and self.start_date and self.end_date <= self.start_date:
            raise ValueError('end_date must be after start_date')
        return self

class FeedbackEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    message: str
    author_type: str = "client"  # "client" | "team"
    author_name: Optional[str] = None
    file_id: Optional[str] = None  # When set, feedback is scoped to a specific file
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class FileVersion(BaseModel):
    version: int
    file_name: str
    content_type: str
    uploaded_by: Optional[str] = None
    uploaded_on: datetime
    change_notes: str = ""

class DeliverableFile(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    file_name: str
    content_type: str
    r2_key: str
    r2_url: str
    uploaded_on: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Thumbnail fields
    thumbnail_r2_key: Optional[str] = None
    thumbnail_r2_url: Optional[str] = None
    thumbnail_status: str = "pending"  # pending | processing | done | failed | n/a
    # Watermark fields
    watermark_r2_key: Optional[str] = None
    watermark_r2_url: Optional[str] = None
    watermark_status: str = "pending"  # pending | processing | done | failed | n/a
    # Preview fields (1920px JPEG for gallery — images only)
    preview_r2_key: Optional[str] = None
    preview_r2_url: Optional[str] = None
    preview_status: str = "n/a"  # pending | processing | done | failed | n/a
    # Versioning fields
    version: int = 1
    previous_versions: List[FileVersion] = Field(default_factory=list)

class PortalDeliverableModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: str = ""
    event_id: Optional[str] = None
    deliverable_id: Optional[str] = None  # Links to DeliverableModel.id within an event
    task_id: Optional[str] = None  # FK → TaskModel.id
    files: List[DeliverableFile] = Field(default_factory=list)
    status: str = "Pending"  # Pending | Uploaded | Approved | Changes Requested
    feedback: List[FeedbackEntry] = Field(default_factory=list)
    # Download limit fields
    max_downloads: Optional[int] = None  # None = unlimited
    download_count: int = 0
    downloads_disabled: bool = False
    created_on: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_on: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class ProjectModel(BaseModel):
    code: Optional[str] = None
    agency_id: str = "default"
    vertical: str
    client_id: str
    status: str = "enquiry"
    lead_source: str = "Other"
    events: List[EventModel] = Field(default_factory=list)
    assignments: List[AssignmentModel] = Field(default_factory=list)  # Project-level team (for non-event verticals)
    portal_token: Optional[str] = None
    portal_deliverables: List[PortalDeliverableModel] = Field(default_factory=list)
    # Portal settings
    portal_watermark_enabled: bool = False
    portal_watermark_text: Optional[str] = None  # Falls back to org_name
    portal_default_download_limit: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_on: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_on: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(populate_by_name=True)