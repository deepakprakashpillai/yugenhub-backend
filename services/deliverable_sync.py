"""
Deliverable Sync Service — Central coordination between Event Deliverables,
Tasks (category=deliverable), and Portal Deliverables.

All routes call into this module instead of inlining sync logic.
"""

from datetime import datetime, timezone
from models.project import PortalDeliverableModel
from logging_config import get_logger
from bson import ObjectId

logger = get_logger("deliverable_sync")

# --- Status mapping from task status to portal deliverable status ---
TASK_TO_PORTAL_STATUS = {
    "todo": "Pending",
    "in_progress": "Pending",
    "review": "Uploaded",
    "done": "Approved",
    "blocked": "Changes Requested",
}


async def on_deliverable_task_created(db, task: dict, project_id: str) -> None:
    """Auto-create portal deliverables based on task quantity.
    Sets task.portal_deliverable_ids and portal_deliverable.task_id.
    """
    quantity = max(task.get("quantity", 1) or 1, 1)
    task_id = task["id"]
    event_id = task.get("event_id")
    deliverable_id = task.get("deliverable_id")

    # Use explicit name if set, otherwise extract base type from title
    task_name = task.get("name")
    if task_name:
        title_base = task_name
    else:
        title_base = task.get("title", "Deliverable")
        if " (" in title_base:
            title_base = title_base.rsplit(" (", 1)[0]

    task_description = task.get("description", "")
    new_portal_deliverables = []
    portal_ids = []

    for i in range(1, quantity + 1):
        title = title_base if quantity == 1 else f"{title_base} {i}"
        pd = PortalDeliverableModel(
            title=title,
            description=task_description or "",
            event_id=event_id,
            deliverable_id=deliverable_id,
            task_id=task_id,
        )
        new_portal_deliverables.append(pd.model_dump())
        portal_ids.append(pd.id)

    if new_portal_deliverables:
        await db.projects.update_one(
            {"_id": ObjectId(project_id)},
            {
                "$push": {"portal_deliverables": {"$each": new_portal_deliverables}},
                "$set": {"updated_on": datetime.now(timezone.utc)},
            }
        )

    # Update task with portal_deliverable_ids
    if portal_ids:
        await db.tasks.update_one(
            {"id": task_id},
            {"$set": {"portal_deliverable_ids": portal_ids}}
        )

    logger.info(
        "Created portal deliverables for task",
        extra={"data": {"task_id": task_id, "count": len(portal_ids), "project_id": project_id}}
    )


async def on_task_status_changed(db, task: dict, old_status: str, new_status: str) -> None:
    """Propagate task status to linked portal deliverables."""
    portal_ids = task.get("portal_deliverable_ids", [])
    if not portal_ids:
        return

    project_id = task.get("project_id")
    if not project_id:
        return

    new_portal_status = TASK_TO_PORTAL_STATUS.get(new_status)
    if not new_portal_status:
        return

    # For "review" status, only transition to "Uploaded" if the portal deliverable has files
    # For other statuses, just set directly
    project = await db.projects.find_one(
        {"_id": ObjectId(project_id)},
        {"portal_deliverables": 1}
    )
    if not project:
        return

    now = datetime.now(timezone.utc)
    for pd in project.get("portal_deliverables", []):
        if pd.get("id") not in portal_ids:
            continue
        # Skip already-approved portal deliverables (client explicitly approved)
        if pd.get("status") == "Approved" and new_status != "done":
            continue
        # For review status, only set Uploaded if deliverable has files
        if new_status == "review" and not pd.get("files"):
            continue

        await db.projects.update_one(
            {"_id": ObjectId(project_id), "portal_deliverables.id": pd["id"]},
            {"$set": {
                "portal_deliverables.$.status": new_portal_status,
                "portal_deliverables.$.updated_on": now,
                "updated_on": now,
            }}
        )

    logger.info(
        "Propagated task status to portal deliverables",
        extra={"data": {"task_id": task["id"], "new_status": new_status, "portal_count": len(portal_ids)}}
    )


