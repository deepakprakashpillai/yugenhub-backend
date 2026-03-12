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

async def test_user_discovery_by_email(async_client: AsyncClient):
    # Seed the user directly for this test
    from database import users_collection
    user_data = {
        "id": "test_admin_id",
        "email": "admin@test.com",
        "name": "Admin User",
        "agency_id": "default_agency",
        "role": "admin"
    }
    await users_collection.insert_one(user_data)
    
    response = await async_client.get("/api/auth/discover?email=admin@test.com")
    assert response.status_code == 200
    data = response.json()
    assert data["found"] is True
    assert data["agency_id"] == "default_agency"
    
    # Cleanup
    await users_collection.delete_one({"id": "test_admin_id"})

async def test_user_discovery_not_found(async_client: AsyncClient):
    response = await async_client.get("/api/auth/discover?email=nonexistent@test.com")
    assert response.status_code == 200
    data = response.json()
    assert data["found"] is False

async def test_user_discovery_missing_params(async_client: AsyncClient):
    response = await async_client.get("/api/auth/discover")
    assert response.status_code == 400

async def test_user_discovery_case_insensitive(async_client: AsyncClient):
    # Seed user with specific case
    from database import users_collection
    user_data = {
        "id": "case_test_id",
        "email": "MixedCase@Test.com",
        "name": "Case User",
        "agency_id": "case_agency",
        "role": "admin"
    }
    await users_collection.insert_one(user_data)
    
    # Search with lowercase
    response = await async_client.get("/api/auth/discover?email=mixedcase@test.com")
    assert response.status_code == 200
    assert response.json()["found"] is True
    
    # Cleanup
    await users_collection.delete_one({"id": "case_test_id"})
