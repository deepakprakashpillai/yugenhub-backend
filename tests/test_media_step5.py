"""
Tests for Media Feature — Step 5
Covers: auto-create MediaItem on deliverable upload,
        attach-media endpoint, delete cascade behaviour.
"""
import pytest
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _create_project(async_client, auth_headers, test_db_session):
    """Insert a minimal project with one portal_deliverable."""
    project_id = "000000000000000000000001"
    deliverable_id = "deliv-001"
    test_db_session.projects.update_one(
        {"_id": __import__("bson").ObjectId(project_id)},
        {"$set": {
            "agency_id": "test_agency",
            "code": "PROJ-001",
            "metadata": {},
            "portal_watermark_enabled": False,
            "portal_watermark_text": None,
            "portal_deliverables": [{
                "id": deliverable_id,
                "title": "Wedding Photos",
                "status": "Pending",
                "files": [],
            }],
        }},
        upsert=True,
    )
    return project_id, deliverable_id


# ─── get_or_create_system_folder ─────────────────────────────────────────────

async def test_system_folder_created_idempotent(test_db_session):
    """Calling twice with same path returns same folder_id."""
    from services.media_folders import get_or_create_system_folder

    class FakeDB:
        class media_folders:
            _store = []

            @classmethod
            async def find_one(cls, query):
                name = query.get("name")
                parent_id = query.get("parent_id")
                for f in cls._store:
                    if f["name"] == name and f["parent_id"] == parent_id:
                        return f
                return None

            @classmethod
            async def insert_one(cls, doc):
                cls._store.append(dict(doc))

    db = FakeDB()
    fid1 = await get_or_create_system_folder("ag1", ["Deliverables", "PROJ-1"], db)
    fid2 = await get_or_create_system_folder("ag1", ["Deliverables", "PROJ-1"], db)
    assert fid1 == fid2
    # Should have exactly 2 folders: Deliverables + PROJ-1
    assert len(FakeDB.media_folders._store) == 2


async def test_system_folder_nested_path(test_db_session):
    """Three-level path creates three folders."""
    from services.media_folders import get_or_create_system_folder

    class FakeDB:
        class media_folders:
            _store = []

            @classmethod
            async def find_one(cls, query):
                return None  # always create

            @classmethod
            async def insert_one(cls, doc):
                cls._store.append(dict(doc))

    db = FakeDB()
    await get_or_create_system_folder("ag1", ["Deliverables", "Project A", "Album 1"], db)
    assert len(FakeDB.media_folders._store) == 3
    paths = [f["path"] for f in FakeDB.media_folders._store]
    assert "/Deliverables/" in paths
    assert "/Deliverables/Project A/" in paths
    assert "/Deliverables/Project A/Album 1/" in paths


# ─── add_file_to_deliverable creates MediaItem ────────────────────────────────

async def test_upload_creates_media_item(async_client: AsyncClient, auth_headers, test_db_session):
    """Registering a deliverable file auto-creates a MediaItem."""
    project_id, deliverable_id = await _create_project(async_client, auth_headers, test_db_session)

    with patch("routes.project.generate_presigned_put_url", return_value="https://r2/put"):
        url_resp = await async_client.post(
            f"/api/projects/{project_id}/deliverables/upload-url",
            json={"file_name": "photo.jpg", "content_type": "image/jpeg"},
            headers=auth_headers,
        )
    assert url_resp.status_code == 200
    r2_key = url_resp.json()["r2_key"]
    r2_url = url_resp.json()["r2_url"]

    with patch("services.media_processing.process_thumbnail", new_callable=AsyncMock):
        resp = await async_client.post(
            f"/api/projects/{project_id}/deliverables/{deliverable_id}/files",
            json={
                "file_name": "photo.jpg",
                "content_type": "image/jpeg",
                "r2_key": r2_key,
                "r2_url": r2_url,
                "size_bytes": 2048,
            },
            headers=auth_headers,
        )
    assert resp.status_code == 200
    file_data = resp.json()

    # MediaItem should exist in DB
    media_item = test_db_session.media_items.find_one({"source_deliverable_id": deliverable_id})
    assert media_item is not None
    assert media_item["source"] == "deliverable"
    assert media_item["source_project_id"] == project_id
    assert media_item["status"] == "active"
    assert media_item["name"] == "photo.jpg"
    assert media_item["size_bytes"] == 2048

    # DeliverableFile should have media_item_id set
    project = test_db_session.projects.find_one({"_id": __import__("bson").ObjectId(project_id)})
    deliverable = next(d for d in project["portal_deliverables"] if d["id"] == deliverable_id)
    file_entry = next(f for f in deliverable["files"] if f["id"] == file_data["id"])
    assert file_entry.get("media_item_id") == media_item["id"]


