import asyncio
import os
import uuid
import certifi
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ==========================================
# CONFIGURATION - CHANGE THESE VALUES
# ==========================================

# 1. MongoDB Configuration (Loaded from .env)
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME", "yugen_hub_db_prod")

if not MONGO_URI:
    print("❌ ERROR: MONGO_URI is missing from your .env file!")
    exit(1)

# 2. Your Initial Owner Details
OWNER_EMAIL = "your.email@example.com"
OWNER_NAME = "Your Full Name"
AGENCY_NAME = "Your Agency Name"

# ==========================================
# SCRIPT - DO NOT CHANGE BEYOND THIS POINT
# ==========================================

async def bootstrap_db():
    print(f"Connecting to MongoDB: {MONGO_URI[:20]}...")
    client = AsyncIOMotorClient(MONGO_URI, tlsCAFile=certifi.where())
    db = client[DB_NAME]
    
    users_collection = db["users"]
    configs_collection = db["agency_configs"]
    
    # 1. Check if user already exists
    existing_user = await users_collection.find_one({"email": OWNER_EMAIL})
    if existing_user:
        print(f"User {OWNER_EMAIL} already exists in the database. Exiting.")
        return

    # 2. Generate initial Agency ID
    agency_id = str(uuid.uuid4())
    print(f"Generated new Agency ID: {agency_id}")

    # 3. Create the Owner User Document
    user_doc = {
        "id": str(uuid.uuid4()),
        "name": OWNER_NAME,
        "email": OWNER_EMAIL,
        "role": "owner",
        "agency_id": agency_id,
        "status": "active",
        "picture": None,
        "created_at": datetime.now(),
        "last_login": None,
        "google_id": None # Will be linked on first OAuth login
    }
    
    await users_collection.insert_one(user_doc)
    print(f"✅ Successfully inserted Owner account for {OWNER_EMAIL}")

    # 4. Seed the Agency Configuration
    # This ensures the first login doesn't crash from missing settings
    agency_config_doc = {
        "agency_id": agency_id,
        "org_name": AGENCY_NAME,
        "org_email": OWNER_EMAIL,
        "org_phone": "",
        "theme_mode": "dark",
        "accent_color": "#ffffff",
        "verticals": [
            {
                "id": "wedding_photography",
                "label": "Wedding Photography",
                "color": "#ef4444"
            }
        ],
        "project_statuses": [
            {"id": "enquiry", "label": "Enquiry", "color": "#fbbf24"},
            {"id": "booked", "label": "Booked", "color": "#60a5fa"},
            {"id": "ongoing", "label": "Ongoing", "color": "#3b82f6"},
            {"id": "completed", "label": "Completed", "color": "#22c55e"},
            {"id": "cancelled", "label": "Cancelled", "color": "#ef4444"}
        ],
        "lead_sources": [
            {"id": "instagram", "label": "Instagram"}
        ],
        "deliverable_types": [
            {"id": "traditional_photos", "label": "Traditional Photos"}
        ],
        "associate_roles": [
            {"id": "lead_photographer", "label": "Lead Photographer"}
        ],
        "finance_categories": [
            {"id": "software_subscriptions", "label": "Software & Subscriptions", "type": "expense"},
            {"id": "project_income", "label": "Project Income", "type": "income"}
        ]
    }

    await configs_collection.insert_one(agency_config_doc)
    print(f"✅ Successfully initialized Agency Config for '{AGENCY_NAME}'")
    print("\n🎉 Bootstrap Complete! You can now log into your production app using Google Auth.")

if __name__ == "__main__":
    asyncio.run(bootstrap_db())
