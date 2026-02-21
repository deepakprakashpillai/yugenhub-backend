import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

async def test_create_and_get_associate(async_client: AsyncClient, auth_headers: dict):
    payload = {
        "name": "John Photographer",
        "phone_number": "555-0100",
        "email_id": "john.photo@example.com",
        "primary_role": "Photographer",
        "employment_type": "Freelance"
    }
    resp = await async_client.post("/api/associates", json=payload, headers=auth_headers)
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data
    associate_id = data["id"]
    
    # Get by ID
    resp = await async_client.get(f"/api/associates/{associate_id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "John Photographer"

async def test_inhouse_associate_requires_email(async_client: AsyncClient, auth_headers: dict):
    payload = {
        "name": "Jane Editor",
        "phone_number": "555-0200",
        "primary_role": "Editor",
        "employment_type": "In-house"
        # email_id conspicuously missing
    }
    resp = await async_client.post("/api/associates", json=payload, headers=auth_headers)
    assert resp.status_code == 400
    assert "email" in resp.json()["detail"].lower()

async def test_list_and_delete_associate(async_client: AsyncClient, auth_headers: dict):
    # Create
    payload = {"name": "Delete Me", "phone_number": "000", "primary_role": "Assistant"}
    resp = await async_client.post("/api/associates", json=payload, headers=auth_headers)
    associate_id = resp.json()["id"]

    # Delete
    del_resp = await async_client.delete(f"/api/associates/{associate_id}", headers=auth_headers)
    assert del_resp.status_code in [200, 204]
    
    # Verify it's gone
    get_resp = await async_client.get(f"/api/associates/{associate_id}", headers=auth_headers)
    assert get_resp.status_code == 404


async def test_associate_stats(async_client: AsyncClient, auth_headers: dict):
    """Associate stats endpoint returns valid data."""
    resp = await async_client.get("/api/associates/stats", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


async def test_update_associate(async_client: AsyncClient, auth_headers: dict):
    """Patch an associate's details."""
    # Create
    resp = await async_client.post(
        "/api/associates",
        json={"name": "Patchable Associate", "phone_number": "111", "primary_role": "Editor"},
        headers=auth_headers,
    )
    associate_id = resp.json()["id"]

    # Patch
    patch_resp = await async_client.patch(
        f"/api/associates/{associate_id}",
        json={"name": "Patched Associate", "primary_role": "Lead Editor"},
        headers=auth_headers,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["name"] == "Patched Associate"
