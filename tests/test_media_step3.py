"""
Tests for Media Feature — Step 3
Covers: share link create/revoke/resolve, expiry enforcement,
        r2_usage service (categorise, cache, stale flag), usage endpoints.
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, AsyncMock
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _make_folder(async_client, auth_headers, name="F"):
    r = await async_client.post("/api/media/folders", json={"name": name}, headers=auth_headers)
    assert r.status_code == 201
    return r.json()


async def _make_active_item(async_client, auth_headers, folder_id, fname="img.jpg"):
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


# ─── Share link — create ──────────────────────────────────────────────────────

async def test_create_share_link_no_expiry(async_client: AsyncClient, auth_headers):
    folder = await _make_folder(async_client, auth_headers, "ShareTest")
    item_id = await _make_active_item(async_client, auth_headers, folder["id"])

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
    folder = await _make_folder(async_client, auth_headers, "ShareExp")
    item_id = await _make_active_item(async_client, auth_headers, folder["id"])

    resp = await async_client.post(
        f"/api/media/items/{item_id}/share",
        json={"expires_in_days": 7},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["expires_at"] is not None


async def test_create_share_link_invalid_expiry(async_client: AsyncClient, auth_headers):
    folder = await _make_folder(async_client, auth_headers, "ShareBad")
    item_id = await _make_active_item(async_client, auth_headers, folder["id"])

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
    """Calling create share twice should give different tokens."""
    folder = await _make_folder(async_client, auth_headers, "ShareRefresh")
    item_id = await _make_active_item(async_client, auth_headers, folder["id"])

    r1 = await async_client.post(f"/api/media/items/{item_id}/share", json={}, headers=auth_headers)
    r2 = await async_client.post(f"/api/media/items/{item_id}/share", json={}, headers=auth_headers)
    assert r1.json()["token"] != r2.json()["token"]


# ─── Share link — revoke ──────────────────────────────────────────────────────

async def test_revoke_share_link(async_client: AsyncClient, auth_headers):
    folder = await _make_folder(async_client, auth_headers, "Revoke")
    item_id = await _make_active_item(async_client, auth_headers, folder["id"])

    share = await async_client.post(f"/api/media/items/{item_id}/share", json={}, headers=auth_headers)
    token = share.json()["token"]

    resp = await async_client.delete(f"/api/media/items/{item_id}/share", headers=auth_headers)
    assert resp.status_code == 204

    # Token should no longer resolve
    resolve = await async_client.get(f"/api/media/share/{token}")
    assert resolve.status_code == 404


async def test_revoke_share_link_not_found(async_client: AsyncClient, auth_headers):
    resp = await async_client.delete("/api/media/items/ghost/share", headers=auth_headers)
    assert resp.status_code == 404


# ─── Share link — resolve (public) ───────────────────────────────────────────

async def test_resolve_share_link(async_client: AsyncClient, auth_headers):
    folder = await _make_folder(async_client, auth_headers, "Resolve")
    item_id = await _make_active_item(async_client, auth_headers, folder["id"])

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
    folder = await _make_folder(async_client, auth_headers, "Public")
    item_id = await _make_active_item(async_client, auth_headers, folder["id"])

    share = await async_client.post(f"/api/media/items/{item_id}/share", json={}, headers=auth_headers)
    token = share.json()["token"]

    with patch("routes.media.generate_presigned_get_url", return_value="https://r2/dl"):
        resp = await async_client.get(f"/api/media/share/{token}")  # no auth_headers

    assert resp.status_code == 200


async def test_resolve_share_link_invalid_token(async_client: AsyncClient):
    resp = await async_client.get("/api/media/share/invalidtoken123")
    assert resp.status_code == 404


async def test_resolve_share_link_expired(async_client: AsyncClient, auth_headers, test_db_session):
    """Expired share links should return 410 Gone."""
    folder = await _make_folder(async_client, auth_headers, "Expired")
    item_id = await _make_active_item(async_client, auth_headers, folder["id"])

    # Set an already-expired expiry directly in DB
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    token = "expired_test_token_xyz"
    test_db_session.media_items.update_one(
        {"id": item_id},
        {"$set": {"share_token": token, "share_expires_at": past}}
    )

    resp = await async_client.get(f"/api/media/share/{token}")
    assert resp.status_code == 410


# ─── r2_usage — _categorise_key ──────────────────────────────────────────────

def test_categorise_key_original():
    from services.r2_usage import _categorise_key
    assert _categorise_key("media/ag1/files/abc.jpg", "ag1") == "original"


def test_categorise_key_thumbnails():
    from services.r2_usage import _categorise_key
    assert _categorise_key("media/ag1/thumbs/abc.jpg", "ag1") == "thumbnails"


def test_categorise_key_previews():
    from services.r2_usage import _categorise_key
    assert _categorise_key("media/ag1/previews/abc.jpg", "ag1") == "previews"


def test_categorise_key_watermarks():
    from services.r2_usage import _categorise_key
    assert _categorise_key("media/ag1/watermarks/abc_wm.mp4", "ag1") == "watermarks"


def test_categorise_key_other():
    from services.r2_usage import _categorise_key
    assert _categorise_key("media/ag1/unknown/abc.jpg", "ag1") == "other"


# ─── r2_usage — calculate_bucket_stats ───────────────────────────────────────

async def test_calculate_bucket_stats(async_client: AsyncClient, auth_headers, test_db_session):
    from services.r2_usage import calculate_bucket_stats

    fake_pages = [{
        "Contents": [
            {"Key": "media/test_agency/files/a.jpg", "Size": 1000},
            {"Key": "media/test_agency/files/b.mp4", "Size": 2000},
            {"Key": "media/test_agency/thumbs/a.jpg", "Size": 100},
            {"Key": "media/test_agency/previews/a.jpg", "Size": 200},
            {"Key": "media/test_agency/watermarks/b_wm.mp4", "Size": 300},
        ]
    }]

    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = fake_pages

    mock_r2 = MagicMock()
    mock_r2.get_paginator.return_value = mock_paginator

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


async def test_calculate_bucket_stats_empty_bucket(test_db_session):
    from services.r2_usage import calculate_bucket_stats

    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [{"Contents": []}]
    mock_r2 = MagicMock()
    mock_r2.get_paginator.return_value = mock_paginator

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

async def test_get_cached_stats_fresh(test_db_session):
    from services.r2_usage import get_cached_stats
    from datetime import datetime, timezone

    fresh_doc = {
        "agency_id": "test_agency",
        "total_bytes": 5000,
        "last_updated": datetime.now(timezone.utc),
        "is_stale": False,
    }
    test_db_session.bucket_stats_cache.insert_one(dict(fresh_doc))

    class FakeDB:
        class bucket_stats_cache:
            @staticmethod
            async def find_one(filter):
                return dict(fresh_doc)

    stats = await get_cached_stats("test_agency", FakeDB())
    assert stats["total_bytes"] == 5000
    assert stats["is_stale"] is False


async def test_get_cached_stats_stale_flag(test_db_session):
    from services.r2_usage import get_cached_stats

    old_time = datetime.now(timezone.utc) - timedelta(hours=25)
    stale_doc = {
        "agency_id": "test_agency",
        "total_bytes": 1000,
        "last_updated": old_time,
        "is_stale": False,
    }

    class FakeDB:
        class bucket_stats_cache:
            @staticmethod
            async def find_one(filter):
                return dict(stale_doc)

    stats = await get_cached_stats("test_agency", FakeDB())
    assert stats["is_stale"] is True


async def test_get_cached_stats_no_cache_computes(test_db_session):
    """If no cache exists, it should fall through to calculate_bucket_stats."""
    from services.r2_usage import get_cached_stats

    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [{"Contents": []}]
    mock_r2 = MagicMock()
    mock_r2.get_paginator.return_value = mock_paginator

    class FakeDB:
        class bucket_stats_cache:
            @staticmethod
            async def find_one(filter):
                return None  # no cache

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
    # get_cached_stats is lazy-imported inside the route handler; patch at its origin
    with patch("services.r2_usage.get_cached_stats", new_callable=AsyncMock, return_value=fake_stats):
        resp = await async_client.get("/api/media/usage", headers=auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_bytes"] == 10000
    assert data["is_stale"] is False


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