async def on_task_quantity_changed(db, task: dict, old_qty: int, new_qty: int, project_id: str) -> None:
    """Add/remove portal deliverables to match quantity."""
    old_qty = max(old_qty or 1, 1)
    new_qty = max(new_qty or 1, 1)
    if old_qty == new_qty:
        return

    task_id = task["id"]
    portal_ids = task.get("portal_deliverable_ids", [])

    # Extract base title
    title_base = task.get("title", "Deliverable")
    if " (" in title_base:
        title_base = title_base.rsplit(" (", 1)[0]

    if new_qty > old_qty:
        # Add more portal deliverables
        task_description = task.get("description", "")
        task_name = task.get("name")
        new_items = []
        new_ids = list(portal_ids)
        for i in range(old_qty + 1, new_qty + 1):
            base = task_name if task_name else title_base
            title = base if new_qty == 1 else f"{base} {i}"
            pd = PortalDeliverableModel(
                title=title,
                description=task_description or "",
                event_id=task.get("event_id"),
                deliverable_id=task.get("deliverable_id"),
                task_id=task_id,
            )
            new_items.append(pd.model_dump())
            new_ids.append(pd.id)

        if new_items:
            await db.projects.update_one(
                {"_id": ObjectId(project_id)},
                {
                    "$push": {"portal_deliverables": {"$each": new_items}},
                    "$set": {"updated_on": datetime.now(timezone.utc)},
                }
            )
            await db.tasks.update_one(
                {"id": task_id},
                {"$set": {"portal_deliverable_ids": new_ids}}
            )

    elif new_qty < old_qty:
        # Remove empty portal deliverables from the end
        project = await db.projects.find_one(
            {"_id": ObjectId(project_id)},
            {"portal_deliverables": 1}
        )
        if not project:
            return

        # Find portal deliverables linked to this task
        linked = [pd for pd in project.get("portal_deliverables", []) if pd.get("id") in portal_ids]
        excess = len(linked) - new_qty
        ids_to_remove = set()

        for pd in reversed(linked):
            if excess <= 0:
                break
            has_content = len(pd.get("files", [])) > 0 or len(pd.get("feedback", [])) > 0
            if not has_content:
                ids_to_remove.add(pd["id"])
                excess -= 1

        if ids_to_remove:
            remaining_portal_ids = [pid for pid in portal_ids if pid not in ids_to_remove]
            updated = [
                pd for pd in project.get("portal_deliverables", [])
                if pd.get("id") not in ids_to_remove
            ]
            await db.projects.update_one(
                {"_id": ObjectId(project_id)},
                {"$set": {
                    "portal_deliverables": updated,
                    "updated_on": datetime.now(timezone.utc),
                }}
            )
            await db.tasks.update_one(
                {"id": task_id},
                {"$set": {"portal_deliverable_ids": remaining_portal_ids}}
            )

    logger.info(
        "Adjusted portal deliverables for quantity change",
        extra={"data": {"task_id": task_id, "old_qty": old_qty, "new_qty": new_qty}}
    )


async def on_task_deleted(db, task: dict) -> None:
    """Remove empty linked portal deliverables, keep non-empty ones (unlink them)."""
    portal_ids = task.get("portal_deliverable_ids", [])
    if not portal_ids:
        return

    project_id = task.get("project_id")
    if not project_id:
        return

    project = await db.projects.find_one(
        {"_id": ObjectId(project_id)},
        {"portal_deliverables": 1}
    )
    if not project:
        return

    ids_to_remove = set()
    ids_to_unlink = set()

    for pd in project.get("portal_deliverables", []):
        if pd.get("id") not in portal_ids:
            continue
        has_content = len(pd.get("files", [])) > 0 or len(pd.get("feedback", [])) > 0
        if has_content:
            ids_to_unlink.add(pd["id"])
        else:
            ids_to_remove.add(pd["id"])

    now = datetime.now(timezone.utc)

    if ids_to_remove:
        updated = [pd for pd in project.get("portal_deliverables", []) if pd.get("id") not in ids_to_remove]
        await db.projects.update_one(
            {"_id": ObjectId(project_id)},
            {"$set": {"portal_deliverables": updated, "updated_on": now}}
        )

    # Unlink non-empty portal deliverables (clear task_id)
    for pid in ids_to_unlink:
        await db.projects.update_one(
            {"_id": ObjectId(project_id), "portal_deliverables.id": pid},
            {"$set": {
                "portal_deliverables.$.task_id": None,
                "portal_deliverables.$.updated_on": now,
            }}
        )

    logger.info(
        "Cleaned up portal deliverables after task deletion",
        extra={"data": {"task_id": task["id"], "removed": len(ids_to_remove), "unlinked": len(ids_to_unlink)}}
    )


