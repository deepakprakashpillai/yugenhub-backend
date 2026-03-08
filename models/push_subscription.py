from pydantic import BaseModel, Field
from datetime import datetime, timezone

class PushSubscriptionKeys(BaseModel):
    p256dh: str
    auth: str

class PushSubscriptionModel(BaseModel):
    user_id: str
    agency_id: str
    endpoint: str
    keys: PushSubscriptionKeys
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
