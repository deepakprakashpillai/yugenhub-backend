import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_create_task(async_client: AsyncClient, auth_headers: dict):
    """Owner/Admin can create a task."""
    payload = {
        "title": "Test Task Alpha",
        "type": "internal",
        "category": "general",
        "priority": "high",
    }
    resp = await async_client.post("/api/tasks", json=payload, headers=auth_headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Test Task Alpha"
    assert data["priority"] == "high"
    assert data["status"] == "todo"


async def test_member_cannot_create_task(async_client: AsyncClient, member_auth_headers: dict):
    """Members are forbidden from creating tasks."""
    payload = {"title": "Should Fail", "type": "internal"}
    resp = await async_client.post("/api/tasks", json=payload, headers=member_auth_headers)
    assert resp.status_code == 403


async def test_list_tasks_grouped(async_client: AsyncClient, auth_headers: dict):
    """List grouped tasks returns Kanban structure."""
    resp = await async_client.get("/api/tasks/grouped", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "groups" in data
    assert "summary" in data
    assert "todo" in data["groups"]


async def test_update_task(async_client: AsyncClient, auth_headers: dict):
    """Create then update a task."""
    # Create
    create_resp = await async_client.post(
        "/api/tasks",
        json={"title": "Update Me", "type": "internal", "priority": "low"},
        headers=auth_headers,
    )
    task_id = create_resp.json()["id"]

    # Update status
    patch_resp = await async_client.patch(
        f"/api/tasks/{task_id}",
        json={"status": "in_progress"},
        headers=auth_headers,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["status"] == "in_progress"


async def test_blocked_status_requires_comment(async_client: AsyncClient, auth_headers: dict):
    """Blocking a task without a comment should return 400."""
    create_resp = await async_client.post(
        "/api/tasks",
        json={"title": "Block Me", "type": "internal"},
        headers=auth_headers,
    )
    task_id = create_resp.json()["id"]

    patch_resp = await async_client.patch(
        f"/api/tasks/{task_id}",
        json={"status": "blocked"},
        headers=auth_headers,
    )
    assert patch_resp.status_code == 400
    assert "comment" in patch_resp.json()["detail"].lower()


async def test_delete_task(async_client: AsyncClient, auth_headers: dict):
    """Owner can delete a task."""
    create_resp = await async_client.post(
        "/api/tasks",
        json={"title": "Delete Me", "type": "internal"},
        headers=auth_headers,
    )
    task_id = create_resp.json()["id"]

    del_resp = await async_client.delete(f"/api/tasks/{task_id}", headers=auth_headers)
    assert del_resp.status_code == 200
    assert "deleted" in del_resp.json()["message"].lower()


async def test_member_cannot_delete_task(async_client: AsyncClient, auth_headers: dict, member_auth_headers: dict):
    """Members cannot delete tasks."""
    create_resp = await async_client.post(
        "/api/tasks",
        json={"title": "No Delete For Member", "type": "internal"},
        headers=auth_headers,
    )
    task_id = create_resp.json()["id"]

    del_resp = await async_client.delete(f"/api/tasks/{task_id}", headers=member_auth_headers)
    assert del_resp.status_code == 403


async def test_task_history(async_client: AsyncClient, auth_headers: dict):
    """History endpoint returns at least the creation entry."""
    create_resp = await async_client.post(
        "/api/tasks",
        json={"title": "History Check", "type": "internal"},
        headers=auth_headers,
    )
    task_id = create_resp.json()["id"]

    hist_resp = await async_client.get(f"/api/tasks/{task_id}/history", headers=auth_headers)
    assert hist_resp.status_code == 200
    assert isinstance(hist_resp.json(), list)
    assert len(hist_resp.json()) >= 1


async def test_list_tasks_flat(async_client: AsyncClient, auth_headers: dict):
    """Flat task list endpoint returns paginated data."""
    resp = await async_client.get("/api/tasks", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data
    assert "total" in data
