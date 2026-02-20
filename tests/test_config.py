import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_get_config(async_client: AsyncClient, auth_headers: dict):
    """Config endpoint returns agency config."""
    resp = await async_client.get("/api/config", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "agency_id" in data
    assert "verticals" in data


async def test_init_config(async_client: AsyncClient, auth_headers: dict):
    """Initialize/update config succeeds."""
    payload = {
        "agency_id": "test_agency",
        "verticals": [
            {
                "id": "wedding",
                "label": "Wedding Photography",
                "description": "Wedding shoots",
                "fields": [{"name": "venue", "label": "Venue", "type": "text"}],
            },
            {
                "id": "portrait",
                "label": "Portrait Photography",
                "description": "Portrait sessions",
                "fields": [],
            },
        ],
    }
    resp = await async_client.post("/api/config/init", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    assert "success" in resp.json()["message"].lower()

    # Verify the new vertical is persisted
    get_resp = await async_client.get("/api/config", headers=auth_headers)
    verticals = get_resp.json()["verticals"]
    assert any(v["id"] == "portrait" for v in verticals)
