import sys
import os
import asyncio
from pymongo import ASCENDING, DESCENDING

# Add parent directory to path to import database
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import notifications_collection, projects_collection, tasks_collection, users_collection, task_history_collection
from logging_config import get_logger

logger = get_logger("setup_indexes")

async def create_indexes():
    print("🚀 Starting Index Creation...")

    # --- Notifications ---
    print("\n📦 Notifications Collection:")
    # For Unread Count: find({user_id: X, read: False})
    await notifications_collection.create_index([("user_id", ASCENDING), ("read", ASCENDING)])
    print("✅ Created index: (user_id, read)")
    
    # For List Notifications: find({user_id: X}).sort(created_at: -1)
    await notifications_collection.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    print("✅ Created index: (user_id, created_at DESC)")

    # --- Projects ---
    print("\n📦 Projects Collection:")
    # For Listing: find({agency_id: X}).sort(created_on: -1)
    await projects_collection.create_index([("agency_id", ASCENDING), ("created_on", DESCENDING)])
    print("✅ Created index: (agency_id, created_on DESC)")
    
    # For Searching: find({title: regex}) - Text index might be better but simple index helps prefix regex
    await projects_collection.create_index([("title", ASCENDING)])
    print("✅ Created index: (title)")

    # --- Tasks ---
    print("\n📦 Tasks Collection:")
    # For Board View: find({project_id: X})
    await tasks_collection.create_index([("project_id", ASCENDING)])
    print("✅ Created index: (project_id)")
    
    # For My Tasks: find({assigned_to: X})
    await tasks_collection.create_index([("assigned_to", ASCENDING)])
    print("✅ Created index: (assigned_to)")
    
    # For Filtering: find({studio_id: X, status: Y})
    await tasks_collection.create_index([("studio_id", ASCENDING), ("status", ASCENDING)])
    print("✅ Created index: (studio_id, status)")

    # --- Users ---
    print("\n📦 Users Collection:")
    # Email lookup is frequent for auth
    await users_collection.create_index([("email", ASCENDING)], unique=True)
    print("✅ Created index: (email UNIQUE)")

    # --- History ---
    print("\n📦 Task History Collection:")
    # History lookup by task
    await task_history_collection.create_index([("task_id", ASCENDING), ("timestamp", DESCENDING)])
    print("✅ Created index: (task_id, timestamp DESC)")

    print("\n✨ All indexes created successfully!")

if __name__ == "__main__":
    # Ensure event loop for async driver
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(create_indexes())
