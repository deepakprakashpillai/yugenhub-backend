
import asyncio
from database import users_collection
from models.user import UserModel
import uuid
from datetime import datetime
from dotenv import load_dotenv
import os

# Load environment variables (for DB connection string)
load_dotenv()

async def seed_users():
    print("🌱 Seeding Test Users...")
    
    # Define test users with different roles
    test_users = [
        {
            "name": "Project Manager (Test)",
            "email": "pm@test.com",
            "role": "admin",
            "picture": "https://api.dicebear.com/7.x/avataaars/svg?seed=pm",
            "google_id": f"test_google_id_{uuid.uuid4()}",
            "agency_id": "default_agency" # Ensure this matches your main agency ID
        },
        {
            "name": "Senior Editor (Test)",
            "email": "editor@test.com",
            "role": "member",
            "picture": "https://api.dicebear.com/7.x/avataaars/svg?seed=editor",
            "google_id": f"test_google_id_{uuid.uuid4()}",
            "agency_id": "default_agency"
        },
        {
            "name": "Client Service (Test)",
            "email": "cs@test.com",
            "role": "member",
            "picture": "https://api.dicebear.com/7.x/avataaars/svg?seed=cs",
            "google_id": f"test_google_id_{uuid.uuid4()}",
            "agency_id": "default_agency"
        },
        {
            "name": "Super Admin (Test)",
            "email": "admin@test.com",
            "role": "owner",
            "picture": "https://api.dicebear.com/7.x/avataaars/svg?seed=admin",
            "google_id": f"test_google_id_{uuid.uuid4()}",
            "agency_id": "default_agency"
        }
    ]

    count = 0
    for user_data in test_users:
        # Check if user already exists to avoid duplicates
        existing = await users_collection.find_one({"email": user_data["email"]})
        if not existing:
            new_user = UserModel(**user_data)
            await users_collection.insert_one(new_user.model_dump(by_alias=True))
            print(f"✅ Added: {user_data['name']}")
            count += 1
        else:
            print(f"⚠️ Skipped (Exists): {user_data['name']}")

    print(f"\n🎉 Seeding Complete! Added {count} new users.")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(seed_users())