async def on_portal_file_added(db, project_id: str, portal_deliverable_id: str) -> None:
    """Pending -> Uploaded transition when first file is added."""
    project = await db.projects.find_one(
        {"_id": ObjectId(project_id), "portal_deliverables.id": portal_deliverable_id},
        {"portal_deliverables.$": 1}
    )
    if not project or not project.get("portal_deliverables"):
        return

    pd = project["portal_deliverables"][0]
    if pd.get("status") == "Pending":
        await db.projects.update_one(
            {"_id": ObjectId(project_id), "portal_deliverables.id": portal_deliverable_id},
            {"$set": {
                "portal_deliverables.$.status": "Uploaded",
                "portal_deliverables.$.updated_on": datetime.now(timezone.utc),
                "updated_on": datetime.now(timezone.utc),
            }}
        )


async def on_portal_file_removed(db, project_id: str, portal_deliverable_id: str, remaining_files: int) -> None:
    """Uploaded -> Pending if last file removed."""
    if remaining_files == 0:
        project = await db.projects.find_one(
            {"_id": ObjectId(project_id), "portal_deliverables.id": portal_deliverable_id},
            {"portal_deliverables.$": 1}
        )
        if not project or not project.get("portal_deliverables"):
            return

        pd = project["portal_deliverables"][0]
        if pd.get("status") == "Uploaded":
            await db.projects.update_one(
                {"_id": ObjectId(project_id), "portal_deliverables.id": portal_deliverable_id},
                {"$set": {
                    "portal_deliverables.$.status": "Pending",
                    "portal_deliverables.$.updated_on": datetime.now(timezone.utc),
                    "updated_on": datetime.now(timezone.utc),
                }}
            )


async def on_client_approved(db, project_id: str, portal_deliverable_id: str) -> None:
    """Portal -> Approved. If ALL portal deliverables for the task are Approved -> task -> done."""
    project = await db.projects.find_one(
        {"_id": ObjectId(project_id)},
        {"portal_deliverables": 1}
    )
    if not project:
        return

    # Find the approved portal deliverable to get its task_id
    approved_pd = next(
        (pd for pd in project.get("portal_deliverables", []) if pd.get("id") == portal_deliverable_id),
        None
    )
    if not approved_pd or not approved_pd.get("task_id"):
        return

    task_id = approved_pd["task_id"]

    # Check if ALL portal deliverables for this task are approved
    task = await db.tasks.find_one({"id": task_id})
    if not task:
        return

    all_portal_ids = set(task.get("portal_deliverable_ids", []))
    if not all_portal_ids:
        return

    all_approved = all(
        pd.get("status") == "Approved"
        for pd in project.get("portal_deliverables", [])
        if pd.get("id") in all_portal_ids
    )

    if all_approved and task.get("status") != "done":
        old_status = task.get("status")
        await db.tasks.update_one(
            {"id": task_id},
            {"$set": {"status": "done", "updated_at": datetime.now(timezone.utc)}}
        )
        # Log history
        from models.task import TaskHistoryModel
        history = TaskHistoryModel(
            task_id=task_id,
            changed_by="system",
            field="status",
            old_value=old_status,
            new_value="done",
            comment="All portal deliverables approved by client",
            studio_id=task.get("studio_id", ""),
        )
        await db.task_history.insert_one(history.model_dump())

        logger.info(
            "All portal deliverables approved — task marked done",
            extra={"data": {"task_id": task_id, "project_id": project_id}}
        )


