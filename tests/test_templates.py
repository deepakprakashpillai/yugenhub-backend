import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_create_and_list_template(async_client: AsyncClient, auth_headers: dict):
    """Create a manual template and verify it appears in the list."""
    payload = {
        "name": "Standard Wedding",
        "vertical": "knots",
        "description": "Default wedding template",
        "events": [],
        "metadata": {},
    }
    resp = await async_client.post("/api/templates", json=payload, headers=auth_headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Standard Wedding"
    template_id = data["_id"]

    # List
    list_resp = await async_client.get("/api/templates", headers=auth_headers)
    assert list_resp.status_code == 200
    assert any(t["_id"] == template_id for t in list_resp.json())


async def test_create_template_missing_fields(async_client: AsyncClient, auth_headers: dict):
    """Missing name/vertical returns 400."""
    resp = await async_client.post(
        "/api/templates",
        json={"description": "no name or vertical"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


async def test_update_template(async_client: AsyncClient, auth_headers: dict):
    # Create
    resp = await async_client.post(
        "/api/templates",
        json={"name": "Updatable", "vertical": "knots"},
        headers=auth_headers,
    )
    template_id = resp.json()["_id"]

    # Update
    patch_resp = await async_client.patch(
        f"/api/templates/{template_id}",
        json={"name": "Updated Template"},
        headers=auth_headers,
    )
    assert patch_resp.status_code == 200


async def test_delete_template(async_client: AsyncClient, auth_headers: dict):
    # Create
    resp = await async_client.post(
        "/api/templates",
        json={"name": "Delete Me Template", "vertical": "knots"},
        headers=auth_headers,
    )
    template_id = resp.json()["_id"]

    # Delete
    del_resp = await async_client.delete(f"/api/templates/{template_id}", headers=auth_headers)
    assert del_resp.status_code == 200

    # Verify gone
    del_resp2 = await async_client.delete(f"/api/templates/{template_id}", headers=auth_headers)
    assert del_resp2.status_code == 404
