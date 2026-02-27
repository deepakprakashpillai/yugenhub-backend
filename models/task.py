from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Literal, Any
from datetime import datetime, timezone
import uuid

class TaskHistoryModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str
    changed_by: str  # user_id
    field: str      # e.g., "status", "priority", "assignee"
    old_value: Optional[Any] = None
    new_value: Optional[Any] = None
    comment: Optional[str] = None
    studio_id: str # Added for direct agency filtering
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(populate_by_name=True)


class TaskModel(BaseModel):
    # Core Fields
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: Optional[str] = None
    quantity: Optional[int] = 1 # Added for deliverables (e.g., number of photos)
    
    # Types & Categories
    type: Literal['internal', 'project'] = 'internal' # 'internal' = Studio Wide, 'project' = Project Specific
    category: Literal['general', 'deliverable'] = 'general' # 'general' = Internal Project Work, 'deliverable' = Client Output
    
    # Relations
    project_id: Optional[str] = None  # Required if type='project'
    event_id: Optional[str] = None    # Optional: Linked to a specific event
    studio_id: str = "default_agency" # agency_id
    
    # State
    status: Literal['todo', 'in_progress', 'review', 'blocked', 'done'] = 'todo'
    priority: Literal['low', 'medium', 'high', 'urgent'] = 'medium'
    
    # Assignment
    assigned_to: Optional[str] = None # user_id
    created_by: Optional[str] = None # user_id (Set by backend)
    
    # Timing
    due_date: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(
        populate_by_name=True,
        use_enum_values=True
    )