async def test_upload_creates_system_folders(async_client: AsyncClient, auth_headers, test_db_session):
    """Deliverable upload auto-creates system folder hierarchy."""
    project_id, deliverable_id = await _create_project(async_client, auth_headers, test_db_session)

    with patch("routes.project.generate_presigned_put_url", return_value="https://r2/put"):
        url_resp = await async_client.post(
            f"/api/projects/{project_id}/deliverables/upload-url",
            json={"file_name": "vid.mp4", "content_type": "video/mp4"},
            headers=auth_headers,
        )
    r2_key = url_resp.json()["r2_key"]
    r2_url = url_resp.json()["r2_url"]

    with patch("services.media_processing.process_thumbnail", new_callable=AsyncMock):
        await async_client.post(
            f"/api/projects/{project_id}/deliverables/{deliverable_id}/files",
            json={"file_name": "vid.mp4", "content_type": "video/mp4", "r2_key": r2_key, "r2_url": r2_url},
            headers=auth_headers,
        )

    # System folders should exist
    folders = list(test_db_session.media_folders.find({"agency_id": "test_agency", "is_system": True}))
    folder_names = [f["name"] for f in folders]
    assert "Deliverables" in folder_names
    assert "PROJ-001" in folder_names
    assert "Wedding Photos" in folder_names


# ─── attach-media endpoint ────────────────────────────────────────────────────

