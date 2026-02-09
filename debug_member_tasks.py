import asyncio
from database import tasks_collection, users_collection
from datetime import datetime

async def debug_tasks():
    # 1. Find the member
    member = await users_collection.find_one({"role": "member"})
    if not member:
        print("No member found!")
        return

    uid = member.get("id")
    agency_id = member.get("agency_id")
    print(f"Assigning tasks to: {member.get('name')} (ID: {uid}, Agency: {agency_id})")
    
    # 2. Find ANY 3 overdue tasks (active)
    now = datetime.now()
    tasks = await tasks_collection.find({
        "due_date": {"$lt": now},
        "status": {"$ne": "done"}
    }).limit(3).to_list(None)
    
    if not tasks:
        print("No overdue tasks found in system!")
        return
        
    print(f"Found {len(tasks)} candidate tasks.")
    
    # 3. Update them
    for t in tasks:
        print(f" - Reassigning '{t.get('title')}' to {uid}")
        await tasks_collection.update_one(
            {"_id": t["_id"]}, 
            {"$set": {
                "assigned_to": uid, 
                "studio_id": agency_id # Ensure they are in same agency
            }}
        )
    
    print("Done! Please refresh the dashboard.")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    loop.run_until_complete(debug_tasks())
    loop.close()
