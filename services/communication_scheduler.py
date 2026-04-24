"""APScheduler jobs for time-based WhatsApp alerts to associates.

Jobs:
  - event_reminder_scan  — hourly: timed events (start_date has a specific time) within the configured window
  - daily_scan           — daily 09:00 UTC:
                             • date-only events happening tomorrow (start_date stored as midnight UTC)
                             • deliverable tasks due soon or overdue
"""

from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bson import ObjectId

from database import db as raw_db
from logging_config import get_logger
from middleware.db_guard import ScopedDatabase
from models.communication import EVENT_REMINDER, DELIVERABLE_REMINDER, DELIVERABLE_OVERDUE
from services.communication_generator import enqueue_message_associate


logger = get_logger("communication_scheduler")

scheduler = AsyncIOScheduler(timezone="UTC")

_DEFAULT_EVENT_HOURS_BEFORE = 24
_DEFAULT_DELIVERABLE_DAYS_BEFORE = 3


def _is_date_only(dt: datetime) -> bool:
    """True when the datetime has no time component (stored as midnight UTC — date-only events)."""
    return dt.hour == 0 and dt.minute == 0 and dt.second == 0


def _scoped(agency_id: str) -> ScopedDatabase:
    return ScopedDatabase(raw_db, agency_id)


async def _load_all_configs() -> dict[str, dict]:
    configs = {}
    async for cfg in raw_db.scheduler_configs.find({}):
        configs[cfg["agency_id"]] = cfg
    return configs


async def _event_reminder_scan() -> None:
    logger.info("Running event reminder scan")
    now = datetime.now(timezone.utc)
    configs = await _load_all_configs()

    max_hours = max(
        (c.get("event_reminder_hours_before", _DEFAULT_EVENT_HOURS_BEFORE) for c in configs.values()),
        default=_DEFAULT_EVENT_HOURS_BEFORE,
    )
    window_end = now + timedelta(hours=max_hours)

    cursor = raw_db.projects.find({
        "events.start_date": {"$gte": now, "$lte": window_end},
        "events.assignments.0": {"$exists": True},
    })
    async for project in cursor:
        agency_id = project.get("agency_id")
        if not agency_id:
            continue

        cfg = configs.get(agency_id, {})
        if not cfg.get("event_scan_enabled", True):
            continue

        hours_before = cfg.get("event_reminder_hours_before", _DEFAULT_EVENT_HOURS_BEFORE)
        agency_window_end = now + timedelta(hours=hours_before)

        org_config = await raw_db.agency_configs.find_one({"agency_id": agency_id})
        agency_name = (org_config or {}).get("org_name", "")
        project_code = project.get("code", "")
        db = _scoped(agency_id)

        for event in project.get("events", []):
            start_date = event.get("start_date")
            if not start_date or not (now <= start_date <= agency_window_end):
                continue
            # Date-only events are handled by the daily 09:00 UTC scan
            if _is_date_only(start_date):
                continue

            for assignment in event.get("assignments", []):
                associate_id = assignment.get("associate_id")
                if not associate_id:
                    continue

                await enqueue_message_associate(
                    db=db,
                    agency_id=agency_id,
                    alert_type=EVENT_REMINDER,
                    recipient_associate_id=associate_id,
                    source={"kind": "event", "id": event.get("id", ""), "project_id": str(project.get("_id", ""))},
                    render_ctx={
                        "project_code": project_code,
                        "event_type": event.get("type", "Event"),
                        "event_date": start_date,
                        "venue_name": event.get("venue_name", ""),
                        "agency_name": agency_name,
                    },
                )


