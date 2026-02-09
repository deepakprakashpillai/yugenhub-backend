from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Literal
from datetime import datetime
import uuid


class NotificationModel(BaseModel):
    """In-app notification for task assignments and updates."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str  # Who receives the notification
    type: Literal['task_assigned', 'task_updated', 'mention', 'reminder'] = 'task_assigned'
    
    # Content
    title: str
    message: str
    
    # Reference
    resource_type: Optional[Literal['task', 'project', 'event']] = 'task'
    resource_id: Optional[str] = None  # ID of the task/project/event
    
    # State
    read: bool = False
    created_at: datetime = Field(default_factory=datetime.now)
    
    model_config = ConfigDict(populate_by_name=True)
