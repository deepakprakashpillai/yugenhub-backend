import asyncio
import os
from database import users_collection, tasks_collection, projects_collection
from dotenv import load_dotenv

load_dotenv()

async def fix_agency_ids():
    target_agency = "default_agency"
    print(f"🔧 Fixing Agency IDs to: '{target_agency}'...")

    # 1. Users
    res_users = await users_collection.update_many(
        {}, 
        {"$set": {"agency_id": target_agency}}
    )
    print(f"✅ Updated {res_users.modified_count} Users.")

    # 2. Tasks (uses studio_id)
    res_tasks = await tasks_collection.update_many(
        {}, 
        {"$set": {"studio_id": target_agency}}
    )
    print(f"✅ Updated {res_tasks.modified_count} Tasks.")

    # 3. Projects
    res_projects = await projects_collection.update_many(
        {}, 
        {"$set": {"agency_id": target_agency}}
    )
    print(f"✅ Updated {res_projects.modified_count} Projects.")

    print("\n🎉 Data reconciliation complete! All records belong to 'default_agency'.")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(fix_agency_ids())
