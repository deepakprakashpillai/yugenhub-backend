"""
Tests for Media Feature
Covers: models, dependencies, folder/file CRUD, upload flow, share links,
        r2 usage, media_access permissions, deliverable integration, migration.
"""
import pytest
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, AsyncMock
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


# ─── Shared Helpers ───────────────────────────────────────────────────────────

async def _create_folder(async_client, auth_headers, name, parent_id=None):
    body = {"name": name}
    if parent_id:
        body["parent_id"] = parent_id
    resp = await async_client.post("/api/media/folders", json=body, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_active_item(async_client, auth_headers, folder_id, fname="img.jpg"):
    with patch("routes.media.generate_presigned_put_url", return_value="https://r2/u"):
        ur = await async_client.post(
            "/api/media/upload-url",
            json={"file_name": fname, "content_type": "image/jpeg", "folder_id": folder_id},
            headers=auth_headers,
        )
    with patch("services.media_processing.process_media_item_thumbnail", new_callable=AsyncMock):
        await async_client.post(
            "/api/media/items",
            json={"media_item_id": ur.json()["media_item_id"], "size_bytes": 1024},
            headers=auth_headers,
        )
    return ur.json()["media_item_id"]


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


# ─── Model: MediaFolder ───────────────────────────────────────────────────────

def test_media_folder_defaults():
    from models.media import MediaFolder
    folder = MediaFolder(agency_id="agency1", name="My Folder")
    assert folder.id is not None
    assert folder.parent_id is None
    assert folder.path == "/"
    assert folder.is_system is False
    assert folder.created_by is None
    assert isinstance(folder.created_at, datetime)
    assert isinstance(folder.updated_at, datetime)


def test_media_folder_nested():
    from models.media import MediaFolder
    parent = MediaFolder(agency_id="agency1", name="Deliverables", is_system=True)
    child = MediaFolder(
        agency_id="agency1",
        name="Project A",
        parent_id=parent.id,
        path="/Deliverables/Project A/",
        is_system=True,
    )
    assert child.parent_id == parent.id
    assert child.path == "/Deliverables/Project A/"
    assert child.is_system is True


# ─── Model: MediaItem ─────────────────────────────────────────────────────────

def test_media_item_defaults():
    from models.media import MediaItem
    item = MediaItem(
        agency_id="agency1",
        folder_id="folder1",
        name="photo.jpg",
        r2_key="media/agency1/files/abc.jpg",
        r2_url="https://r2.example.com/media/agency1/files/abc.jpg",
        content_type="image/jpeg",
    )
    assert item.id is not None
    assert item.size_bytes == 0
    assert item.thumbnail_status == "pending"
    assert item.preview_status == "n/a"
    assert item.watermark_status == "n/a"
    assert item.source == "direct"
    assert item.source_project_id is None
    assert item.share_token is None
    assert item.status == "pending"
    assert item.uploaded_by is None
    assert isinstance(item.created_at, datetime)


def test_media_item_deliverable_source():
    from models.media import MediaItem
    item = MediaItem(
        agency_id="a", folder_id="f", name="video.mp4",
        r2_key="media/a/files/vid.mp4",
        r2_url="https://r2.example.com/vid.mp4",
        content_type="video/mp4",
        source="deliverable",
        source_project_id="proj1",
        source_deliverable_id="del1",
        status="active",
        size_bytes=1048576,
        uploaded_by="user1",
    )
    assert item.source == "deliverable"
    assert item.source_project_id == "proj1"
    assert item.source_deliverable_id == "del1"
    assert item.status == "active"
    assert item.size_bytes == 1048576


def test_media_item_sharing_fields():
    from models.media import MediaItem
    expires = datetime.now(timezone.utc) + timedelta(days=7)
    item = MediaItem(
        agency_id="a", folder_id="f", name="doc.pdf",
        r2_key="media/a/files/doc.pdf",
        r2_url="https://r2.example.com/doc.pdf",
        content_type="application/pdf",
        share_token="tok_abc123",
        share_expires_at=expires,
        status="active",
    )
    assert item.share_token == "tok_abc123"
    assert item.share_expires_at == expires


# ─── Model: DeliverableFile gains media_item_id ───────────────────────────────

def test_deliverable_file_media_item_id():
    from models.project import DeliverableFile
    f = DeliverableFile(
        file_name="photo.jpg", content_type="image/jpeg",
        r2_key="k", r2_url="u",
    )
    assert hasattr(f, "media_item_id")
    assert f.media_item_id is None

    f2 = DeliverableFile(
        file_name="photo.jpg", content_type="image/jpeg",
        r2_key="k", r2_url="u",
        media_item_id="media_item_abc",
    )
    assert f2.media_item_id == "media_item_abc"


# ─── Model: UserModel gains media_access ─────────────────────────────────────

def test_user_model_media_access():
    from models.user import UserModel
    u = UserModel(email="a@b.com", name="Alice", agency_id="agency1")
    assert hasattr(u, "media_access")
    assert u.media_access is False

    u2 = UserModel(email="a@b.com", name="Alice", agency_id="agency1", media_access=True)
    assert u2.media_access is True


# ─── Dependency: require_media_access ────────────────────────────────────────

async def test_require_media_access_owner_allowed(async_client: AsyncClient, auth_headers):
    """Owner should always pass the media access guard — 404 not 403."""
    resp = await async_client.get("/api/media/nonexistent", headers=auth_headers)
    assert resp.status_code == 404


async def test_require_media_access_member_blocked():
    """A member without media_access should get 403 on guarded routes."""
    from models.user import UserModel
    from fastapi import HTTPException
    from routes.deps import require_media_access

    member = UserModel(
        id="m1", email="m@test.com", name="Member", agency_id="a",
        role="member", media_access=False,
    )
    checker = require_media_access()
    with pytest.raises(HTTPException) as exc_info:
        await checker(current_user=member)
    assert exc_info.value.status_code == 403


async def test_require_media_access_member_with_flag_allowed():
    from models.user import UserModel
    from routes.deps import require_media_access

    member = UserModel(
        id="m2", email="m2@test.com", name="Member2", agency_id="a",
        role="member", media_access=True,
    )
    checker = require_media_access()
    result = await checker(current_user=member)
    assert result.id == "m2"


async def test_require_media_access_admin_allowed():
    from models.user import UserModel
    from routes.deps import require_media_access

    admin = UserModel(
        id="a1", email="admin@test.com", name="Admin", agency_id="a",
        role="admin", media_access=False,
    )
    checker = require_media_access()
    result = await checker(current_user=admin)
    assert result.id == "a1"


# ─── Folder CRUD ──────────────────────────────────────────────────────────────

async def test_list_folders_empty(async_client: AsyncClient, auth_headers):
    resp = await async_client.get("/api/media/folders", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_create_folder(async_client: AsyncClient, auth_headers):
    resp = await async_client.post(
        "/api/media/folders",
        json={"name": "My Uploads"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "My Uploads"
    assert data["path"] == "/My Uploads/"
    assert data["parent_id"] is None
    assert "id" in data


async def test_create_nested_folder(async_client: AsyncClient, auth_headers):
    parent = await _create_folder(async_client, auth_headers, "Deliverables")
    child = await _create_folder(async_client, auth_headers, "Project A", parent_id=parent["id"])
    assert child["parent_id"] == parent["id"]
    assert child["path"] == "/Deliverables/Project A/"


async def test_create_folder_duplicate_rejected(async_client: AsyncClient, auth_headers):
    await _create_folder(async_client, auth_headers, "Archive")
    resp = await async_client.post("/api/media/folders", json={"name": "Archive"}, headers=auth_headers)
    assert resp.status_code == 409


async def test_create_folder_missing_name(async_client: AsyncClient, auth_headers):
    resp = await async_client.post("/api/media/folders", json={"name": ""}, headers=auth_headers)
    assert resp.status_code == 400


async def test_create_folder_invalid_parent(async_client: AsyncClient, auth_headers):
    resp = await async_client.post(
        "/api/media/folders",
        json={"name": "Child", "parent_id": "nonexistent"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_list_folder_tree_structure(async_client: AsyncClient, auth_headers):
    parent = await _create_folder(async_client, auth_headers, "Root")
    await _create_folder(async_client, auth_headers, "Child1", parent_id=parent["id"])
    await _create_folder(async_client, auth_headers, "Child2", parent_id=parent["id"])

    resp = await async_client.get("/api/media/folders", headers=auth_headers)
    assert resp.status_code == 200
    folders = resp.json()
    children = [f for f in folders if f.get("parent_id") == parent["id"]]
    assert len(children) == 2


async def test_rename_folder(async_client: AsyncClient, auth_headers):
    folder = await _create_folder(async_client, auth_headers, "Old Name")
    resp = await async_client.patch(
        f"/api/media/folders/{folder['id']}",
        json={"name": "New Name"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"
    assert resp.json()["path"] == "/New Name/"


async def test_rename_folder_updates_descendant_paths(async_client: AsyncClient, auth_headers):
    parent = await _create_folder(async_client, auth_headers, "Parent")
    child = await _create_folder(async_client, auth_headers, "Child", parent_id=parent["id"])

    await async_client.patch(
        f"/api/media/folders/{parent['id']}",
        json={"name": "Renamed"},
        headers=auth_headers,
    )

    tree_resp = await async_client.get("/api/media/folders", headers=auth_headers)
    folders = tree_resp.json()
    renamed = next(f for f in folders if f["id"] == parent["id"])
    assert renamed["path"] == "/Renamed/"
    child_updated = next(f for f in folders if f["id"] == child["id"])
    assert child_updated["path"] == "/Renamed/Child/"


async def test_rename_folder_not_found(async_client: AsyncClient, auth_headers):
    resp = await async_client.patch(
        "/api/media/folders/doesnotexist",
        json={"name": "X"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_delete_empty_folder(async_client: AsyncClient, auth_headers):
    folder = await _create_folder(async_client, auth_headers, "ToDelete")
    resp = await async_client.delete(f"/api/media/folders/{folder['id']}", headers=auth_headers)
    assert resp.status_code == 204

    tree = (await async_client.get("/api/media/folders", headers=auth_headers)).json()
    assert not any(f["id"] == folder["id"] for f in tree)


async def test_delete_folder_not_found(async_client: AsyncClient, auth_headers):
    resp = await async_client.delete("/api/media/folders/ghost", headers=auth_headers)
    assert resp.status_code == 404


# ─── Upload flow ──────────────────────────────────────────────────────────────

async def test_upload_url_returns_presigned(async_client: AsyncClient, auth_headers):
    folder = await _create_folder(async_client, auth_headers, "Photos")

    with patch("routes.media.generate_presigned_put_url", return_value="https://r2.example.com/upload"):
        resp = await async_client.post(
            "/api/media/upload-url",
            json={"file_name": "photo.jpg", "content_type": "image/jpeg", "folder_id": folder["id"]},
            headers=auth_headers,
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["upload_url"] == "https://r2.example.com/upload"
    assert "media_item_id" in data
    assert data["r2_key"].endswith(".jpg")


async def test_upload_url_invalid_content_type(async_client: AsyncClient, auth_headers):
    folder = await _create_folder(async_client, auth_headers, "Docs")
    resp = await async_client.post(
        "/api/media/upload-url",
        json={"file_name": "script.exe", "content_type": "application/octet-stream", "folder_id": folder["id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 400


async def test_upload_url_invalid_folder(async_client: AsyncClient, auth_headers):
    resp = await async_client.post(
        "/api/media/upload-url",
        json={"file_name": "a.jpg", "content_type": "image/jpeg", "folder_id": "badid"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_register_file_activates_item(async_client: AsyncClient, auth_headers):
    folder = await _create_folder(async_client, auth_headers, "Videos")

    with patch("routes.media.generate_presigned_put_url", return_value="https://r2.example.com/upload"):
        url_resp = await async_client.post(
            "/api/media/upload-url",
            json={"file_name": "clip.mp4", "content_type": "video/mp4", "folder_id": folder["id"]},
            headers=auth_headers,
        )
    item_id = url_resp.json()["media_item_id"]

    with patch("services.media_processing.process_media_item_thumbnail", new_callable=AsyncMock):
        resp = await async_client.post(
            "/api/media/items",
            json={"media_item_id": item_id, "size_bytes": 204800},
            headers=auth_headers,
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


async def test_register_file_not_found(async_client: AsyncClient, auth_headers):
    resp = await async_client.post(
        "/api/media/items",
        json={"media_item_id": "nonexistent"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_register_file_missing_id(async_client: AsyncClient, auth_headers):
    resp = await async_client.post("/api/media/items", json={}, headers=auth_headers)
    assert resp.status_code == 400


# ─── List folder items ────────────────────────────────────────────────────────

async def test_list_folder_items(async_client: AsyncClient, auth_headers):
    folder = await _create_folder(async_client, auth_headers, "Gallery")

    for fname in ("a.jpg", "b.jpg"):
        await _create_active_item(async_client, auth_headers, folder["id"], fname)

    resp = await async_client.get(f"/api/media/folders/{folder['id']}/items", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["data"]) == 2


async def test_list_folder_items_pagination(async_client: AsyncClient, auth_headers):
    folder = await _create_folder(async_client, auth_headers, "BigFolder")
    for i in range(5):
        await _create_active_item(async_client, auth_headers, folder["id"], f"f{i}.jpg")

    resp = await async_client.get(
        f"/api/media/folders/{folder['id']}/items?page=1&limit=3",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert data["total_pages"] == 2
    assert len(data["data"]) == 3


async def test_list_folder_items_invalid_folder(async_client: AsyncClient, auth_headers):
    resp = await async_client.get("/api/media/folders/ghost/items", headers=auth_headers)
    assert resp.status_code == 404


# ─── Rename / Move ────────────────────────────────────────────────────────────

async def test_rename_item(async_client: AsyncClient, auth_headers):
    folder = await _create_folder(async_client, auth_headers, "Src")
    item_id = await _create_active_item(async_client, auth_headers, folder["id"])

    resp = await async_client.patch(
        f"/api/media/items/{item_id}",
        json={"name": "renamed.jpg"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "renamed.jpg"


async def test_move_item(async_client: AsyncClient, auth_headers):
    src = await _create_folder(async_client, auth_headers, "Source")
    dst = await _create_folder(async_client, auth_headers, "Destination")
    item_id = await _create_active_item(async_client, auth_headers, src["id"])

    resp = await async_client.patch(
        f"/api/media/items/{item_id}",
        json={"folder_id": dst["id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["folder_id"] == dst["id"]

    src_items = (await async_client.get(f"/api/media/folders/{src['id']}/items", headers=auth_headers)).json()
    assert src_items["total"] == 0

    dst_items = (await async_client.get(f"/api/media/folders/{dst['id']}/items", headers=auth_headers)).json()
    assert dst_items["total"] == 1


async def test_move_item_invalid_folder(async_client: AsyncClient, auth_headers):
    folder = await _create_folder(async_client, auth_headers, "F")
    item_id = await _create_active_item(async_client, auth_headers, folder["id"])
    resp = await async_client.patch(
        f"/api/media/items/{item_id}",
        json={"folder_id": "badid"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_update_item_nothing_to_update(async_client: AsyncClient, auth_headers):
    folder = await _create_folder(async_client, auth_headers, "F2")
    item_id = await _create_active_item(async_client, auth_headers, folder["id"])
    resp = await async_client.patch(
        f"/api/media/items/{item_id}",
        json={},
        headers=auth_headers,
    )
    assert resp.status_code == 400


# ─── Delete item ──────────────────────────────────────────────────────────────

async def test_delete_item(async_client: AsyncClient, auth_headers):
    folder = await _create_folder(async_client, auth_headers, "DelTest")
    item_id = await _create_active_item(async_client, auth_headers, folder["id"])

    with patch("routes.media.delete_r2_object"):
        resp = await async_client.delete(f"/api/media/items/{item_id}", headers=auth_headers)

    assert resp.status_code == 204

    items = (await async_client.get(f"/api/media/folders/{folder['id']}/items", headers=auth_headers)).json()
    assert items["total"] == 0


async def test_delete_item_not_found(async_client: AsyncClient, auth_headers):
    resp = await async_client.delete("/api/media/items/ghost", headers=auth_headers)
    assert resp.status_code == 404


async def test_delete_folder_with_files_blocked(async_client: AsyncClient, auth_headers):
    folder = await _create_folder(async_client, auth_headers, "Full")
    await _create_active_item(async_client, auth_headers, folder["id"])

    resp = await async_client.delete(f"/api/media/folders/{folder['id']}", headers=auth_headers)
    assert resp.status_code == 409


async def test_delete_folder_cascade(async_client: AsyncClient, auth_headers):
    folder = await _create_folder(async_client, auth_headers, "FullCascade")
    await _create_active_item(async_client, auth_headers, folder["id"])

    with patch("routes.media.delete_r2_object"):
        resp = await async_client.delete(
            f"/api/media/folders/{folder['id']}?cascade=true",
            headers=auth_headers,
        )
    assert resp.status_code == 204


# ─── Download URL ─────────────────────────────────────────────────────────────

async def test_get_download_url(async_client: AsyncClient, auth_headers):
    folder = await _create_folder(async_client, auth_headers, "DL")
    item_id = await _create_active_item(async_client, auth_headers, folder["id"])

    with patch("routes.media.generate_presigned_get_url", return_value="https://r2.example.com/dl"):
        resp = await async_client.get(f"/api/media/items/{item_id}/download", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json()["url"] == "https://r2.example.com/dl"
    assert resp.json()["content_type"] == "image/jpeg"


async def test_get_download_url_not_found(async_client: AsyncClient, auth_headers):
    resp = await async_client.get("/api/media/items/ghost/download", headers=auth_headers)
    assert resp.status_code == 404


# ─── Search ───────────────────────────────────────────────────────────────────

async def test_search_items(async_client: AsyncClient, auth_headers):
    folder = await _create_folder(async_client, auth_headers, "SearchTest")
    await _create_active_item(async_client, auth_headers, folder["id"], "wedding_photo.jpg")
    await _create_active_item(async_client, auth_headers, folder["id"], "corporate_event.jpg")

    with patch("routes.media.generate_presigned_get_url", return_value="https://r2/thumb"):
        resp = await async_client.get("/api/media/search?q=wedding", headers=auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["data"][0]["name"] == "wedding_photo.jpg"


async def test_search_items_no_results(async_client: AsyncClient, auth_headers):
    folder = await _create_folder(async_client, auth_headers, "EmptySearch")
    await _create_active_item(async_client, auth_headers, folder["id"], "photo.jpg")

    resp = await async_client.get("/api/media/search?q=xyznotfound", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


async def test_search_missing_query(async_client: AsyncClient, auth_headers):
    resp = await async_client.get("/api/media/search", headers=auth_headers)
    assert resp.status_code == 422


# ─── Permission guard ─────────────────────────────────────────────────────────

async def test_media_routes_blocked_for_member_without_access(
    async_client: AsyncClient, member_auth_headers
):
    resp = await async_client.get("/api/media/folders", headers=member_auth_headers)
    assert resp.status_code == 403


# ─── process_media_item_thumbnail service ────────────────────────────────────

async def test_process_thumbnail_image(test_db_session):
    from services.media_processing import process_media_item_thumbnail

    item_id = str(uuid.uuid4())
    test_db_session.media_items.insert_one({
        "id": item_id, "agency_id": "test_agency",
        "thumbnail_status": "pending", "preview_status": "pending",
    })

    with patch("services.media_processing.download_r2_object", return_value=b"fake_image_data"), \
         patch("services.media_processing.generate_thumbnail", return_value=b"thumb"), \
         patch("services.media_processing.generate_preview", return_value=b"preview"), \
         patch("services.media_processing.upload_r2_object"):
        await process_media_item_thumbnail(item_id, "media/a/files/x.jpg", "image/jpeg", "test_agency")

    updated = test_db_session.media_items.find_one({"id": item_id})
    assert updated["thumbnail_status"] == "done"
    assert updated["preview_status"] == "done"
    assert updated["thumbnail_r2_key"] == f"media/test_agency/thumbs/{item_id}.jpg"
    assert updated["preview_r2_key"] == f"media/test_agency/previews/{item_id}.jpg"


async def test_process_thumbnail_video(test_db_session):
    from services.media_processing import process_media_item_thumbnail

    item_id = str(uuid.uuid4())
    test_db_session.media_items.insert_one({
        "id": item_id, "agency_id": "test_agency",
        "thumbnail_status": "pending", "preview_status": "n/a",
    })

    with patch("services.media_processing.download_r2_object", return_value=b"video"), \
         patch("services.media_processing.generate_video_thumbnail", return_value=b"thumb"), \
         patch("services.media_processing.upload_r2_object"):
        await process_media_item_thumbnail(item_id, "media/a/files/v.mp4", "video/mp4", "test_agency")

    updated = test_db_session.media_items.find_one({"id": item_id})
    assert updated["thumbnail_status"] == "done"
    assert updated.get("preview_r2_key") is None


async def test_process_thumbnail_failure(test_db_session):
    from services.media_processing import process_media_item_thumbnail

    item_id = str(uuid.uuid4())
    test_db_session.media_items.insert_one({
        "id": item_id, "agency_id": "test_agency",
        "thumbnail_status": "pending", "preview_status": "pending",
    })

    with patch("services.media_processing.download_r2_object", side_effect=Exception("R2 down")):
        await process_media_item_thumbnail(item_id, "media/a/files/x.jpg", "image/jpeg", "test_agency")

    updated = test_db_session.media_items.find_one({"id": item_id})
    assert updated["thumbnail_status"] == "failed"
    assert updated["preview_status"] == "failed"


# ─── Share link — create ──────────────────────────────────────────────────────

async def test_create_share_link_no_expiry(async_client: AsyncClient, auth_headers):
    folder = await _create_folder(async_client, auth_headers, "ShareTest")
    item_id = await _create_active_item(async_client, auth_headers, folder["id"])

    resp = await async_client.post(
        f"/api/media/items/{item_id}/share",
        json={},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "share_url" in data
    assert "token" in data
    assert data["token"]
    assert data["expires_at"] is None


async def test_create_share_link_with_expiry(async_client: AsyncClient, auth_headers):
    folder = await _create_folder(async_client, auth_headers, "ShareExp")
    item_id = await _create_active_item(async_client, auth_headers, folder["id"])

    resp = await async_client.post(
        f"/api/media/items/{item_id}/share",
        json={"expires_in_days": 7},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["expires_at"] is not None


async def test_create_share_link_invalid_expiry(async_client: AsyncClient, auth_headers):
    folder = await _create_folder(async_client, auth_headers, "ShareBad")
    item_id = await _create_active_item(async_client, auth_headers, folder["id"])

    resp = await async_client.post(
        f"/api/media/items/{item_id}/share",
        json={"expires_in_days": 0},
        headers=auth_headers,
    )
    assert resp.status_code == 400


async def test_create_share_link_not_found(async_client: AsyncClient, auth_headers):
    resp = await async_client.post(
        "/api/media/items/ghost/share",
        json={},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_refresh_share_link_generates_new_token(async_client: AsyncClient, auth_headers):
    folder = await _create_folder(async_client, auth_headers, "ShareRefresh")
    item_id = await _create_active_item(async_client, auth_headers, folder["id"])

    r1 = await async_client.post(f"/api/media/items/{item_id}/share", json={}, headers=auth_headers)
    r2 = await async_client.post(f"/api/media/items/{item_id}/share", json={}, headers=auth_headers)
    assert r1.json()["token"] != r2.json()["token"]


# ─── Share link — revoke ──────────────────────────────────────────────────────

async def test_revoke_share_link(async_client: AsyncClient, auth_headers):
    folder = await _create_folder(async_client, auth_headers, "Revoke")
    item_id = await _create_active_item(async_client, auth_headers, folder["id"])

    share = await async_client.post(f"/api/media/items/{item_id}/share", json={}, headers=auth_headers)
    token = share.json()["token"]

    resp = await async_client.delete(f"/api/media/items/{item_id}/share", headers=auth_headers)
    assert resp.status_code == 204

    resolve = await async_client.get(f"/api/media/share/{token}")
    assert resolve.status_code == 404


async def test_revoke_share_link_not_found(async_client: AsyncClient, auth_headers):
    resp = await async_client.delete("/api/media/items/ghost/share", headers=auth_headers)
    assert resp.status_code == 404


# ─── Share link — resolve (public) ───────────────────────────────────────────

async def test_resolve_share_link(async_client: AsyncClient, auth_headers):
    folder = await _create_folder(async_client, auth_headers, "Resolve")
    item_id = await _create_active_item(async_client, auth_headers, folder["id"])

    share = await async_client.post(f"/api/media/items/{item_id}/share", json={}, headers=auth_headers)
    token = share.json()["token"]

    with patch("routes.media.generate_presigned_get_url", return_value="https://r2/dl"):
        resp = await async_client.get(f"/api/media/share/{token}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["url"] == "https://r2/dl"
    assert data["content_type"] == "image/jpeg"
    assert data["file_name"] == "img.jpg"


async def test_resolve_share_link_no_auth_required(async_client: AsyncClient, auth_headers):
    """Public share endpoint must work without Authorization header."""
    folder = await _create_folder(async_client, auth_headers, "Public")
    item_id = await _create_active_item(async_client, auth_headers, folder["id"])

    share = await async_client.post(f"/api/media/items/{item_id}/share", json={}, headers=auth_headers)
    token = share.json()["token"]

    with patch("routes.media.generate_presigned_get_url", return_value="https://r2/dl"):
        resp = await async_client.get(f"/api/media/share/{token}")

    assert resp.status_code == 200


async def test_resolve_share_link_invalid_token(async_client: AsyncClient):
    resp = await async_client.get("/api/media/share/invalidtoken123")
    assert resp.status_code == 404


async def test_resolve_share_link_expired(async_client: AsyncClient, auth_headers, test_db_session):
    """Expired share links should return 410 Gone."""
    folder = await _create_folder(async_client, auth_headers, "Expired")
    item_id = await _create_active_item(async_client, auth_headers, folder["id"])

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    token = "expired_test_token_xyz"
    test_db_session.media_items.update_one(
        {"id": item_id},
        {"$set": {"share_token": token, "share_expires_at": past}},
    )

    resp = await async_client.get(f"/api/media/share/{token}")
    assert resp.status_code == 410


# ─── r2_usage — _categorise_key ──────────────────────────────────────────────

@pytest.mark.parametrize("key,expected", [
    ("media/ag1/files/abc.jpg", "original"),
    ("media/ag1/thumbs/abc.jpg", "thumbnails"),
    ("media/ag1/previews/abc.jpg", "previews"),
    ("media/ag1/watermarks/abc_wm.mp4", "watermarks"),
    ("media/ag1/unknown/abc.jpg", "other"),
])
def test_categorise_key(key, expected):
    from services.r2_usage import _categorise_key
    assert _categorise_key(key, "ag1") == expected


# ─── r2_usage — calculate_bucket_stats ───────────────────────────────────────

async def test_calculate_bucket_stats(test_db_session):
    from services.r2_usage import calculate_bucket_stats

    fake_pages = [{"Contents": [
        {"Key": "media/test_agency/files/a.jpg", "Size": 1000},
        {"Key": "media/test_agency/files/b.mp4", "Size": 2000},
        {"Key": "media/test_agency/thumbs/a.jpg", "Size": 100},
        {"Key": "media/test_agency/previews/a.jpg", "Size": 200},
        {"Key": "media/test_agency/watermarks/b_wm.mp4", "Size": 300},
    ]}]

    mock_r2 = MagicMock()
    mock_r2.get_paginator.return_value.paginate.return_value = fake_pages

    class FakeDB:
        class bucket_stats_cache:
            @staticmethod
            async def update_one(filter, update, upsert=False):
                pass

    with patch("services.r2_usage.get_r2_client", return_value=mock_r2):
        stats = await calculate_bucket_stats("test_agency", FakeDB())

    assert stats["total_bytes"] == 3600
    assert stats["original_bytes"] == 3000
    assert stats["thumbnail_bytes"] == 100
    assert stats["preview_bytes"] == 200
    assert stats["watermark_bytes"] == 300
    assert stats["file_count"] == 5
    assert stats["derived_bytes"] == 600
    assert stats["is_stale"] is False


async def test_calculate_bucket_stats_empty_bucket():
    from services.r2_usage import calculate_bucket_stats

    mock_r2 = MagicMock()
    mock_r2.get_paginator.return_value.paginate.return_value = [{"Contents": []}]

    class FakeDB:
        class bucket_stats_cache:
            @staticmethod
            async def update_one(filter, update, upsert=False):
                pass

    with patch("services.r2_usage.get_r2_client", return_value=mock_r2):
        stats = await calculate_bucket_stats("test_agency", FakeDB())

    assert stats["total_bytes"] == 0
    assert stats["file_count"] == 0


# ─── r2_usage — get_cached_stats ─────────────────────────────────────────────

async def test_get_cached_stats_fresh():
    from services.r2_usage import get_cached_stats

    fresh_doc = {
        "agency_id": "test_agency",
        "total_bytes": 5000,
        "last_updated": datetime.now(timezone.utc),
        "is_stale": False,
    }

    class FakeDB:
        class bucket_stats_cache:
            @staticmethod
            async def find_one(filter):
                return dict(fresh_doc)

    stats = await get_cached_stats("test_agency", FakeDB())
    assert stats["total_bytes"] == 5000
    assert stats["is_stale"] is False


async def test_get_cached_stats_stale_flag():
    from services.r2_usage import get_cached_stats

    old_time = datetime.now(timezone.utc) - timedelta(hours=25)

    class FakeDB:
        class bucket_stats_cache:
            @staticmethod
            async def find_one(filter):
                return {"agency_id": "test_agency", "total_bytes": 1000,
                        "last_updated": old_time, "is_stale": False}

    stats = await get_cached_stats("test_agency", FakeDB())
    assert stats["is_stale"] is True


async def test_get_cached_stats_no_cache_computes():
    from services.r2_usage import get_cached_stats

    mock_r2 = MagicMock()
    mock_r2.get_paginator.return_value.paginate.return_value = [{"Contents": []}]

    class FakeDB:
        class bucket_stats_cache:
            @staticmethod
            async def find_one(filter):
                return None

            @staticmethod
            async def update_one(filter, update, upsert=False):
                pass

    with patch("services.r2_usage.get_r2_client", return_value=mock_r2):
        stats = await get_cached_stats("test_agency", FakeDB())

    assert stats["total_bytes"] == 0
    assert stats["is_stale"] is False


# ─── Usage endpoints ──────────────────────────────────────────────────────────

async def test_get_usage_stats_endpoint(async_client: AsyncClient, auth_headers):
    fake_stats = {
        "agency_id": "test_agency",
        "total_bytes": 10000,
        "original_bytes": 8000,
        "thumbnail_bytes": 500,
        "preview_bytes": 1000,
        "watermark_bytes": 200,
        "other_bytes": 300,
        "derived_bytes": 1700,
        "file_count": 12,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "is_stale": False,
    }
    with patch("services.r2_usage.get_cached_stats", new_callable=AsyncMock, return_value=fake_stats):
        resp = await async_client.get("/api/media/usage", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json()["total_bytes"] == 10000
    assert resp.json()["is_stale"] is False


async def test_refresh_usage_stats_endpoint(async_client: AsyncClient, auth_headers):
    with patch("services.r2_usage.calculate_bucket_stats", new_callable=AsyncMock):
        resp = await async_client.post("/api/media/usage/refresh", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "refreshing"


async def test_usage_endpoints_blocked_for_member(async_client: AsyncClient, member_auth_headers):
    resp = await async_client.get("/api/media/usage", headers=member_auth_headers)
    assert resp.status_code == 403

    resp = await async_client.post("/api/media/usage/refresh", headers=member_auth_headers)
    assert resp.status_code == 403


# ─── media_access: team endpoint exposure ────────────────────────────────────

async def test_get_team_includes_media_access_for_owner(
    async_client: AsyncClient, auth_headers, test_user
):
    resp = await async_client.get("/api/settings/team", headers=auth_headers)
    assert resp.status_code == 200
    owner = next(m for m in resp.json() if m["id"] == test_user["id"])
    assert "media_access" in owner


async def test_get_team_hides_media_access_from_member(
    async_client: AsyncClient, member_auth_headers
):
    resp = await async_client.get("/api/settings/team", headers=member_auth_headers)
    assert resp.status_code == 200
    for member in resp.json():
        assert "media_access" not in member


# ─── media_access: invite ────────────────────────────────────────────────────

async def test_invite_with_media_access_true(
    async_client: AsyncClient, auth_headers
):
    resp = await async_client.post(
        "/api/settings/team/invite",
        json={"email": "mediauser@test.com", "role": "member", "media_access": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    user_id = resp.json()["user_id"]

    team = (await async_client.get("/api/settings/team", headers=auth_headers)).json()
    invited = next((m for m in team if m.get("id") == user_id), None)
    assert invited is not None
    assert invited["media_access"] is True


async def test_invite_with_media_access_false_by_default(
    async_client: AsyncClient, auth_headers
):
    resp = await async_client.post(
        "/api/settings/team/invite",
        json={"email": "nomedia@test.com", "role": "member"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    user_id = resp.json()["user_id"]

    team = (await async_client.get("/api/settings/team", headers=auth_headers)).json()
    invited = next((m for m in team if m.get("id") == user_id), None)
    assert invited is not None
    assert invited["media_access"] is False


async def test_invite_media_access_stripped_for_non_owner(test_db_session):
    """Admin cannot grant media_access — it should be silently stripped."""
    admin_data = {
        "id": "admin_with_team_mgmt",
        "email": "admin_mgr@test.com",
        "name": "Admin Mgr",
        "role": "admin",
        "agency_id": "test_agency",
        "can_manage_team": True,
        "media_access": False,
    }
    test_db_session.users.update_one({"id": admin_data["id"]}, {"$set": admin_data}, upsert=True)

    from routes.deps import create_access_token
    admin_token = create_access_token(
        data={"sub": admin_data["id"], "agency_id": admin_data["agency_id"]},
        expires_delta=timedelta(minutes=60),
    )
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    from httpx import AsyncClient as AC, ASGITransport
    from main import app
    async with AC(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        resp = await client.post(
            "/api/settings/team/invite",
            json={"email": "sneaky@test.com", "role": "member", "media_access": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        user_id = resp.json()["user_id"]

    saved = test_db_session.users.find_one({"id": user_id})
    assert saved.get("media_access", False) is False


# ─── media_access: grant/revoke ──────────────────────────────────────────────

async def test_update_access_grant_media(async_client: AsyncClient, auth_headers, test_db_session):
    member = {
        "id": "member_for_media_grant",
        "email": "grantme@test.com",
        "name": "Grant Me",
        "role": "member",
        "agency_id": "test_agency",
        "media_access": False,
    }
    test_db_session.users.update_one({"id": member["id"]}, {"$set": member}, upsert=True)

    resp = await async_client.patch(
        f"/api/settings/team/{member['id']}/access",
        json={"media_access": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert "media_access" in resp.json().get("updated", [])

    saved = test_db_session.users.find_one({"id": member["id"]})
    assert saved["media_access"] is True


async def test_update_access_revoke_media(async_client: AsyncClient, auth_headers, test_db_session):
    member = {
        "id": "member_with_media",
        "email": "revokeme@test.com",
        "name": "Revoke Me",
        "role": "member",
        "agency_id": "test_agency",
        "media_access": True,
    }
    test_db_session.users.update_one({"id": member["id"]}, {"$set": member}, upsert=True)

    resp = await async_client.patch(
        f"/api/settings/team/{member['id']}/access",
        json={"media_access": False},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    saved = test_db_session.users.find_one({"id": member["id"]})
    assert saved["media_access"] is False


async def test_update_access_invalid_type(async_client: AsyncClient, auth_headers, test_db_session):
    member = {
        "id": "member_bad_type",
        "email": "badtype@test.com",
        "name": "Bad Type",
        "role": "member",
        "agency_id": "test_agency",
    }
    test_db_session.users.update_one({"id": member["id"]}, {"$set": member}, upsert=True)

    resp = await async_client.patch(
        f"/api/settings/team/{member['id']}/access",
        json={"media_access": "yes"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


async def test_update_access_owner_only(async_client: AsyncClient, member_auth_headers, test_db_session):
    member = {
        "id": "another_member",
        "email": "another@test.com",
        "name": "Another",
        "role": "member",
        "agency_id": "test_agency",
    }
    test_db_session.users.update_one({"id": member["id"]}, {"$set": member}, upsert=True)

    resp = await async_client.patch(
        f"/api/settings/team/{member['id']}/access",
        json={"media_access": True},
        headers=member_auth_headers,
    )
    assert resp.status_code == 403


async def test_dev_login_returns_media_access(async_client: AsyncClient, test_user):
    resp = await async_client.post(f"/api/auth/dev/login/{test_user['id']}")
    assert resp.status_code == 200
    assert "media_access" in resp.json()["user"]


async def test_member_with_media_access_allowed(test_db_session):
    """Member with media_access=True can access media routes."""
    member = {
        "id": "media_member",
        "email": "mediamember@test.com",
        "name": "Media Member",
        "role": "member",
        "agency_id": "test_agency",
        "media_access": True,
    }
    test_db_session.users.update_one({"id": member["id"]}, {"$set": member}, upsert=True)

    from routes.deps import create_access_token
    token = create_access_token(
        data={"sub": member["id"], "agency_id": member["agency_id"]},
        expires_delta=timedelta(minutes=60),
    )
    headers = {"Authorization": f"Bearer {token}"}

    from httpx import AsyncClient as AC, ASGITransport
    from main import app
    async with AC(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        resp = await client.get("/api/media/folders", headers=headers)
    assert resp.status_code == 200


# ─── get_or_create_system_folder ─────────────────────────────────────────────

async def test_system_folder_created_idempotent():
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
    assert len(FakeDB.media_folders._store) == 2


async def test_system_folder_nested_path():
    from services.media_folders import get_or_create_system_folder

    class FakeDB:
        class media_folders:
            _store = []

            @classmethod
            async def find_one(cls, query):
                return None

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


# ─── Deliverable upload creates MediaItem ────────────────────────────────────

async def test_upload_creates_media_item(async_client: AsyncClient, auth_headers, test_db_session):
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

    media_item = test_db_session.media_items.find_one({"source_deliverable_id": deliverable_id})
    assert media_item is not None
    assert media_item["source"] == "deliverable"
    assert media_item["source_project_id"] == project_id
    assert media_item["status"] == "active"
    assert media_item["name"] == "photo.jpg"
    assert media_item["size_bytes"] == 2048

    project = test_db_session.projects.find_one({"_id": __import__("bson").ObjectId(project_id)})
    deliverable = next(d for d in project["portal_deliverables"] if d["id"] == deliverable_id)
    file_entry = next(f for f in deliverable["files"] if f["id"] == file_data["id"])
    assert file_entry.get("media_item_id") == media_item["id"]


async def test_upload_creates_system_folders(async_client: AsyncClient, auth_headers, test_db_session):
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

    folders = list(test_db_session.media_folders.find({"agency_id": "test_agency", "is_system": True}))
    folder_names = [f["name"] for f in folders]
    assert "Deliverables" in folder_names
    assert "PROJ-001" in folder_names
    assert "Wedding Photos" in folder_names


# ─── attach-media endpoint ────────────────────────────────────────────────────

async def test_attach_media_links_file_to_deliverable(async_client: AsyncClient, auth_headers, test_db_session):
    project_id, deliverable_id = await _create_project(async_client, auth_headers, test_db_session)

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

    project = test_db_session.projects.find_one({"_id": __import__("bson").ObjectId(project_id)})
    deliverable = next(d for d in project["portal_deliverables"] if d["id"] == deliverable_id)
    assert len(deliverable["files"]) == 1
    assert deliverable["files"][0]["media_item_id"] == media_item_id


async def test_attach_media_missing_item(async_client: AsyncClient, auth_headers, test_db_session):
    project_id, deliverable_id = await _create_project(async_client, auth_headers, test_db_session)

    resp = await async_client.post(
        f"/api/projects/{project_id}/deliverables/{deliverable_id}/attach-media",
        json={"media_item_id": "nonexistent-id"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_attach_media_missing_deliverable(async_client: AsyncClient, auth_headers, test_db_session):
    project_id, _ = await _create_project(async_client, auth_headers, test_db_session)

    item_id = str(uuid.uuid4())
    test_db_session.media_items.insert_one({
        "id": item_id, "agency_id": "test_agency",
        "folder_id": "some-folder", "name": "img.jpg",
        "r2_key": "media/test_agency/files/img.jpg", "r2_url": "https://r2/img.jpg",
        "content_type": "image/jpeg", "size_bytes": 100,
        "source": "direct", "status": "active",
    })

    resp = await async_client.post(
        f"/api/projects/{project_id}/deliverables/ghost-deliverable/attach-media",
        json={"media_item_id": item_id},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_attach_media_missing_body(async_client: AsyncClient, auth_headers, test_db_session):
    project_id, deliverable_id = await _create_project(async_client, auth_headers, test_db_session)

    resp = await async_client.post(
        f"/api/projects/{project_id}/deliverables/{deliverable_id}/attach-media",
        json={},
        headers=auth_headers,
    )
    assert resp.status_code == 400


# ─── Delete: cascade behaviour ───────────────────────────────────────────────

async def test_delete_attached_file_leaves_media_item(async_client: AsyncClient, auth_headers, test_db_session):
    """Deleting a portal file attached from Media library does NOT delete the MediaItem."""
    project_id, deliverable_id = await _create_project(async_client, auth_headers, test_db_session)

    media_item_id = str(uuid.uuid4())
    file_id = str(uuid.uuid4())
    test_db_session.media_items.insert_one({
        "id": media_item_id, "agency_id": "test_agency",
        "folder_id": "folder-1", "name": "hero.jpg",
        "r2_key": "media/test_agency/files/hero.jpg",
        "r2_url": "https://r2/hero.jpg",
        "content_type": "image/jpeg", "size_bytes": 4096,
        "source": "direct", "status": "active", "uploaded_by": "test_user_id",
    })
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
    mock_del.assert_not_called()
    assert test_db_session.media_items.find_one({"id": media_item_id}) is not None


async def test_delete_deliverable_file_removes_media_item(async_client: AsyncClient, auth_headers, test_db_session):
    """Deleting a portal file uploaded via portal DOES delete its MediaItem."""
    project_id, deliverable_id = await _create_project(async_client, auth_headers, test_db_session)

    media_item_id = str(uuid.uuid4())
    file_id = str(uuid.uuid4())
    test_db_session.media_items.insert_one({
        "id": media_item_id, "agency_id": "test_agency",
        "folder_id": "folder-1", "name": "shot.jpg",
        "r2_key": "deliverables/test_agency/proj/shot.jpg",
        "r2_url": "https://r2/shot.jpg",
        "content_type": "image/jpeg", "size_bytes": 1024,
        "source": "deliverable", "status": "active", "uploaded_by": "test_user_id",
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
    assert test_db_session.media_items.find_one({"id": media_item_id}) is None


# ─── Migration endpoints ──────────────────────────────────────────────────────

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


async def test_get_migration_status_no_job(async_client: AsyncClient, auth_headers):
    resp = await async_client.get("/api/settings/migrate-to-media/status", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_started"


async def test_get_migration_status_returns_latest(async_client: AsyncClient, auth_headers, test_db_session):
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


async def test_get_migration_status_owner_only(async_client: AsyncClient, member_auth_headers):
    resp = await async_client.get("/api/settings/migrate-to-media/status", headers=member_auth_headers)
    assert resp.status_code == 403


# ─── run_migration logic ──────────────────────────────────────────────────────

async def test_run_migration_migrates_deliverable_file(test_db_session):
    from bson import ObjectId
    from services.media_migration import run_migration

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

    with patch("services.media_migration.copy_r2_object") as mock_copy, \
         patch("services.media_migration.delete_r2_object") as mock_del:
        await run_migration(agency_id, job_id)

    item = test_db_session.media_items.find_one({"id": file_id})
    assert item is not None
    assert item["source"] == "deliverable"
    assert item["r2_key"] == f"media/{agency_id}/files/{file_id}.jpg"
    assert item["status"] == "active"

    project = test_db_session.projects.find_one({"_id": project_oid})
    file_entry = project["portal_deliverables"][0]["files"][0]
    assert file_entry["media_item_id"] == file_id
    assert file_entry["r2_key"] == f"media/{agency_id}/files/{file_id}.jpg"

    job = test_db_session.migration_jobs.find_one({"job_id": job_id})
    assert job["status"] == "completed"
    assert job["migrated"] == 1
    assert job["failed"] == 0
    mock_copy.assert_called_once()
    mock_del.assert_called_once()


async def test_run_migration_skips_already_migrated(test_db_session):
    from bson import ObjectId
    from services.media_migration import run_migration

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
                "media_item_id": file_id,
            }],
        }],
    })
    test_db_session.migration_jobs.insert_one({
        "job_id": job_id, "agency_id": agency_id,
        "status": "queued", "migrated": 0, "failed": 0, "errors": [],
    })

    with patch("services.media_migration.copy_r2_object") as mock_copy:
        await run_migration(agency_id, job_id)

    mock_copy.assert_not_called()
    job = test_db_session.migration_jobs.find_one({"job_id": job_id})
    assert job["migrated"] == 0


async def test_run_migration_migrates_album_file(test_db_session):
    from services.media_migration import run_migration

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
    from bson import ObjectId
    from services.media_migration import run_migration

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

    with patch("services.media_migration.copy_r2_object", side_effect=Exception("Network error")):
        await run_migration(agency_id, job_id)

    job = test_db_session.migration_jobs.find_one({"job_id": job_id})
    assert job["status"] == "completed"
    assert job["failed"] == 1
    assert job["migrated"] == 0
    assert len(job["errors"]) > 0