async def _daily_scan() -> None:
    logger.info("Running daily scan (date-only event reminders + deliverables)")
    now = datetime.now(timezone.utc)
    configs = await _load_all_configs()

    # ── Date-only event reminders (fire 1 day before at 09:00 UTC) ──────────
    tomorrow_start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_end = tomorrow_start + timedelta(days=1)

    cursor = raw_db.projects.find({
        "events.start_date": {"$gte": tomorrow_start, "$lt": tomorrow_end},
        "events.assignments.0": {"$exists": True},
    })
    async for project in cursor:
        agency_id = project.get("agency_id")
        if not agency_id:
            continue
        cfg = configs.get(agency_id, {})
        if not cfg.get("event_scan_enabled", True):
            continue

        org_config = await raw_db.agency_configs.find_one({"agency_id": agency_id})
        agency_name = (org_config or {}).get("org_name", "")
        project_code = project.get("code", "")
        db = _scoped(agency_id)

        for event in project.get("events", []):
            start_date = event.get("start_date")
            if not start_date or not (tomorrow_start <= start_date < tomorrow_end):
                continue
            if not _is_date_only(start_date):
                continue  # timed events are handled by the hourly scan

            for assignment in event.get("assignments", []):
                associate_id = assignment.get("associate_id")
                if not associate_id:
                    continue
                await enqueue_message_associate(
                    db=db,
                    agency_id=agency_id,
                    alert_type=EVENT_REMINDER,
                    recipient_associate_id=associate_id,
                    source={"kind": "event", "id": event.get("id", ""), "project_id": str(project.get("_id", ""))},
                    render_ctx={
                        "project_code": project_code,
                        "event_type": event.get("type", "Event"),
                        "event_date": start_date,
                        "venue_name": event.get("venue_name", ""),
                        "agency_name": agency_name,
                    },
                )

    max_days = max(
        (c.get("deliverable_reminder_days_before", _DEFAULT_DELIVERABLE_DAYS_BEFORE) for c in configs.values()),
        default=_DEFAULT_DELIVERABLE_DAYS_BEFORE,
    )
    window_end = now + timedelta(days=max_days)

    cursor = raw_db.tasks.find({
        "assigned_associate_id": {"$exists": True, "$ne": None},
        "category": "deliverable",
        "status": {"$nin": ["done", "completed"]},
        "due_date": {"$ne": None},
    })
    async for task in cursor:
        due_date = task.get("due_date")
        if not due_date:
            continue

        agency_id = task.get("studio_id") or task.get("agency_id")
        if not agency_id:
            continue

        cfg = configs.get(agency_id, {})
        if not cfg.get("deliverable_scan_enabled", True):
            continue

        days_before = cfg.get("deliverable_reminder_days_before", _DEFAULT_DELIVERABLE_DAYS_BEFORE)
        agency_window_end = now + timedelta(days=days_before)

        associate_id = task.get("assigned_associate_id")

        project_code = ""
        project_id = task.get("project_id")
        if project_id:
            try:
                project = await raw_db.projects.find_one({"_id": ObjectId(project_id)})
            except Exception:
                project = await raw_db.projects.find_one({"id": project_id})
            if project:
                project_code = project.get("code", "")

        org_config = await raw_db.agency_configs.find_one({"agency_id": agency_id})
        agency_name = (org_config or {}).get("org_name", "")
        db = _scoped(agency_id)

        source = {"kind": "task", "id": str(task.get("id", task.get("_id", "")))}
        ctx = {
            "project_code": project_code,
            "deliverable_type": task.get("title", ""),
            "due_date": due_date,
            "agency_name": agency_name,
        }

        if now <= due_date <= agency_window_end:
            await enqueue_message_associate(
                db=db,
                agency_id=agency_id,
                alert_type=DELIVERABLE_REMINDER,
                recipient_associate_id=associate_id,
                source=source,
                render_ctx=ctx,
            )
        elif due_date < now:
            await enqueue_message_associate(
                db=db,
                agency_id=agency_id,
                alert_type=DELIVERABLE_OVERDUE,
                recipient_associate_id=associate_id,
                source=source,
                render_ctx=ctx,
            )


