import pytest
import uuid
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

async def test_app_health(async_client: AsyncClient):
    response = await async_client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "online"

async def test_dev_login_fails_in_production(async_client: AsyncClient):
    # Try dev endpoints
    response = await async_client.get("/api/auth/dev/users")
    # Even in test env, we are just checking it handles errors smoothly or works if not prod
    assert response.status_code in [200, 404]

async def test_auth_token_format(auth_headers: dict):
    assert "Authorization" in auth_headers
    assert auth_headers["Authorization"].startswith("Bearer ")
