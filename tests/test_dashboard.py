import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_dashboard_stats(async_client: AsyncClient, auth_headers: dict):
    """Dashboard stats returns expected shape."""
    resp = await async_client.get("/api/dashboard/stats", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "active_projects" in data
    assert "my_tasks_due_today" in data


async def test_attention_items(async_client: AsyncClient, auth_headers: dict):
    """Attention items returns a list."""
    resp = await async_client.get("/api/dashboard/attention", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_attention_scope_me(async_client: AsyncClient, auth_headers: dict):
    """Attention items with scope=me."""
    resp = await async_client.get("/api/dashboard/attention?scope=me", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_workload_global(async_client: AsyncClient, auth_headers: dict):
    """Workload stats (global) returns a list of alerts."""
    resp = await async_client.get("/api/dashboard/workload?scope=global", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_workload_me(async_client: AsyncClient, auth_headers: dict):
    """Workload stats (me) returns personal counts."""
    resp = await async_client.get("/api/dashboard/workload?scope=me", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "due_today" in data
    assert "overdue" in data


async def test_pipeline(async_client: AsyncClient, auth_headers: dict):
    """Pipeline returns vertical distribution."""
    resp = await async_client.get("/api/dashboard/pipeline", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_schedule(async_client: AsyncClient, auth_headers: dict):
    """Upcoming schedule returns a list."""
    resp = await async_client.get("/api/dashboard/schedule", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_activity(async_client: AsyncClient, auth_headers: dict):
    """Recent activity returns a list."""
    resp = await async_client.get("/api/dashboard/activity", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
