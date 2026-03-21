from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Literal
from datetime import datetime, timezone
import uuid


class AlbumFileModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    file_name: str
    content_type: str
    r2_key: str
    width: Optional[int] = None
    height: Optional[int] = None
    size_bytes: Optional[int] = None
    sort_order: int = 0
    imported_from_deliverable_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AlbumTabModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str = "Gallery"
    sort_order: int = 0
    files: List[AlbumFileModel] = Field(default_factory=list)


class LandingPageConfig(BaseModel):
    hero_image_r2_key: Optional[str] = None
    title: Optional[str] = None
    subtitle: Optional[str] = None
    font_pair: str = "modern"
    color_scheme: str = "dark"

    # Hero positioning per device — {x: float, y: float, scale: float}
    hero_position_mobile: Optional[dict] = None
    hero_position_tablet: Optional[dict] = None
    hero_position_desktop: Optional[dict] = None

    # Text position — 9-point grid value (global fallback + per-device overrides)
    text_position: str = "center"
    text_position_mobile: Optional[str] = None
    text_position_tablet: Optional[str] = None
    text_position_desktop: Optional[str] = None

    # Overlay — {type, color, opacity, gradient_direction, gradient_end_color}
    overlay: Optional[dict] = None

    # Title/subtitle typography — {color, size, size_mobile, size_tablet, size_desktop, letter_spacing, uppercase, text_shadow}
    title_style: Optional[dict] = None
    subtitle_style: Optional[dict] = None

    # Hero section
    hero_height: str = "full"
    show_scroll_indicator: bool = True
    theme_preset: Optional[str] = None

    # Phase 2
    gallery_layout: str = "masonry"             # masonry | grid | columns
    text_animation: str = "none"                # none | fade | slide-up | fade-up
    logo_r2_key: Optional[str] = None
    vignette: Optional[dict] = None             # {enabled: bool, intensity: 0-100}
    footer_config: Optional[dict] = None        # {show_footer, brand_name, tagline, contact_email, phone, instagram_handle, website_url}


class AlbumModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agency_id: str = "default"
    project_id: Optional[str] = None
    client_id: Optional[str] = None
    vertical: Optional[str] = None
    title: str
    description: Optional[str] = None
    slug: str = ""
    status: Literal["draft", "published", "expired"] = "draft"
    password_hash: Optional[str] = None
    download_enabled: bool = True
    ttl_duration: Optional[int] = None  # Days, None = never expires
    published_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    cover_image_r2_key: Optional[str] = None
    landing_page: LandingPageConfig = Field(default_factory=LandingPageConfig)
    tabs: List[AlbumTabModel] = Field(default_factory=list)
    view_count: int = 0
    unique_view_count: int = 0
    download_count: int = 0
    last_viewed_at: Optional[datetime] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(populate_by_name=True)


class AlbumAnalyticsEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    album_id: str
    agency_id: str
    event_type: Literal["view", "tab_view", "download", "bulk_download"] = "view"
    tab_id: Optional[str] = None
    file_id: Optional[str] = None
    viewer_fingerprint: Optional[str] = None  # sha256(ip+ua)
    ip_hash: Optional[str] = None
    user_agent: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(populate_by_name=True)
