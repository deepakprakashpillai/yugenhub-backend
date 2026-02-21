import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_get_notifications_empty(async_client: AsyncClient, auth_headers: dict):
    """Initially empty list for the test user."""
    resp = await async_client.get("/api/notifications", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_unread_count(async_client: AsyncClient, auth_headers: dict):
    resp = await async_client.get("/api/notifications/unread-count", headers=auth_headers)
    assert resp.status_code == 200
    assert "count" in resp.json()


async def test_mark_nonexistent_notification(async_client: AsyncClient, auth_headers: dict):
    """Marking a non-existent notification as read returns 404."""
    resp = await async_client.patch(
        "/api/notifications/fake_id/read",
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_mark_all_read(async_client: AsyncClient, auth_headers: dict):
    """Mark all read succeeds even when no notifications exist."""
    resp = await async_client.post(
        "/api/notifications/mark-all-read",
        headers=auth_headers,
    )
    assert resp.status_code == 200
