from datetime import datetime, timezone
from typing import Any, Dict, Optional
from models.task import TaskHistoryModel
from middleware.db_guard import ScopedDatabase


async def log_history(
    db: ScopedDatabase,
    task_id: str,
    user_id: str,
    changes: Dict[str, Any],
    comment: Optional[str] = None,
) -> None:
    """Insert one TaskHistoryModel entry per changed field."""
    if not changes:
        return

    timestamp = datetime.now(timezone.utc)
    studio_id = db.agency_id
    blocked_comment = (
        comment
        if "status" in changes and changes.get("status") == "blocked"
        else None
    )
    general_comment = comment if not blocked_comment else None

    entries = []
    for field, (old_val, new_val) in changes.items():
        entry = TaskHistoryModel(
            task_id=task_id,
            changed_by=user_id,
            field=field,
            old_value=str(old_val) if old_val is not None else None,
            new_value=str(new_val) if new_val is not None else None,
            comment=(
                blocked_comment
                if field == "status" and new_val == "blocked"
                else general_comment
            ),
            studio_id=studio_id,
            timestamp=timestamp,
        )
        entries.append(entry.model_dump())

    if entries:
        await db.task_history.insert_many(entries)
