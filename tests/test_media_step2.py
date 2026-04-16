"""
Tests for Media Feature — Step 2
Covers: folder CRUD, file upload flow, rename/move, delete cascade,
        download URL, search, and process_media_item_thumbnail service.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _create_folder(async_client, auth_headers, name, parent_id=None):
    body = {"name": name}
    if parent_id:
        body["parent_id"] = parent_id
    resp = await async_client.post("/api/media/folders", json=body, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


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
    tree = resp.json()
    root = next(f for f in tree if f["id"] == parent["id"])
    assert len(root["children"]) == 2


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

    # Re-fetch tree and verify child path updated
    tree_resp = await async_client.get("/api/media/folders", headers=auth_headers)
    tree = tree_resp.json()
    renamed = next(f for f in tree if f["id"] == parent["id"])
    assert renamed["path"] == "/Renamed/"
    assert renamed["children"][0]["path"] == "/Renamed/Child/"


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

    # Upload and activate two files
    for fname in ("a.jpg", "b.jpg"):
        with patch("routes.media.generate_presigned_put_url", return_value="https://r2.example.com/u"):
            ur = await async_client.post(
                "/api/media/upload-url",
                json={"file_name": fname, "content_type": "image/jpeg", "folder_id": folder["id"]},
                headers=auth_headers,
            )
        with patch("services.media_processing.process_media_item_thumbnail", new_callable=AsyncMock):
            await async_client.post(
                "/api/media/items",
                json={"media_item_id": ur.json()["media_item_id"], "size_bytes": 1024},
                headers=auth_headers,
            )

    resp = await async_client.get(f"/api/media/folders/{folder['id']}/items", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["data"]) == 2


async def test_list_folder_items_pagination(async_client: AsyncClient, auth_headers):
    folder = await _create_folder(async_client, auth_headers, "BigFolder")
    for i in range(5):
        with patch("routes.media.generate_presigned_put_url", return_value="https://r2/u"):
            ur = await async_client.post(
                "/api/media/upload-url",
                json={"file_name": f"f{i}.jpg", "content_type": "image/jpeg", "folder_id": folder["id"]},
                headers=auth_headers,
            )
        with patch("services.media_processing.process_media_item_thumbnail", new_callable=AsyncMock):
            await async_client.post(
                "/api/media/items",
                json={"media_item_id": ur.json()["media_item_id"], "size_bytes": 100},
                headers=auth_headers,
            )

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
            json={"media_item_id": ur.json()["media_item_id"], "size_bytes": 512},
            headers=auth_headers,
        )
    return ur.json()["media_item_id"]


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

    # Verify no longer in source
    src_items = (await async_client.get(f"/api/media/folders/{src['id']}/items", headers=auth_headers)).json()
    assert src_items["total"] == 0

    # Verify now in destination
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

async def test_process_media_item_thumbnail_image(test_db_session):
    from services.media_processing import process_media_item_thumbnail
    from database import media_items_collection
    import uuid

    item_id = str(uuid.uuid4())
    test_db_session.media_items.insert_one({
        "id": item_id,
        "agency_id": "test_agency",
        "thumbnail_status": "pending",
        "preview_status": "pending",
    })

    fake_bytes = b"fake_image_data"
    with patch("services.media_processing.download_r2_object", return_value=fake_bytes), \
         patch("services.media_processing.generate_thumbnail", return_value=b"thumb"), \
         patch("services.media_processing.generate_preview", return_value=b"preview"), \
         patch("services.media_processing.upload_r2_object"):

        await process_media_item_thumbnail(item_id, "media/a/files/x.jpg", "image/jpeg", "test_agency")

    updated = test_db_session.media_items.find_one({"id": item_id})
    assert updated["thumbnail_status"] == "done"
    assert updated["preview_status"] == "done"
    assert updated["thumbnail_r2_key"] == f"media/test_agency/thumbs/{item_id}.jpg"
    assert updated["preview_r2_key"] == f"media/test_agency/previews/{item_id}.jpg"


async def test_process_media_item_thumbnail_video(test_db_session):
    from services.media_processing import process_media_item_thumbnail
    import uuid

    item_id = str(uuid.uuid4())
    test_db_session.media_items.insert_one({
        "id": item_id,
        "agency_id": "test_agency",
        "thumbnail_status": "pending",
        "preview_status": "n/a",
    })

    with patch("services.media_processing.download_r2_object", return_value=b"video"), \
         patch("services.media_processing.generate_video_thumbnail", return_value=b"thumb"), \
         patch("services.media_processing.upload_r2_object"):

        await process_media_item_thumbnail(item_id, "media/a/files/v.mp4", "video/mp4", "test_agency")

    updated = test_db_session.media_items.find_one({"id": item_id})
    assert updated["thumbnail_status"] == "done"
    assert updated.get("preview_r2_key") is None  # no preview for video


async def test_process_media_item_thumbnail_failure(test_db_session):
    from services.media_processing import process_media_item_thumbnail
    import uuid

    item_id = str(uuid.uuid4())
    test_db_session.media_items.insert_one({
        "id": item_id,
        "agency_id": "test_agency",
        "thumbnail_status": "pending",
        "preview_status": "pending",
    })

    with patch("services.media_processing.download_r2_object", side_effect=Exception("R2 down")):
        await process_media_item_thumbnail(item_id, "media/a/files/x.jpg", "image/jpeg", "test_agency")

    updated = test_db_session.media_items.find_one({"id": item_id})
    assert updated["thumbnail_status"] == "failed"
    assert updated["preview_status"] == "failed"
