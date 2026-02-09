import asyncio
from database import configs_collection
from models.config import AgencyConfigModel, Vertical, VerticalField

DEFAULT_CONFIG = {
    "agency_id": "default",
    "status_options": [
        {"value": "enquiry", "label": "Enquiry", "color": "blue"},
        {"value": "booked", "label": "Booked", "color": "green"},
        {"value": "production", "label": "Production", "color": "purple"},
        {"value": "completed", "label": "Completed", "color": "gray"}
    ],
    "lead_sources": ["Instagram", "Referral", "Website", "Google"],
    "deliverable_types": ["Photo", "Video", "Album", "Reels"],
    "verticals": [
        {
            "id": "knots",
            "label": "Knots",
            "description": "Wedding Photography & Films",
            "fields": [
                {"name": "side", "label": "Side", "type": "select", "options": ["groom", "bride", "both"]},
                {"name": "groom_name", "label": "Groom Name", "type": "text"},
                {"name": "bride_name", "label": "Bride Name", "type": "text"},
                {"name": "groom_number", "label": "Groom Number", "type": "tel"},
                {"name": "bride_number", "label": "Bride Number", "type": "tel"},
                {"name": "groom_age", "label": "Groom Age", "type": "number"},
                {"name": "bride_age", "label": "Bride Age", "type": "number"},
                {"name": "groom_location", "label": "Groom Location", "type": "text"},
                {"name": "bride_location", "label": "Bride Location", "type": "text"},
                {"name": "wedding_style", "label": "Wedding Style", "type": "text"},
                {"name": "wedding_date", "label": "Wedding Date", "type": "date"}
            ]
        },
        {
            "id": "pluto",
            "label": "Pluto",
            "description": "Kids & Maternity",
            "fields": [
                {"name": "child_name", "label": "Child Name", "type": "text"},
                {"name": "child_age", "label": "Child Age", "type": "number"},
                {"name": "occasion_type", "label": "Occasion", "type": "select", "options": ["birthday", "baptism", "newborn", "other"]},
                {"name": "mother_name", "label": "Mother Name", "type": "text"},
                {"name": "father_name", "label": "Father Name", "type": "text"},
                {"name": "address", "label": "Address", "type": "text"}
            ]
        },
        {
            "id": "festia",
            "label": "Festia",
            "description": "Corporate & Events",
            "fields": [
                {"name": "event_scale", "label": "Scale", "type": "select", "options": ["private", "corporate", "mass"]},
                {"name": "company_name", "label": "Company Name", "type": "text"},
                {"name": "event_name", "label": "Event Name", "type": "text"},
                {"name": "venue", "label": "Venue", "type": "text"}
            ]
        },
        {
            "id": "thryv",
            "label": "Thryv",
            "description": "Commercial & Brands",
            "fields": [
                {"name": "company_name", "label": "Company Name", "type": "text"},
                {"name": "industry", "label": "Industry", "type": "text"},
                {"name": "campaign_type", "label": "Campaign Type", "type": "text"},
                {"name": "platforms", "label": "Platforms", "type": "text"}
            ]
        }
    ]
}

async def seed_config():
    print("⚙️ Seeding Agency Config...")
    
    # Seed for 'default' agency
    config_default = DEFAULT_CONFIG.copy()
    await configs_collection.update_one(
        {"agency_id": "default"},
        {"$set": config_default},
        upsert=True
    )
    print("✅ Config seeded for agency: default")

    # Seed for 'default_agency' (just in case)
    config_default_agency = DEFAULT_CONFIG.copy()
    config_default_agency["agency_id"] = "default_agency"
    await configs_collection.update_one(
        {"agency_id": "default_agency"},
        {"$set": config_default_agency},
        upsert=True
    )
    print("✅ Config seeded for agency: default_agency")

if __name__ == "__main__":
    asyncio.run(seed_config())
