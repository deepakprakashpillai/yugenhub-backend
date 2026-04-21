from pydantic import BaseModel, Field, ConfigDict
from typing import List, Dict
from datetime import datetime, timezone
from models.communication import ALL_ALERT_TYPES


class ClientAlertOverride(BaseModel):
    excluded: bool = False            # if True, never send anything to this client
    disabled_types: List[str] = Field(default_factory=list)  # specific types suppressed


class OperatorAlertOverride(BaseModel):
    excluded: bool = False            # if True, hide all communications from this operator
    hidden_types: List[str] = Field(default_factory=list)    # specific types hidden from this operator


class CommunicationSettings(BaseModel):
    agency_id: str
    globally_enabled_types: List[str] = Field(default_factory=lambda: list(ALL_ALERT_TYPES))
    client_overrides: Dict[str, ClientAlertOverride] = Field(default_factory=dict)
    operator_overrides: Dict[str, OperatorAlertOverride] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        extra="ignore",
    )
