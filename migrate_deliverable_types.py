"""
Migration script: Ensure all deliverables (both event-level and task-level) have a proper 'type' field.

- Event-level deliverables (in projects.events[].deliverables[]) already have a 'type' field in the model.
  This script sets it to 'Unspecified' if it's missing or empty.

- Task-level deliverables (tasks with category='deliverable') use 'title' as the type name.
  This script sets 'title' to 'Unspecified' if it's missing or empty.

Usage:
    python migrate_deliverable_types.py
"""

import asyncio
import os
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = "yugen_hub"  # Updated to match the DB name in the URI
DEFAULT_TYPE = "Unspecified"


async def migrate():
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]

    # 1. Fix event-level deliverables (projects.events[].deliverables[].type)
    projects = db["projects"]
    project_count = 0
    deliverable_count = 0

    async for project in projects.find({"events": {"$exists": True, "$ne": []}}):
        updated = False
        events = project.get("events", [])
        for event in events:
            for deliverable in event.get("deliverables", []):
                if not deliverable.get("type"):
                    deliverable["type"] = DEFAULT_TYPE
                    updated = True
                    deliverable_count += 1

        if updated:
            await projects.update_one(
                {"_id": project["_id"]},
                {"$set": {"events": events}}
            )
            project_count += 1

    print(f"[Event Deliverables] Updated {deliverable_count} deliverables across {project_count} projects.")

    # 2. Fix task-level deliverables (tasks with category='deliverable' and missing title)
    tasks = db["tasks"]
    result = await tasks.update_many(
        {
            "category": "deliverable",
            "$or": [
                {"title": {"$exists": False}},
                {"title": ""},
                {"title": None}
            ]
        },
        {"$set": {"title": DEFAULT_TYPE}}
    )
    print(f"[Task Deliverables] Updated {result.modified_count} tasks with missing titles.")

    client.close()
    print("Migration complete.")


if __name__ == "__main__":
    asyncio.run(migrate())