async def run_event_reminder_for_agency(db: ScopedDatabase, agency_id: str, hours_before: int) -> int:
    """Trigger event reminder scan for a single agency. Returns count queued."""
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=hours_before)
    count = 0

    org_config = await raw_db.agency_configs.find_one({"agency_id": agency_id})
    agency_name = (org_config or {}).get("org_name", "")

    cursor = raw_db.projects.find({
        "agency_id": agency_id,
        "events.start_date": {"$gte": now, "$lte": window_end},
        "events.assignments.0": {"$exists": True},
    })
    async for project in cursor:
        project_code = project.get("code", "")
        for event in project.get("events", []):
            start_date = event.get("start_date")
            if not start_date or not (now <= start_date <= window_end):
                continue
            for assignment in event.get("assignments", []):
                associate_id = assignment.get("associate_id")
                if not associate_id:
                    continue
                msg = await enqueue_message_associate(
                    db=db,
                    agency_id=agency_id,
                    alert_type=EVENT_REMINDER,
                    recipient_associate_id=associate_id,
                    source={"kind": "event", "id": event.get("id", ""), "project_id": str(project.get("_id", ""))},
                    render_ctx={
                        "project_code": project_code,
                        "event_type": event.get("type", "Event"),
                        "event_date": start_date,
                        "venue_name": event.get("venue_name", ""),
                        "agency_name": agency_name,
                    },
                    skip_dedup=True,
                )
                if msg:
                    count += 1
    return count


async def run_deliverable_scan_for_agency(db: ScopedDatabase, agency_id: str, days_before: int) -> int:
    """Trigger deliverable scan for a single agency. Returns count queued."""
    now = datetime.now(timezone.utc)
    due_soon_cutoff = now + timedelta(days=days_before)
    count = 0

    org_config = await raw_db.agency_configs.find_one({"agency_id": agency_id})
    agency_name = (org_config or {}).get("org_name", "")

    cursor = raw_db.tasks.find({
        "$or": [{"studio_id": agency_id}, {"agency_id": agency_id}],
        "assigned_associate_id": {"$exists": True, "$ne": None},
        "category": "deliverable",
        "status": {"$nin": ["done", "completed"]},
        "due_date": {"$ne": None},
    })
    async for task in cursor:
        due_date = task.get("due_date")
        if not due_date:
            continue

        associate_id = task.get("assigned_associate_id")
        project_code = ""
        project_id = task.get("project_id")
        if project_id:
            try:
                project = await raw_db.projects.find_one({"_id": ObjectId(project_id)})
            except Exception:
                project = await raw_db.projects.find_one({"id": project_id})
            if project:
                project_code = project.get("code", "")

        source = {"kind": "task", "id": str(task.get("id", task.get("_id", "")))}
        ctx = {
            "project_code": project_code,
            "deliverable_type": task.get("title", ""),
            "due_date": due_date,
            "agency_name": agency_name,
        }

        if now <= due_date <= due_soon_cutoff:
            msg = await enqueue_message_associate(
                db=db, agency_id=agency_id, alert_type=DELIVERABLE_REMINDER,
                recipient_associate_id=associate_id, source=source, render_ctx=ctx, skip_dedup=True,
            )
        elif due_date < now:
            msg = await enqueue_message_associate(
                db=db, agency_id=agency_id, alert_type=DELIVERABLE_OVERDUE,
                recipient_associate_id=associate_id, source=source, render_ctx=ctx, skip_dedup=True,
            )
        else:
            msg = None

        if msg:
            count += 1
    return count


def start_scheduler() -> None:
    scheduler.add_job(_event_reminder_scan, "interval", hours=1, id="event_reminder_scan", replace_existing=True)
    scheduler.add_job(_daily_scan, "cron", hour=9, minute=0, id="daily_scan", replace_existing=True)
    scheduler.start()
    logger.info("Communication scheduler started")


def stop_scheduler() -> None:
    scheduler.shutdown(wait=False)
    logger.info("Communication scheduler stopped")
