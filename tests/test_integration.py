import pytest
from httpx import AsyncClient
import os

pytestmark = pytest.mark.asyncio

# The test API key matches conftest's env setup
TEST_API_KEY = "test_n8n_api_key_for_testing"
AGENCY_ID = "test_agency"


@pytest.fixture(scope="function", autouse=True)
def set_n8n_key():
    """Ensure N8N_API_KEY is set for integration tests."""
    from config import config
    original = config.N8N_API_KEY
    config.N8N_API_KEY = TEST_API_KEY
    yield
    config.N8N_API_KEY = original


def api_headers():
    return {"X-API-Key": TEST_API_KEY}


def base_params():
    return {"agency_id": AGENCY_ID}


# ─── Auth Tests ──────────────────────────────────────────────────────────────


async def test_no_api_key_returns_401(async_client: AsyncClient):
    """Requests without API key are rejected."""
    resp = await async_client.get("/api/integration/projects", params=base_params())
    assert resp.status_code == 401


async def test_wrong_api_key_returns_403(async_client: AsyncClient):
    """Requests with wrong API key are rejected."""
    resp = await async_client.get(
        "/api/integration/projects",
        headers={"X-API-Key": "wrong_key"},
        params=base_params(),
    )
    assert resp.status_code == 403


async def test_missing_agency_id_returns_422(async_client: AsyncClient):
    """Requests without agency_id are rejected with validation error."""
    resp = await async_client.get("/api/integration/projects", headers=api_headers())
    assert resp.status_code == 422


# ─── Projects ────────────────────────────────────────────────────────────────


async def test_list_projects(async_client: AsyncClient):
    """List projects returns expected shape."""
    resp = await async_client.get(
        "/api/integration/projects", headers=api_headers(), params=base_params()
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "data" in data
    assert isinstance(data["data"], list)


async def test_project_stats(async_client: AsyncClient):
    """Project stats returns expected fields."""
    resp = await async_client.get(
        "/api/integration/projects/stats", headers=api_headers(), params=base_params()
    )
    assert resp.status_code == 200
    data = resp.json()
    for key in ("total", "active", "ongoing", "this_month"):
        assert key in data


async def test_get_project_not_found(async_client: AsyncClient):
    """Fetching non-existent project returns 404."""
    from bson import ObjectId
    fake_id = str(ObjectId())
    resp = await async_client.get(
        f"/api/integration/projects/{fake_id}",
        headers=api_headers(),
        params=base_params(),
    )
    assert resp.status_code == 404


# ─── Clients ─────────────────────────────────────────────────────────────────


async def test_list_clients(async_client: AsyncClient):
    """List clients returns expected shape."""
    resp = await async_client.get(
        "/api/integration/clients", headers=api_headers(), params=base_params()
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "data" in data


async def test_client_stats(async_client: AsyncClient):
    """Client stats returns expected fields."""
    resp = await async_client.get(
        "/api/integration/clients/stats", headers=api_headers(), params=base_params()
    )
    assert resp.status_code == 200
    data = resp.json()
    for key in ("total", "active", "this_month"):
        assert key in data


# ─── Associates ──────────────────────────────────────────────────────────────


async def test_list_associates(async_client: AsyncClient):
    """List associates returns expected shape."""
    resp = await async_client.get(
        "/api/integration/associates", headers=api_headers(), params=base_params()
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "data" in data


async def test_associate_stats(async_client: AsyncClient):
    """Associate stats returns expected fields."""
    resp = await async_client.get(
        "/api/integration/associates/stats", headers=api_headers(), params=base_params()
    )
    assert resp.status_code == 200
    data = resp.json()
    for key in ("total", "inhouse", "freelance"):
        assert key in data


# ─── Dashboard ───────────────────────────────────────────────────────────────


async def test_dashboard_stats(async_client: AsyncClient):
    """Dashboard stats returns expected fields."""
    resp = await async_client.get(
        "/api/integration/dashboard/stats", headers=api_headers(), params=base_params()
    )
    assert resp.status_code == 200
    data = resp.json()
    for key in ("active_projects", "total_clients", "total_associates", "pending_tasks"):
        assert key in data


# ─── Finance ─────────────────────────────────────────────────────────────────


async def test_finance_overview(async_client: AsyncClient):
    """Finance overview returns expected fields."""
    resp = await async_client.get(
        "/api/integration/finance/overview", headers=api_headers(), params=base_params()
    )
    assert resp.status_code == 200
    data = resp.json()
    for key in ("income", "expenses", "net_profit", "outstanding_receivables"):
        assert key in data
