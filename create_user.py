import asyncio
import os
from database import users_collection
from models.user import UserModel
from datetime import datetime

async def add_user(email: str, name: str):
    # Check if exists
    existing = await users_collection.find_one({"email": email})
    if existing:
        print(f"User {email} already exists.")
        return

    # Create new user
    new_user = UserModel(
        google_id="", # Will be linked on first login
        email=email,
        name=name,
        agency_id="default_agency",
        role="owner",
        created_at=datetime.now(),
        last_login=datetime.now()
    )

    await users_collection.insert_one(new_user.model_dump())
    print(f"✅ Successfully added user: {email}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='Add a user to the whitelist')
    parser.add_argument('email', type=str, help='Email address to whitelist')
    parser.add_argument('--name', type=str, default='Admin User', help='User name')
    
    args = parser.parse_args()
    
    loop = asyncio.get_event_loop()
    loop.run_until_complete(add_user(args.email, args.name))
