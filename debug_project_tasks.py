"""
Debug script to list tasks per project and find the one with 16 tasks.
"""
import asyncio
from database import tasks_collection, projects_collection

async def debug_tasks():
    print("🔍 Scanning projects for task counts...")
    
    # Aggregate tasks by project
    pipeline = [
        {"$group": {"_id": "$project_id", "count": {"$sum": 1}, "completed": {"$sum": {"$cond": [{"$eq": ["$status", "done"]}, 1, 0]}}}}
    ]
    
    stats = await tasks_collection.aggregate(pipeline).to_list(1000)
    
    for stat in stats:
        project_id = stat["_id"]
        total = stat["count"]
        completed = stat["completed"]
        
        # We are looking for 7/16
        if total == 16:
            # Fetch project details
            if project_id:
                project = await projects_collection.find_one({"_id": str(project_id)}) # ID might be ObjectId or Str
                if not project:
                     # Try ObjectId
                     from bson import ObjectId
                     try:
                        project = await projects_collection.find_one({"_id": ObjectId(project_id)})
                     except:
                        pass
                
                name = project.get("code") if project else "Unknown Project"
                print(f"✅ FOUND Project: {name} (ID: {project_id})")
                print(f"   Stats: {completed}/{total}")
                
                # List the tasks
                print("   Listing Tasks:")
                tasks = await tasks_collection.find({"project_id": str(project_id)}).to_list(20)
                for t in tasks:
                    print(f"    - {t.get('title')} [{t.get('status')}] (Type: {t.get('type')})")

if __name__ == "__main__":
    asyncio.run(debug_tasks())
