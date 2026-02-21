import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

async def test_create_and_get_client(async_client: AsyncClient, auth_headers: dict):
    # Create client
    payload = {
        "name": "Acme Widgets",
        "phone": "+1234567890",
        "email": "contact@acme.com",
        "type": "Lead",
        "location": "New York"
    }
    resp = await async_client.post("/api/clients", json=payload, headers=auth_headers)
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data
    client_id = data["id"]
    
    # List clients
    resp = await async_client.get("/api/clients?search=Acme", headers=auth_headers)
    assert resp.status_code == 200
    list_data = resp.json()
    assert list_data["total"] >= 1
    assert any(c["_id"] == client_id for c in list_data["data"])
    
    # Get specific client
    resp = await async_client.get(f"/api/clients/{client_id}", headers=auth_headers)
    assert resp.status_code == 200
    client_data = resp.json()
    assert client_data["name"] == "Acme Widgets"
    assert client_data["email"] == "contact@acme.com"

async def test_update_client(async_client: AsyncClient, auth_headers: dict):
    # Setup
    payload = {"name": "Test Updating Client", "phone": "000000"}
    resp = await async_client.post("/api/clients", json=payload, headers=auth_headers)
    client_id = resp.json()["id"]

    # Act
    patch_resp = await async_client.patch(f"/api/clients/{client_id}", json={"name": "Updated Client", "type": "Active Client"}, headers=auth_headers)
    assert patch_resp.status_code == 200
    updated = patch_resp.json()
    assert updated["name"] == "Updated Client"
    assert updated["type"] == "Active Client"

async def test_create_client_unauthorized(async_client: AsyncClient):
    resp = await async_client.post("/api/clients", json={"name": "No Auth", "phone": "123"})
    assert resp.status_code == 401


async def test_client_stats(async_client: AsyncClient, auth_headers: dict):
    """Client stats endpoint returns valid data."""
    resp = await async_client.get("/api/clients/stats", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


async def test_delete_client(async_client: AsyncClient, auth_headers: dict):
    """Create then delete a client."""
    resp = await async_client.post(
        "/api/clients",
        json={"name": "Delete Me Client", "phone": "999"},
        headers=auth_headers,
    )
    client_id = resp.json()["id"]

    del_resp = await async_client.delete(f"/api/clients/{client_id}", headers=auth_headers)
    assert del_resp.status_code == 204
