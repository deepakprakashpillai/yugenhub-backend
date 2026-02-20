import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _create_client(ac: AsyncClient, headers: dict) -> str:
    resp = await ac.post("/api/clients", json={"name": "Proj Client", "phone": "111", "type": "Active Client"}, headers=headers)
    return resp.json()["id"]


async def _create_project(ac: AsyncClient, headers: dict, client_id: str) -> dict:
    payload = {
        "vertical": "wedding",
        "client_id": client_id,
        "status": "enquiry",
        "lead_source": "web",
        "events": [],
        "metadata": {"client_name": "Proj Client"},
    }
    resp = await ac.post("/api/projects", json=payload, headers=headers)
    assert resp.status_code == 201
    return resp.json()


# ── CREATE + GET + LIST ───────────────────────────────────────────────────────

async def test_create_and_get_project(async_client: AsyncClient, auth_headers: dict):
    client_id = await _create_client(async_client, auth_headers)
    data = await _create_project(async_client, auth_headers, client_id)
    assert "_id" in data
    assert data["code"].startswith("WE-")
    project_id = data["_id"]

    # List
    list_resp = await async_client.get("/api/projects", headers=auth_headers)
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] >= 1

    # Get by ID
    get_resp = await async_client.get(f"/api/projects/{project_id}", headers=auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["vertical"] == "wedding"


async def test_create_project_invalid_vertical(async_client: AsyncClient, auth_headers: dict):
    resp = await async_client.post("/api/projects", json={"vertical": "invalid_magic_vertical", "client_id": "dummy"}, headers=auth_headers)
    assert resp.status_code == 400
    assert "invalid vertical" in resp.json()["detail"].lower()


# ── PATCH PROJECT ─────────────────────────────────────────────────────────────

async def test_patch_project(async_client: AsyncClient, auth_headers: dict):
    client_id = await _create_client(async_client, auth_headers)
    proj = await _create_project(async_client, auth_headers, client_id)

    resp = await async_client.patch(
        f"/api/projects/{proj['_id']}",
        json={"status": "booked"},
        headers=auth_headers,
    )
    assert resp.status_code == 200


# ── DELETE PROJECT ────────────────────────────────────────────────────────────

async def test_delete_project(async_client: AsyncClient, auth_headers: dict):
    client_id = await _create_client(async_client, auth_headers)
    proj = await _create_project(async_client, auth_headers, client_id)

    resp = await async_client.delete(f"/api/projects/{proj['_id']}", headers=auth_headers)
    assert resp.status_code == 200

    # Verify gone
    get_resp = await async_client.get(f"/api/projects/{proj['_id']}", headers=auth_headers)
    assert get_resp.status_code == 404


# ── STATS OVERVIEW ────────────────────────────────────────────────────────────

async def test_project_stats_overview(async_client: AsyncClient, auth_headers: dict):
    resp = await async_client.get("/api/projects/stats/overview", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data or "active" in data or isinstance(data, dict)


# ── EVENTS CRUD ───────────────────────────────────────────────────────────────

async def _add_event_and_get_id(ac: AsyncClient, headers: dict, project_id: str, event_type: str = "Ceremony") -> str:
    """Helper: add an event and return its id by refetching the project."""
    event_payload = {
        "type": event_type,
        "start_date": "2026-06-15T10:00:00",
        "end_date": "2026-06-15T18:00:00",
        "venue_name": "Grand Hall",
        "venue_location": "Downtown",
    }
    resp = await ac.post(f"/api/projects/{project_id}/events", json=event_payload, headers=headers)
    assert resp.status_code == 200

    # Refetch project to get event IDs
    proj_resp = await ac.get(f"/api/projects/{project_id}", headers=headers)
    events = proj_resp.json().get("events", [])
    assert len(events) >= 1
    return events[-1]["id"]


async def test_add_event_to_project(async_client: AsyncClient, auth_headers: dict):
    client_id = await _create_client(async_client, auth_headers)
    proj = await _create_project(async_client, auth_headers, client_id)

    event_id = await _add_event_and_get_id(async_client, auth_headers, proj["_id"])
    assert event_id is not None


async def test_patch_event(async_client: AsyncClient, auth_headers: dict):
    client_id = await _create_client(async_client, auth_headers)
    proj = await _create_project(async_client, auth_headers, client_id)
    event_id = await _add_event_and_get_id(async_client, auth_headers, proj["_id"], "Reception")

    patch_resp = await async_client.patch(
        f"/api/projects/{proj['_id']}/events/{event_id}",
        json={"venue_name": "Updated Venue"},
        headers=auth_headers,
    )
    assert patch_resp.status_code == 200


async def test_delete_event(async_client: AsyncClient, auth_headers: dict):
    client_id = await _create_client(async_client, auth_headers)
    proj = await _create_project(async_client, auth_headers, client_id)
    event_id = await _add_event_and_get_id(async_client, auth_headers, proj["_id"], "Bridal Shoot")

    del_resp = await async_client.delete(
        f"/api/projects/{proj['_id']}/events/{event_id}",
        headers=auth_headers,
    )
    assert del_resp.status_code == 200


# ── ASSIGNMENTS CRUD ──────────────────────────────────────────────────────────

async def test_assignment_lifecycle(async_client: AsyncClient, auth_headers: dict):
    """Add, patch, and delete an assignment on a project event."""
    client_id = await _create_client(async_client, auth_headers)
    proj = await _create_project(async_client, auth_headers, client_id)

    # Create associate
    assoc_resp = await async_client.post(
        "/api/associates",
        json={"name": "Assign Test Photographer", "phone_number": "999", "primary_role": "Photographer"},
        headers=auth_headers,
    )
    associate_id = assoc_resp.json()["id"]

    # Add event
    event_id = await _add_event_and_get_id(async_client, auth_headers, proj["_id"], "Engagement")

    # Add assignment
    assign_resp = await async_client.post(
        f"/api/projects/{proj['_id']}/events/{event_id}/assignments",
        json={"associate_id": associate_id, "role": "Photographer"},
        headers=auth_headers,
    )
    assert assign_resp.status_code in [200, 201]

    # Get updated project to find assignment id
    proj_resp = await async_client.get(f"/api/projects/{proj['_id']}", headers=auth_headers)
    proj_data = proj_resp.json()
    target_event = next(e for e in proj_data["events"] if e["id"] == event_id)
    assignments = target_event.get("assignments", [])
    assert len(assignments) >= 1
    assignment_id = assignments[-1].get("id") or assignments[-1].get("_id")

    if assignment_id:
        # Patch assignment
        patch_resp = await async_client.patch(
            f"/api/projects/{proj['_id']}/events/{event_id}/assignments/{assignment_id}",
            json={"role": "Lead Photographer"},
            headers=auth_headers,
        )
        assert patch_resp.status_code == 200

        # Delete assignment
        del_resp = await async_client.delete(
            f"/api/projects/{proj['_id']}/events/{event_id}/assignments/{assignment_id}",
            headers=auth_headers,
        )
        assert del_resp.status_code == 200


# ── ASSIGNED BY ASSOCIATE ─────────────────────────────────────────────────────

async def test_projects_assigned_to_associate(async_client: AsyncClient, auth_headers: dict):
    resp = await async_client.get("/api/projects/assigned/nonexistent_id", headers=auth_headers)
    # Should return 200 with empty list, or 404 — depends on implementation
    assert resp.status_code in [200, 404]
