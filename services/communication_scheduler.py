"""APScheduler jobs for time-based WhatsApp alerts.

IMPORTANT: This runs in a single-replica setup (same as expire_albums_loop).
If the API ever scales horizontally add a Mongo leader-lock before enabling the jobs
on more than one replica.

Jobs:
  - task_deadline_scan  — hourly: tasks due within 24h, not done
  - invoice_scan        — daily 09:00 UTC: due-soon (3 days) and overdue invoices
"""

from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bson import ObjectId

from database import db as raw_db
from logging_config import get_logger
from middleware.db_guard import ScopedDatabase
from models.communication import TASK_DEADLINE, INVOICE_DUE_SOON, INVOICE_OVERDUE
from services.communication_generator import enqueue_message


async def _find_project(project_id: str) -> dict | None:
    """Fetch a project by its _id (stored as hex string on tasks)."""
    try:
        return await raw_db.projects.find_one({"_id": ObjectId(project_id)})
    except Exception:
        return await raw_db.projects.find_one({"id": project_id})

logger = get_logger("communication_scheduler")

scheduler = AsyncIOScheduler(timezone="UTC")


def _scoped(agency_id: str) -> ScopedDatabase:
    """Wrap raw db in a ScopedDatabase scoped to the given agency."""
    return ScopedDatabase(raw_db, agency_id)


async def _task_deadline_scan() -> None:
    logger.info("Running task deadline scan")
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=24)

    cursor = raw_db.tasks.find({
        "due_date": {"$gte": now, "$lte": window_end},
        "status": {"$nin": ["done", "completed"]},
    })
    async for task in cursor:
        project_id = task.get("project_id")
        client_id = None
        agency_id = task.get("studio_id") or task.get("agency_id")
        project_code = ""
        if project_id:
            project = await _find_project(project_id)
            if project:
                client_id = project.get("client_id")
                project_code = project.get("code", "")
                agency_id = agency_id or project.get("agency_id")

        if not client_id or not agency_id:
            continue

        if ObjectId.is_valid(client_id):
            client = await raw_db.clients.find_one({"_id": ObjectId(client_id)})
        else:
            client = await raw_db.clients.find_one({"id": client_id})
        if not client:
            continue

        db = _scoped(agency_id)
        await enqueue_message(
            db=db,
            agency_id=agency_id,
            alert_type=TASK_DEADLINE,
            recipient_client_id=client_id,
            source={"kind": "task", "id": str(task.get("id", task.get("_id", "")))},
            render_ctx={
                "client_name": client.get("name", "there"),
                "task_title": task.get("title", ""),
                "project_code": project_code,
                "due_date": task.get("due_date"),
                "agency_name": "",
            },
        )


async def _invoice_scan() -> None:
    logger.info("Running invoice scan")
    now = datetime.now(timezone.utc)
    due_soon_cutoff = now + timedelta(days=3)

    cursor = raw_db.finance_invoices.find({
        "status": {"$nin": ["paid", "cancelled"]},
    })
    async for invoice in cursor:
        due_date = invoice.get("due_date")
        if not due_date:
            continue
        if isinstance(due_date, str):
            try:
                due_date = datetime.fromisoformat(due_date.replace("Z", "+00:00"))
            except ValueError:
                continue

        client_id = invoice.get("client_id")
        agency_id = invoice.get("agency_id")
        if not client_id or not agency_id:
            continue

        if ObjectId.is_valid(client_id):
            client = await raw_db.clients.find_one({"_id": ObjectId(client_id)})
        else:
            client = await raw_db.clients.find_one({"id": client_id})
        if not client:
            continue

        invoice_no = invoice.get("invoice_no", "")
        amount = invoice.get("total_amount", 0)

        db = _scoped(agency_id)

        currency = invoice.get("currency", "INR")
        if now <= due_date <= due_soon_cutoff:
            await enqueue_message(
                db=db,
                agency_id=agency_id,
                alert_type=INVOICE_DUE_SOON,
                recipient_client_id=client_id,
                source={"kind": "invoice", "id": str(invoice.get("id", invoice.get("_id", "")))},
                render_ctx={
                    "client_name": client.get("name", "there"),
                    "invoice_no": invoice_no,
                    "amount": amount,
                    "currency": currency,
                    "due_date": due_date,
                    "agency_name": "",
                },
            )
        elif due_date < now:
            await enqueue_message(
                db=db,
                agency_id=agency_id,
                alert_type=INVOICE_OVERDUE,
                recipient_client_id=client_id,
                source={"kind": "invoice", "id": str(invoice.get("id", invoice.get("_id", "")))},
                render_ctx={
                    "client_name": client.get("name", "there"),
                    "invoice_no": invoice_no,
                    "amount": amount,
                    "currency": currency,
                    "due_date": due_date,
                    "agency_name": "",
                },
            )


def start_scheduler() -> None:
    scheduler.add_job(_task_deadline_scan, "interval", hours=1, id="task_deadline_scan", replace_existing=True)
    scheduler.add_job(_invoice_scan, "cron", hour=9, minute=0, id="invoice_scan", replace_existing=True)
    scheduler.start()
    logger.info("Communication scheduler started")


def stop_scheduler() -> None:
    scheduler.shutdown(wait=False)
    logger.info("Communication scheduler stopped")
