
import asyncio
from database import users_collection, configs_collection
from models.user import UserModel
import uuid
from datetime import datetime
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

async def seed_multi_org():
    print("🌱 Seeding Multi-Org Test Users...")
    
    # Define test users with DIFFERENT agency_ids
    test_users = [
        {
            "name": "Alpha Owner",
            "email": "owner@alpha.com",
            "role": "owner",
            "picture": "https://api.dicebear.com/7.x/avataaars/svg?seed=alpha",
            "google_id": f"test_google_id_{uuid.uuid4()}",
            "agency_id": "agency_alpha" 
        },
        {
            "name": "Beta Owner",
            "email": "owner@beta.com",
            "role": "owner",
            "picture": "https://api.dicebear.com/7.x/avataaars/svg?seed=beta",
            "google_id": f"test_google_id_{uuid.uuid4()}",
            "agency_id": "agency_beta"
        }
    ]

    count = 0
    for user_data in test_users:
        # Check if user already exists
        existing = await users_collection.find_one({"email": user_data["email"]})
        if not existing:
            new_user = UserModel(**user_data)
            await users_collection.insert_one(new_user.model_dump(by_alias=True))
            print(f"✅ Added: {user_data['name']} ({user_data['agency_id']})")
            count += 1
        else:
            print(f"⚠️ Skipped (Exists): {user_data['name']}")

    # Also inspect configs to confirm they are distinct (optional, as they are created on demand)
    print("\nℹ️  Note: Configs for these agencies will be created automatically when you access Settings.")
    print(f"\n🎉 Seeding Complete! Added {count} new users.")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(seed_multi_org())
