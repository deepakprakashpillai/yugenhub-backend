from pydantic import BaseModel


class NotificationPrefsModel(BaseModel):
    user_id: str
    agency_id: str

    # In-app notification toggles
    task_assigned: bool = True
    task_updated: bool = True
    project_created: bool = True
    project_completed: bool = True
    mentions: bool = True

    # Email notification toggle
    email_notifications: bool = False

    # Push notifications toggle
    push_notifications: bool = True
