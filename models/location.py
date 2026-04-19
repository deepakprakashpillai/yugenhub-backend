from pydantic import BaseModel, Field
from typing import Optional
import uuid


class MapLocation(BaseModel):
    address: Optional[str] = None            # raw user input / search term
    formatted_address: Optional[str] = None  # Google canonical string
    lat: Optional[float] = None
    lng: Optional[float] = None
    place_id: Optional[str] = None
    maps_url: Optional[str] = None           # canonical share URL
    source: Optional[str] = None             # "places" | "url_paste" | "manual"


class LinkedLocation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    map: Optional[MapLocation] = None
    notes: str = ""
