import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_calendar_returns_items(async_client: AsyncClient, auth_headers: dict):
    """Calendar endpoint returns a list for a valid date range."""
    resp = await async_client.get(
        "/api/calendar?start=2024-01-01&end=2030-12-31",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_calendar_invalid_date(async_client: AsyncClient, auth_headers: dict):
    """Bad date format returns 400."""
    resp = await async_client.get(
        "/api/calendar?start=bad&end=worse",
        headers=auth_headers,
    )
    assert resp.status_code == 400


async def test_calendar_filter_by_task(async_client: AsyncClient, auth_headers: dict):
    """Filter calendar by type=task."""
    resp = await async_client.get(
        "/api/calendar?start=2024-01-01&end=2030-12-31&type=task",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    for item in resp.json():
        assert item["type"] == "task"


async def test_calendar_filter_by_event(async_client: AsyncClient, auth_headers: dict):
    """Filter calendar by type=event."""
    resp = await async_client.get(
        "/api/calendar?start=2024-01-01&end=2030-12-31&type=event",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    for item in resp.json():
        assert item["type"] == "event"
