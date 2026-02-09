"""
Script to cleanup junk tasks for Project KN-8801.
"""
import asyncio
from database import tasks_collection, projects_collection

async def cleanup_tasks():
    # 1. Find project KN-8801
    project = await projects_collection.find_one({"code": "KN-8801"})
    if not project:
        print("Project KN-8801 not found!")
        return

    project_id = str(project["_id"])
    print(f"Project ID: {project_id}")
    
    # 2. Delete tasks linked to this project
    result = await tasks_collection.delete_many({"project_id": project_id})
    print(f"🗑️ Deleted {result.deleted_count} tasks for KN-8801.")

if __name__ == "__main__":
    asyncio.run(cleanup_tasks())
