"""
Migration Script: Sync In-house Associates to Users Collection

Run this script once to create User records for all existing In-house Associates.
This allows them to log in via Google Sign-In.

Usage:
    cd yugenhub-backend
    source venv/bin/activate
    python scripts/sync_inhouse_users.py
"""

import asyncio
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime
import uuid

# Load environment variables
load_dotenv()

# MongoDB Connection (same as database.py)
MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    print("❌ ERROR: MONGO_URI not found in .env file!")
    sys.exit(1)

client = AsyncIOMotorClient(MONGO_URI, tlsAllowInvalidCertificates=True)
db = client["yugen_hub"]  # Same database name as app

associates_collection = db["associates"]
users_collection = db["users"]


async def sync_inhouse_associates():
    """
    Find all In-house Associates and ensure they have corresponding User records.
    """
    print("=" * 60)
    print("SYNC IN-HOUSE ASSOCIATES TO USERS")
    print("=" * 60)
    
    # Find all In-house associates with email
    query = {
        "employment_type": "In-house",
        "email_id": {"$exists": True, "$ne": None, "$ne": ""}
    }
    
    inhouse_associates = await associates_collection.find(query).to_list(1000)
    print(f"\nFound {len(inhouse_associates)} In-house Associates with email.\n")
    
    created_count = 0
    skipped_count = 0
    error_count = 0
    
    for associate in inhouse_associates:
        email = associate.get("email_id")
        name = associate.get("name", "Unknown")
        agency_id = associate.get("agency_id", "default")
        associate_id = str(associate.get("_id"))
        
        if not email:
            print(f"  ⚠️  Skipping {name}: No email")
            skipped_count += 1
            continue
        
        # Check if user already exists
        existing_user = await users_collection.find_one({"email": email})
        
        if existing_user:
            print(f"  ✓  {name} <{email}>: User already exists")
            skipped_count += 1
            continue
        
        # Create new user
        try:
            new_user = {
                "id": str(uuid.uuid4()),
                "google_id": "",  # Will be set on first Google Sign-In
                "email": email,
                "name": name,
                "picture": None,
                "agency_id": agency_id,
                "role": "member",
                "associate_id": associate_id,  # Link back to associate
                "created_at": datetime.now(),
                "last_login": datetime.now()
            }
            
            await users_collection.insert_one(new_user)
            print(f"  ✅ {name} <{email}>: User CREATED")
            created_count += 1
            
        except Exception as e:
            print(f"  ❌ {name} <{email}>: ERROR - {e}")
            error_count += 1
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Created: {created_count}")
    print(f"  Skipped: {skipped_count}")
    print(f"  Errors:  {error_count}")
    print("=" * 60)
    
    return created_count, skipped_count, error_count


if __name__ == "__main__":
    asyncio.run(sync_inhouse_associates())
