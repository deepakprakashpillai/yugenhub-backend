"""
One-time migration script to backfill FK links between:
  - Tasks (category=deliverable)
  - Event Deliverables (DeliverableModel)
  - Portal Deliverables (PortalDeliverableModel)

Run: cd backend && python -m scripts.migrate_deliverable_links
"""

import asyncio
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import db
from bson import ObjectId
from datetime import datetime, timezone
from models.project import PortalDeliverableModel


async def migrate():
    projects_col = db.projects
    tasks_col = db.tasks

    # Step 1: For tasks with metadata.deliverable_id -> copy to task.deliverable_id
    cursor = tasks_col.find({"category": "deliverable", "metadata.deliverable_id": {"$exists": True}})
    migrated_tasks = 0
    async for task in cursor:
        deliv_id = task.get("metadata", {}).get("deliverable_id")
        if deliv_id and not task.get("deliverable_id"):
            await tasks_col.update_one(
                {"_id": task["_id"]},
                {"$set": {"deliverable_id": deliv_id}}
            )
            migrated_tasks += 1
    print(f"Step 1: Migrated {migrated_tasks} tasks from metadata.deliverable_id to task.deliverable_id")

    # Step 2: For tasks without deliverable_id, match by (event_id, title pattern)
    cursor = tasks_col.find({
        "category": "deliverable",
        "$or": [
            {"deliverable_id": None},
            {"deliverable_id": {"$exists": False}},
        ]
    })
    matched_tasks = 0
    async for task in cursor:
        event_id = task.get("event_id")
        if not event_id or not task.get("project_id"):
            continue

        # Extract base type from task title
        title = task.get("title", "")
        if " (" in title:
            title = title.rsplit(" (", 1)[0]
        if not title:
            continue

        # Find matching project event deliverable
        project = await projects_col.find_one(
            {"_id": ObjectId(task["project_id"])},
            {"events": 1}
        )
        if not project:
            continue

        for event in project.get("events", []):
            if event.get("id") != event_id:
                continue
            for deliverable in event.get("deliverables", []):
                if deliverable.get("type", "").lower() == title.lower():
                    await tasks_col.update_one(
                        {"_id": task["_id"]},
                        {"$set": {"deliverable_id": deliverable["id"]}}
                    )
                    matched_tasks += 1
                    break
            break
    print(f"Step 2: Matched {matched_tasks} tasks to event deliverables by title")

    # Step 3: For portal deliverables, find task by deliverable_id and set task_id
    # Also update task.portal_deliverable_ids
    projects_cursor = projects_col.find(
        {"portal_deliverables": {"$exists": True, "$ne": []}},
        {"portal_deliverables": 1, "events": 1}
    )
    linked_portal = 0
    async for project in projects_cursor:
        project_id = str(project["_id"])
        portal_deliverables = project.get("portal_deliverables", [])

        # Group portal deliverables by deliverable_id
        for pd in portal_deliverables:
            if pd.get("task_id"):
                continue  # Already linked

            deliv_id = pd.get("deliverable_id")
            event_id = pd.get("event_id")
            if not deliv_id:
                continue

            # Find task by deliverable_id
            task = await tasks_col.find_one({
                "project_id": project_id,
                "category": "deliverable",
                "deliverable_id": deliv_id,
            })
            if not task:
                # Fallback: match by event_id and title
                task = await tasks_col.find_one({
                    "project_id": project_id,
                    "category": "deliverable",
                    "event_id": event_id,
                })

            if task:
                # Set task_id on portal deliverable
                await projects_col.update_one(
                    {"_id": project["_id"], "portal_deliverables.id": pd["id"]},
                    {"$set": {"portal_deliverables.$.task_id": task["id"]}}
                )
                # Add portal deliverable id to task.portal_deliverable_ids
                await tasks_col.update_one(
                    {"_id": task["_id"]},
                    {"$addToSet": {"portal_deliverable_ids": pd["id"]}}
                )
                linked_portal += 1
    print(f"Step 3: Linked {linked_portal} portal deliverables to tasks")

    # Step 4: Ensure portal_deliverable_ids field exists on all deliverable tasks
    await tasks_col.update_many(
        {"category": "deliverable", "portal_deliverable_ids": {"$exists": False}},
        {"$set": {"portal_deliverable_ids": []}}
    )

    # Step 5: Ensure deliverable_id field exists on all deliverable tasks
    await tasks_col.update_many(
        {"category": "deliverable", "deliverable_id": {"$exists": False}},
        {"$set": {"deliverable_id": None}}
    )

    # Step 6: Remove status from event deliverables in all projects
    projects_cursor = projects_col.find(
        {"events.deliverables.status": {"$exists": True}},
    )
    cleaned_projects = 0
    async for project in projects_cursor:
        updated = False
        for event in project.get("events", []):
            for deliverable in event.get("deliverables", []):
                if "status" in deliverable:
                    del deliverable["status"]
                    updated = True
        if updated:
            await projects_col.update_one(
                {"_id": project["_id"]},
                {"$set": {"events": project["events"]}}
            )
            cleaned_projects += 1
    print(f"Step 6: Removed status from event deliverables in {cleaned_projects} projects")

    print("\nMigration complete!")


if __name__ == "__main__":
    asyncio.run(migrate())