async def on_client_feedback(db, project_id: str, portal_deliverable_id: str, feedback_entry: dict = None) -> None:
    """Portal -> Changes Requested. Task -> blocked with auto-comment.
    Also syncs feedback message as a task comment when feedback_entry is provided.
    """
    project = await db.projects.find_one(
        {"_id": ObjectId(project_id)},
        {"portal_deliverables": 1}
    )
    if not project:
        return

    feedback_pd = next(
        (pd for pd in project.get("portal_deliverables", []) if pd.get("id") == portal_deliverable_id),
        None
    )
    if not feedback_pd or not feedback_pd.get("task_id"):
        return

    task_id = feedback_pd["task_id"]
    task = await db.tasks.find_one({"id": task_id})
    if not task:
        return

    from models.task import TaskHistoryModel

    # Sync feedback message as a task comment
    if feedback_entry:
        feedback_msg = feedback_entry.get("message", "")
        file_id = feedback_entry.get("file_id")
        file_name = None
        if file_id:
            file_name = next(
                (f.get("file_name") for f in feedback_pd.get("files", []) if f.get("id") == file_id),
                None
            )
        deliverable_title = feedback_pd.get("title", "deliverable")
        comment_parts = [f'[Portal Feedback on "{deliverable_title}"']
        if file_name:
            comment_parts.append(f" - File: {file_name}")
        comment_parts.append(f"]: {feedback_msg}")

        comment_history = TaskHistoryModel(
            task_id=task_id,
            changed_by="portal_client",
            field="comment",
            comment="".join(comment_parts),
            studio_id=task.get("studio_id", ""),
        )
        await db.task_history.insert_one(comment_history.model_dump())

    # Only block if not already blocked
    if task.get("status") == "blocked":
        return

    old_status = task.get("status")
    await db.tasks.update_one(
        {"id": task_id},
        {"$set": {"status": "blocked", "updated_at": datetime.now(timezone.utc)}}
    )

    # Log status change history
    history = TaskHistoryModel(
        task_id=task_id,
        changed_by="system",
        field="status",
        old_value=old_status,
        new_value="blocked",
        comment=f"Client requested changes on '{feedback_pd.get('title', 'deliverable')}'",
        studio_id=task.get("studio_id", ""),
    )
    await db.task_history.insert_one(history.model_dump())

    logger.info(
        "Client feedback — task blocked",
        extra={"data": {"task_id": task_id, "portal_deliverable_id": portal_deliverable_id}}
    )


async def reconcile_project(db, project_id: str) -> dict:
    """Full ID-based reconciliation. Creates missing portal deliverables,
    removes empty excess ones, and ensures FK links are correct."""
    project = await db.projects.find_one({"_id": ObjectId(project_id)})
    if not project:
        return {"created": 0, "removed": 0}

    portal_deliverables = project.get("portal_deliverables", [])

    # Fetch all deliverable tasks for this project
    task_list = await db.tasks.find({
        "project_id": project_id,
        "category": "deliverable",
    }).to_list(length=None)

    # Build lookup: task_id -> task
    tasks_by_id = {t["id"]: t for t in task_list}

    # Build lookup: task_id -> existing portal deliverables
    pd_by_task = {}
    for pd in portal_deliverables:
        tid = pd.get("task_id")
        if tid:
            pd_by_task.setdefault(tid, []).append(pd)

    created = 0
    removed = 0
    ids_to_remove = set()
    new_items = []

    for task in task_list:
        task_id = task["id"]
        qty = max(task.get("quantity", 1) or 1, 1)
        existing = pd_by_task.get(task_id, [])
        current_count = len(existing)

        task_name = task.get("name")
        if task_name:
            title_base = task_name
        else:
            title_base = task.get("title", "Deliverable")
            if " (" in title_base:
                title_base = title_base.rsplit(" (", 1)[0]

        if current_count < qty:
            new_ids = list(task.get("portal_deliverable_ids", []))
            for i in range(current_count + 1, qty + 1):
                title = title_base if qty == 1 else f"{title_base} {i}"
                pd = PortalDeliverableModel(
                    title=title,
                    event_id=task.get("event_id"),
                    deliverable_id=task.get("deliverable_id"),
                    task_id=task_id,
                )
                new_items.append(pd.model_dump())
                new_ids.append(pd.id)
                created += 1

            # Update task portal_deliverable_ids
            await db.tasks.update_one(
                {"id": task_id},
                {"$set": {"portal_deliverable_ids": new_ids}}
            )

        elif current_count > qty:
            excess = current_count - qty
            for pd in reversed(existing):
                if excess <= 0:
                    break
                has_content = len(pd.get("files", [])) > 0 or len(pd.get("feedback", [])) > 0
                if not has_content:
                    ids_to_remove.add(pd["id"])
                    excess -= 1
                    removed += 1

            if ids_to_remove:
                remaining_ids = [pid for pid in task.get("portal_deliverable_ids", []) if pid not in ids_to_remove]
                await db.tasks.update_one(
                    {"id": task_id},
                    {"$set": {"portal_deliverable_ids": remaining_ids}}
                )

    # Apply changes to project
    updated_deliverables = portal_deliverables
    if ids_to_remove:
        updated_deliverables = [pd for pd in updated_deliverables if pd["id"] not in ids_to_remove]
    if new_items:
        updated_deliverables = updated_deliverables + new_items

    if ids_to_remove or new_items:
        await db.projects.update_one(
            {"_id": ObjectId(project_id)},
            {"$set": {
                "portal_deliverables": updated_deliverables,
                "updated_on": datetime.now(timezone.utc),
            }}
        )

    return {"created": created, "removed": removed}
