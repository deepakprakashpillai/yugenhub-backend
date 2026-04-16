"""
Tests for Media Feature — Step 6
Covers: migration job start/status endpoints, run_migration logic
        (deliverable files + album files, idempotency, error handling).
"""
import pytest
from unittest.mock import patch, MagicMock
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


# ─── POST /settings/migrate-to-media ─────────────────────────────────────────

async def test_start_migration_owner_only(async_client: AsyncClient, member_auth_headers):
    resp = await async_client.post("/api/settings/migrate-to-media", headers=member_auth_headers)
    assert resp.status_code == 403


async def test_start_migration_creates_job(async_client: AsyncClient, auth_headers, test_db_session):
    resp = await async_client.post("/api/settings/migrate-to-media", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "job_id" in data
    assert data["status"] in ("queued", "running")

    job = test_db_session.migration_jobs.find_one({"job_id": data["job_id"]})
    assert job is not None
    assert job["agency_id"] == "test_agency"


async def test_start_migration_no_duplicate_while_running(async_client: AsyncClient, auth_headers, test_db_session):
    """If a job is already running, returns the existing job_id."""
    import uuid
    existing_job_id = str(uuid.uuid4())
    test_db_session.migration_jobs.insert_one({
        "job_id": existing_job_id,
        "agency_id": "test_agency",
        "status": "running",
        "migrated": 0, "failed": 0, "errors": [],
    })

    resp = await async_client.post("/api/settings/migrate-to-media", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["job_id"] == existing_job_id
    assert resp.json()["status"] == "running"


# ─── GET /settings/migrate-to-media/status ───────────────────────────────────

async def test_get_status_no_job(async_client: AsyncClient, auth_headers, test_db_session):
    resp = await async_client.get("/api/settings/migrate-to-media/status", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_started"


async def test_get_status_returns_latest_job(async_client: AsyncClient, auth_headers, test_db_session):
    import uuid
    from datetime import datetime, timezone
    job_id = str(uuid.uuid4())
    test_db_session.migration_jobs.insert_one({
        "job_id": job_id, "agency_id": "test_agency",
        "status": "completed", "migrated": 5, "failed": 0,
        "errors": [], "started_at": datetime.now(timezone.utc),
        "completed_at": datetime.now(timezone.utc),
    })
    resp = await async_client.get("/api/settings/migrate-to-media/status", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == job_id
    assert data["status"] == "completed"
    assert data["migrated"] == 5


async def test_get_status_owner_only(async_client: AsyncClient, member_auth_headers):
    resp = await async_client.get("/api/settings/migrate-to-media/status", headers=member_auth_headers)
    assert resp.status_code == 403


# ─── copy_r2_object in utils/r2.py ───────────────────────────────────────────

def test_copy_r2_object_callable():
    from utils.r2 import copy_r2_object
    assert callable(copy_r2_object)


# ─── run_migration — deliverable files ───────────────────────────────────────

async def test_run_migration_migrates_deliverable_file(test_db_session):
    """run_migration creates a MediaItem and rekeys the DeliverableFile."""
    import uuid
    from bson import ObjectId
    from datetime import datetime, timezone

    agency_id = "mig_agency_1"
    project_oid = ObjectId()
    project_id = str(project_oid)
    file_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())

    test_db_session.projects.insert_one({
        "_id": project_oid,
        "agency_id": agency_id,
        "code": "MIG-001",
        "metadata": {},
        "portal_deliverables": [{
            "id": "deliv-mig-1",
            "title": "Ceremony",
            "status": "Uploaded",
            "files": [{
                "id": file_id,
                "file_name": "shot.jpg",
                "content_type": "image/jpeg",
                "r2_key": f"deliverables/{agency_id}/{project_id}/shot.jpg",
                "r2_url": "https://old/shot.jpg",
                "thumbnail_r2_key": None,
                "preview_r2_key": None,
                "watermark_r2_key": None,
            }],
        }],
    })
    test_db_session.migration_jobs.insert_one({
        "job_id": job_id, "agency_id": agency_id,
        "status": "queued", "migrated": 0, "failed": 0, "errors": [],
    })

    from services.media_migration import run_migration

    with patch("services.media_migration.copy_r2_object") as mock_copy, \
         patch("services.media_migration.delete_r2_object") as mock_del:
        await run_migration(agency_id, job_id)

    # MediaItem should be created
    item = test_db_session.media_items.find_one({"id": file_id})
    assert item is not None
    assert item["source"] == "deliverable"
    assert item["r2_key"] == f"media/{agency_id}/files/{file_id}.jpg"
    assert item["status"] == "active"

    # DeliverableFile should be updated
    project = test_db_session.projects.find_one({"_id": project_oid})
    file_entry = project["portal_deliverables"][0]["files"][0]
    assert file_entry["media_item_id"] == file_id
    assert file_entry["r2_key"] == f"media/{agency_id}/files/{file_id}.jpg"

    # Job should be completed
    job = test_db_session.migration_jobs.find_one({"job_id": job_id})
    assert job["status"] == "completed"
    assert job["migrated"] == 1
    assert job["failed"] == 0

    # R2 copy + delete should have been called
    mock_copy.assert_called_once()
    mock_del.assert_called_once()


async def test_run_migration_skips_already_migrated(test_db_session):
    """Files with media_item_id already set are skipped."""
    import uuid
    from bson import ObjectId
    from datetime import datetime, timezone

    agency_id = "mig_agency_2"
    project_oid = ObjectId()
    file_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())

    test_db_session.projects.insert_one({
        "_id": project_oid,
        "agency_id": agency_id,
        "code": "MIG-002",
        "metadata": {},
        "portal_deliverables": [{
            "id": "deliv-mig-2",
            "title": "Reception",
            "status": "Uploaded",
            "files": [{
                "id": file_id,
                "file_name": "hero.jpg",
                "content_type": "image/jpeg",
                "r2_key": f"media/{agency_id}/files/{file_id}.jpg",
                "r2_url": "https://new/hero.jpg",
                "media_item_id": file_id,  # already migrated
            }],
        }],
    })
    test_db_session.migration_jobs.insert_one({
        "job_id": job_id, "agency_id": agency_id,
        "status": "queued", "migrated": 0, "failed": 0, "errors": [],
    })

    from services.media_migration import run_migration

    with patch("services.media_migration.copy_r2_object") as mock_copy:
        await run_migration(agency_id, job_id)

    mock_copy.assert_not_called()
    job = test_db_session.migration_jobs.find_one({"job_id": job_id})
    assert job["migrated"] == 0


async def test_run_migration_migrates_album_file(test_db_session):
    """run_migration handles album files correctly."""
    import uuid
    from datetime import datetime, timezone

    agency_id = "mig_agency_3"
    album_id = str(uuid.uuid4())
    tab_id = str(uuid.uuid4())
    file_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())

    test_db_session.albums.insert_one({
        "id": album_id, "agency_id": agency_id,
        "title": "Beach Session",
        "tabs": [{
            "id": tab_id, "title": "Candids",
            "files": [{
                "id": file_id, "file_name": "candid.jpg",
                "content_type": "image/jpeg",
                "r2_key": f"albums/{agency_id}/{album_id}/candid.jpg",
                "thumbnail_r2_key": None, "preview_r2_key": None,
            }],
        }],
    })
    test_db_session.migration_jobs.insert_one({
        "job_id": job_id, "agency_id": agency_id,
        "status": "queued", "migrated": 0, "failed": 0, "errors": [],
    })

    from services.media_migration import run_migration

    with patch("services.media_migration.copy_r2_object"), \
         patch("services.media_migration.delete_r2_object"):
        await run_migration(agency_id, job_id)

    item = test_db_session.media_items.find_one({"id": file_id})
    assert item is not None
    assert item["source"] == "album"
    assert item["source_album_id"] == album_id

    job = test_db_session.migration_jobs.find_one({"job_id": job_id})
    assert job["status"] == "completed"
    assert job["migrated"] == 1


async def test_run_migration_handles_r2_copy_failure(test_db_session):
    """If R2 copy fails for a file, it's counted as failed and migration continues."""
    import uuid
    from bson import ObjectId

    agency_id = "mig_agency_4"
    project_oid = ObjectId()
    file_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())

    test_db_session.projects.insert_one({
        "_id": project_oid,
        "agency_id": agency_id,
        "code": "MIG-ERR",
        "metadata": {},
        "portal_deliverables": [{
            "id": "deliv-err",
            "title": "Errors",
            "status": "Uploaded",
            "files": [{
                "id": file_id,
                "file_name": "broken.jpg",
                "content_type": "image/jpeg",
                "r2_key": "deliverables/broken.jpg",
                "r2_url": "https://old/broken.jpg",
            }],
        }],
    })
    test_db_session.migration_jobs.insert_one({
        "job_id": job_id, "agency_id": agency_id,
        "status": "queued", "migrated": 0, "failed": 0, "errors": [],
    })

    from services.media_migration import run_migration

    with patch("services.media_migration.copy_r2_object", side_effect=Exception("Network error")):
        await run_migration(agency_id, job_id)

    job = test_db_session.migration_jobs.find_one({"job_id": job_id})
    assert job["status"] == "completed"
    assert job["failed"] == 1
    assert job["migrated"] == 0
    assert len(job["errors"]) > 0
