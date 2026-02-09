"""
Migration script to sync Project Deliverables to Tasks Collection.
This ensures that all deliverables are tracked as Tasks and contribute to Project Progress.
"""
import asyncio
from database import projects_collection, tasks_collection
from models.task import TaskModel
from datetime import datetime

async def sync_deliverables():
    print("🔄 Starting Deliverable -> Task Sync...")
    
    projects_cursor = projects_collection.find({})
    
    count_created = 0
    count_skipped = 0
    
    async for project in projects_cursor:
        project_id = str(project["_id"])
        project_code = project.get("code", "PROJECT")
        agency_id = project.get("agency_id")
        created_by = project.get("created_by")
        
        events = project.get("events", [])
        
        for event in events:
            event_id = event.get("id")
            event_type = event.get("type", "Event")
            
            deliverables = event.get("deliverables", [])
            
            for deliverable in deliverables:
                del_type = deliverable.get("type", "Deliverable")
                task_title = f"{del_type} ({event_type})"
                
                # Check if task already exists (fuzzy match to avoid duplicates)
                # We check by project_id and exact title
                existing_task = await tasks_collection.find_one({
                    "project_id": project_id,
                    "title": task_title,
                    "event_id": event_id
                })
                
                if existing_task:
                    count_skipped += 1
                    continue
                
                # Create Task
                # Map deliverable fields to Task fields
                task = TaskModel(
                    title=task_title,
                    description=f"Deliverable for {event_type} (Synced)",
                    project_id=project_id,
                    event_id=event_id,
                    status="todo", # Default
                    priority="medium",
                    due_date=deliverable.get("due_date"),
                    assigned_to=deliverable.get("incharge_id"),
                    studio_id=agency_id,
                    created_by=created_by or "system",
                    type="project",
                    category="deliverable"
                )
                
                await tasks_collection.insert_one(task.model_dump())
                print(f"   ➕ Created Task: {task_title} for {project_code}")
                count_created += 1

    print(f"\n✅ Sync Complete!")
    print(f"   Created: {count_created}")
    print(f"   Skipped: {count_skipped}")

if __name__ == "__main__":
    asyncio.run(sync_deliverables())