async def test_attach_media_links_file_to_deliverable(async_client: AsyncClient, auth_headers, test_db_session):
    """attach-media creates a DeliverableFile linked to an existing MediaItem."""
    project_id, deliverable_id = await _create_project(async_client, auth_headers, test_db_session)

    # Create a MediaItem in media library
    import uuid
    media_folder_id = str(uuid.uuid4())
    media_item_id = str(uuid.uuid4())
    test_db_session.media_folders.insert_one({
        "id": media_folder_id, "agency_id": "test_agency", "name": "My Photos",
        "parent_id": None, "path": "/My Photos/", "is_system": False,
    })
    test_db_session.media_items.insert_one({
        "id": media_item_id, "agency_id": "test_agency",
        "folder_id": media_folder_id, "name": "cover.jpg",
        "r2_key": "media/test_agency/files/cover.jpg",
        "r2_url": "https://r2/cover.jpg",
        "content_type": "image/jpeg", "size_bytes": 5000,
        "thumbnail_status": "done", "thumbnail_r2_key": "media/test_agency/thumbs/cover.jpg",
        "thumbnail_r2_url": "https://r2/thumb.jpg",
        "preview_status": "done", "preview_r2_key": "media/test_agency/previews/cover.jpg",
        "preview_r2_url": "https://r2/preview.jpg",
        "watermark_status": "n/a", "source": "direct",
        "status": "active", "uploaded_by": "test_user_id",
    })

    resp = await async_client.post(
        f"/api/projects/{project_id}/deliverables/{deliverable_id}/attach-media",
        json={"media_item_id": media_item_id},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    file_data = resp.json()
    assert file_data["media_item_id"] == media_item_id
    assert file_data["file_name"] == "cover.jpg"
    assert file_data["thumbnail_r2_key"] is not None

    # Deliverable should now have the file
    project = test_db_session.projects.find_one({"_id": __import__("bson").ObjectId(project_id)})
    deliverable = next(d for d in project["portal_deliverables"] if d["id"] == deliverable_id)
    assert len(deliverable["files"]) == 1
    assert deliverable["files"][0]["media_item_id"] == media_item_id


async def test_attach_media_missing_item(async_client: AsyncClient, auth_headers, test_db_session):
    """attach-media returns 404 for non-existent media item."""
    project_id, deliverable_id = await _create_project(async_client, auth_headers, test_db_session)

    resp = await async_client.post(
        f"/api/projects/{project_id}/deliverables/{deliverable_id}/attach-media",
        json={"media_item_id": "nonexistent-id"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_attach_media_missing_deliverable(async_client: AsyncClient, auth_headers, test_db_session):
    """attach-media returns 404 for non-existent deliverable."""
    project_id, _ = await _create_project(async_client, auth_headers, test_db_session)

    import uuid
    media_item_id = str(uuid.uuid4())
    test_db_session.media_items.insert_one({
        "id": media_item_id, "agency_id": "test_agency",
        "folder_id": "some-folder", "name": "img.jpg",
        "r2_key": "media/test_agency/files/img.jpg", "r2_url": "https://r2/img.jpg",
        "content_type": "image/jpeg", "size_bytes": 100,
        "source": "direct", "status": "active",
    })

    resp = await async_client.post(
        f"/api/projects/{project_id}/deliverables/ghost-deliverable/attach-media",
        json={"media_item_id": media_item_id},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_attach_media_missing_body(async_client: AsyncClient, auth_headers, test_db_session):
    """attach-media returns 400 when media_item_id is not provided."""
    project_id, deliverable_id = await _create_project(async_client, auth_headers, test_db_session)

    resp = await async_client.post(
        f"/api/projects/{project_id}/deliverables/{deliverable_id}/attach-media",
        json={},
        headers=auth_headers,
    )
    assert resp.status_code == 400


# ─── delete: media-attached file leaves MediaItem intact ─────────────────────

async def test_delete_attached_file_leaves_media_item(async_client: AsyncClient, auth_headers, test_db_session):
    """Deleting a portal file that was attached from Media library does NOT delete the MediaItem."""
    project_id, deliverable_id = await _create_project(async_client, auth_headers, test_db_session)

    import uuid
    media_item_id = str(uuid.uuid4())
    file_id = str(uuid.uuid4())
    test_db_session.media_items.insert_one({
        "id": media_item_id, "agency_id": "test_agency",
        "folder_id": "folder-1", "name": "hero.jpg",
        "r2_key": "media/test_agency/files/hero.jpg",
        "r2_url": "https://r2/hero.jpg",
        "content_type": "image/jpeg", "size_bytes": 4096,
        "source": "direct",  # attached from Media, not uploaded via portal
        "status": "active", "uploaded_by": "test_user_id",
    })
    # Add file to deliverable
    test_db_session.projects.update_one(
        {"_id": __import__("bson").ObjectId(project_id), "portal_deliverables.id": deliverable_id},
        {"$push": {"portal_deliverables.$.files": {
            "id": file_id, "file_name": "hero.jpg",
            "content_type": "image/jpeg",
            "r2_key": "media/test_agency/files/hero.jpg",
            "r2_url": "https://r2/hero.jpg",
            "media_item_id": media_item_id,
            "thumbnail_status": "n/a", "watermark_status": "n/a", "preview_status": "n/a",
        }}}
    )

    with patch("routes.project.delete_r2_object") as mock_del:
        resp = await async_client.delete(
            f"/api/projects/{project_id}/deliverables/{deliverable_id}/files/{file_id}",
            headers=auth_headers,
        )
    assert resp.status_code == 200

    # R2 should NOT have been deleted
    mock_del.assert_not_called()

    # MediaItem should still exist
    still_there = test_db_session.media_items.find_one({"id": media_item_id})
    assert still_there is not None


async def test_delete_deliverable_file_removes_media_item(async_client: AsyncClient, auth_headers, test_db_session):
    """Deleting a portal file that was originally uploaded via portal DOES delete its MediaItem."""
    project_id, deliverable_id = await _create_project(async_client, auth_headers, test_db_session)

    import uuid
    media_item_id = str(uuid.uuid4())
    file_id = str(uuid.uuid4())
    test_db_session.media_items.insert_one({
        "id": media_item_id, "agency_id": "test_agency",
        "folder_id": "folder-1", "name": "shot.jpg",
        "r2_key": "deliverables/test_agency/proj/shot.jpg",
        "r2_url": "https://r2/shot.jpg",
        "content_type": "image/jpeg", "size_bytes": 1024,
        "source": "deliverable",  # auto-created from portal upload
        "status": "active", "uploaded_by": "test_user_id",
    })
    test_db_session.projects.update_one(
        {"_id": __import__("bson").ObjectId(project_id), "portal_deliverables.id": deliverable_id},
        {"$push": {"portal_deliverables.$.files": {
            "id": file_id, "file_name": "shot.jpg",
            "content_type": "image/jpeg",
            "r2_key": "deliverables/test_agency/proj/shot.jpg",
            "r2_url": "https://r2/shot.jpg",
            "media_item_id": media_item_id,
            "thumbnail_status": "n/a", "watermark_status": "n/a", "preview_status": "n/a",
        }}}
    )

    with patch("routes.project.delete_r2_object"):
        resp = await async_client.delete(
            f"/api/projects/{project_id}/deliverables/{deliverable_id}/files/{file_id}",
            headers=auth_headers,
        )
    assert resp.status_code == 200

    # MediaItem should be gone
    gone = test_db_session.media_items.find_one({"id": media_item_id})
    assert gone is None
