import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_list_users(async_client: AsyncClient, auth_headers: dict):
    """Users list returns at least the seeded test user."""
    resp = await async_client.get("/api/users", headers=auth_headers)
    assert resp.status_code == 200
    users = resp.json()
    assert isinstance(users, list)
    assert any(u.get("id") == "test_owner_id" for u in users)


async def test_list_users_unauthorized(async_client: AsyncClient):
    """Unauthenticated request returns 401."""
    resp = await async_client.get("/api/users")
    assert resp.status_code == 401
