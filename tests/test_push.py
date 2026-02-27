import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

async def test_get_vapid_public_key(async_client: AsyncClient, auth_headers: dict):
    """Test that the VAPID public key is returned."""
    resp = await async_client.get("/api/push/vapid-public-key", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "public_key" in data
    assert len(data["public_key"]) > 0

async def test_subscribe_unsubscribe_push(async_client: AsyncClient, auth_headers: dict):
    """Test creating and removing a push subscription."""
    payload = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/test-endpoint-123",
        "keys": {
            "p256dh": "p256dh-key-test",
            "auth": "auth-key-test"
        }
    }
    
    # 1. Subscribe
    resp = await async_client.post("/api/push/subscribe", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["message"] == "Subscription saved"
    
    # 2. Subscribe again (Idempotent upsert)
    resp = await async_client.post("/api/push/subscribe", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    
    # 3. Unsubscribe
    resp = await async_client.request("DELETE", "/api/push/subscribe", json={"endpoint": payload["endpoint"]}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["message"] == "Subscription removed"

async def test_unsubscribe_missing_endpoint(async_client: AsyncClient, auth_headers: dict):
    """Test standard validation."""
    resp = await async_client.request("DELETE", "/api/push/subscribe", json={}, headers=auth_headers)
    assert resp.status_code == 400
