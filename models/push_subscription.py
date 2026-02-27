from pydantic import BaseModel
from datetime import datetime

class PushSubscriptionKeys(BaseModel):
    p256dh: str
    auth: str

class PushSubscriptionModel(BaseModel):
    user_id: str
    agency_id: str
    endpoint: str
    keys: PushSubscriptionKeys
    created_at: datetime = datetime.now()
