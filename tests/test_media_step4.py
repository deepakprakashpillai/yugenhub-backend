"""
Tests for Media Feature — Step 4
Covers: media_access in team invite, GET /team, PATCH access,
        and media_access returned in auth responses.
"""
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


# ─── GET /settings/team includes media_access for owner ──────────────────────

async def test_get_team_includes_media_access_for_owner(
    async_client: AsyncClient, auth_headers, test_user
):
    resp = await async_client.get("/api/settings/team", headers=auth_headers)
    assert resp.status_code == 200
    members = resp.json()
    owner = next(m for m in members if m["id"] == test_user["id"])
    assert "media_access" in owner


async def test_get_team_hides_media_access_from_member(
    async_client: AsyncClient, member_auth_headers
):
    resp = await async_client.get("/api/settings/team", headers=member_auth_headers)
    assert resp.status_code == 200
    for member in resp.json():
        assert "media_access" not in member


# ─── POST /settings/team/invite accepts media_access ─────────────────────────

async def test_invite_with_media_access_true(
    async_client: AsyncClient, auth_headers, test_db_session
):
    resp = await async_client.post(
        "/api/settings/team/invite",
        json={"email": "mediauser@test.com", "role": "member", "media_access": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    user_id = resp.json()["user_id"]

    pass  # user is inserted; we verify via GET /team

    # Re-fetch team and check
    team = (await async_client.get("/api/settings/team", headers=auth_headers)).json()
    invited = next((m for m in team if m.get("id") == user_id), None)
    assert invited is not None
    assert invited["media_access"] is True


async def test_invite_with_media_access_false_by_default(
    async_client: AsyncClient, auth_headers
):
    resp = await async_client.post(
        "/api/settings/team/invite",
        json={"email": "nomedia@test.com", "role": "member"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    user_id = resp.json()["user_id"]

    team = (await async_client.get("/api/settings/team", headers=auth_headers)).json()
    invited = next((m for m in team if m.get("id") == user_id), None)
    assert invited is not None
    assert invited["media_access"] is False


async def test_invite_media_access_stripped_for_non_owner(
    async_client: AsyncClient, test_db_session
):
    """Admin cannot grant media_access — it should be silently stripped."""
    # Create an admin with can_manage_team
    admin_data = {
        "id": "admin_with_team_mgmt",
        "email": "admin_mgr@test.com",
        "name": "Admin Mgr",
        "role": "admin",
        "agency_id": "test_agency",
        "can_manage_team": True,
        "media_access": False,
    }
    test_db_session.users.update_one({"id": admin_data["id"]}, {"$set": admin_data}, upsert=True)

    from routes.deps import create_access_token
    from datetime import timedelta
    admin_token = create_access_token(
        data={"sub": admin_data["id"], "agency_id": admin_data["agency_id"]},
        expires_delta=timedelta(minutes=60)
    )
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    from httpx import AsyncClient as AC, ASGITransport
    from main import app
    async with AC(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        resp = await client.post(
            "/api/settings/team/invite",
            json={"email": "sneaky@test.com", "role": "member", "media_access": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        user_id = resp.json()["user_id"]

        # Owner checks what was actually saved
        saved = test_db_session.users.find_one({"id": user_id})
        assert saved.get("media_access", False) is False


# ─── PATCH /settings/team/{user_id}/access handles media_access ──────────────

async def test_update_access_grant_media(
    async_client: AsyncClient, auth_headers, test_db_session
):
    # Create a member to update
    member = {
        "id": "member_for_media_grant",
        "email": "grantme@test.com",
        "name": "Grant Me",
        "role": "member",
        "agency_id": "test_agency",
        "media_access": False,
    }
    test_db_session.users.update_one({"id": member["id"]}, {"$set": member}, upsert=True)

    resp = await async_client.patch(
        f"/api/settings/team/{member['id']}/access",
        json={"media_access": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert "media_access" in resp.json().get("updated", [])

    saved = test_db_session.users.find_one({"id": member["id"]})
    assert saved["media_access"] is True


async def test_update_access_revoke_media(
    async_client: AsyncClient, auth_headers, test_db_session
):
    member = {
        "id": "member_with_media",
        "email": "revokeme@test.com",
        "name": "Revoke Me",
        "role": "member",
        "agency_id": "test_agency",
        "media_access": True,
    }
    test_db_session.users.update_one({"id": member["id"]}, {"$set": member}, upsert=True)

    resp = await async_client.patch(
        f"/api/settings/team/{member['id']}/access",
        json={"media_access": False},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    saved = test_db_session.users.find_one({"id": member["id"]})
    assert saved["media_access"] is False


async def test_update_access_invalid_media_access_type(
    async_client: AsyncClient, auth_headers, test_db_session
):
    member = {
        "id": "member_bad_type",
        "email": "badtype@test.com",
        "name": "Bad Type",
        "role": "member",
        "agency_id": "test_agency",
    }
    test_db_session.users.update_one({"id": member["id"]}, {"$set": member}, upsert=True)

    resp = await async_client.patch(
        f"/api/settings/team/{member['id']}/access",
        json={"media_access": "yes"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


async def test_update_access_owner_only(
    async_client: AsyncClient, member_auth_headers, test_db_session
):
    """Non-owner cannot call the access update endpoint."""
    member = {
        "id": "another_member",
        "email": "another@test.com",
        "name": "Another",
        "role": "member",
        "agency_id": "test_agency",
    }
    test_db_session.users.update_one({"id": member["id"]}, {"$set": member}, upsert=True)

    resp = await async_client.patch(
        f"/api/settings/team/{member['id']}/access",
        json={"media_access": True},
        headers=member_auth_headers,
    )
    assert resp.status_code == 403


# ─── Auth response includes media_access ─────────────────────────────────────

async def test_dev_login_returns_media_access(
    async_client: AsyncClient, test_user
):
    resp = await async_client.post(f"/api/auth/dev/login/{test_user['id']}")
    assert resp.status_code == 200
    user_data = resp.json()["user"]
    assert "media_access" in user_data


# ─── require_media_access with real DB user ───────────────────────────────────

async def test_member_without_media_access_blocked(
    async_client: AsyncClient, member_auth_headers
):
    """Member with media_access=False cannot access media routes."""
    resp = await async_client.get("/api/media/folders", headers=member_auth_headers)
    assert resp.status_code == 403


async def test_member_with_media_access_allowed(
    async_client: AsyncClient, test_db_session
):
    """Member with media_access=True can access media routes."""
    member = {
        "id": "media_member",
        "email": "mediamember@test.com",
        "name": "Media Member",
        "role": "member",
        "agency_id": "test_agency",
        "media_access": True,
    }
    test_db_session.users.update_one({"id": member["id"]}, {"$set": member}, upsert=True)

    from routes.deps import create_access_token
    from datetime import timedelta
    token = create_access_token(
        data={"sub": member["id"], "agency_id": member["agency_id"]},
        expires_delta=timedelta(minutes=60)
    )
    headers = {"Authorization": f"Bearer {token}"}

    from httpx import AsyncClient as AC, ASGITransport
    from main import app
    async with AC(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        resp = await client.get("/api/media/folders", headers=headers)
    assert resp.status_code == 200
