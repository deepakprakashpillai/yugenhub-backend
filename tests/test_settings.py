import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


# ── Organisation ──────────────────────────────────────────────────────────────

async def test_get_org(async_client: AsyncClient, auth_headers: dict):
    resp = await async_client.get("/api/settings/org", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "org_name" in data
    assert "agency_id" in data


async def test_update_org_owner_only(async_client: AsyncClient, auth_headers: dict, member_auth_headers: dict):
    """Only owner can update org."""
    # Owner updates
    resp = await async_client.patch(
        "/api/settings/org",
        json={"org_name": "Updated Agency"},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    # Member cannot update
    resp = await async_client.patch(
        "/api/settings/org",
        json={"org_name": "Hacked"},
        headers=member_auth_headers,
    )
    assert resp.status_code == 403


# ── Team ──────────────────────────────────────────────────────────────────────

async def test_get_team(async_client: AsyncClient, auth_headers: dict):
    resp = await async_client.get("/api/settings/team", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) >= 1  # At least the test_user


async def test_invite_user(async_client: AsyncClient, auth_headers: dict):
    resp = await async_client.post(
        "/api/settings/team/invite",
        json={"email": "newbie@test.com", "role": "member"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert "user_id" in resp.json()


async def test_invite_duplicate_user(async_client: AsyncClient, auth_headers: dict):
    """Inviting an already-existing email should return 409."""
    resp = await async_client.post(
        "/api/settings/team/invite",
        json={"email": "owner@test.com", "role": "member"},
        headers=auth_headers,
    )
    assert resp.status_code == 409


async def test_change_user_role(async_client: AsyncClient, auth_headers: dict, test_member_user: dict):
    resp = await async_client.patch(
        f"/api/settings/team/{test_member_user['id']}/role",
        json={"role": "admin"},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    # Reset back to member
    await async_client.patch(
        f"/api/settings/team/{test_member_user['id']}/role",
        json={"role": "member"},
        headers=auth_headers,
    )


# ── Workflow ──────────────────────────────────────────────────────────────────

async def test_get_workflow(async_client: AsyncClient, auth_headers: dict):
    resp = await async_client.get("/api/settings/workflow", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "status_options" in data
    assert "lead_sources" in data
    assert "deliverable_types" in data


async def test_update_workflow_member_forbidden(async_client: AsyncClient, member_auth_headers: dict):
    resp = await async_client.patch(
        "/api/settings/workflow",
        json={"lead_sources": [{"id": "ref", "label": "Referral"}]},
        headers=member_auth_headers,
    )
    assert resp.status_code == 403


async def test_status_usage(async_client: AsyncClient, auth_headers: dict):
    resp = await async_client.get("/api/settings/workflow/status/enquiry/usage", headers=auth_headers)
    assert resp.status_code == 200
    assert "count" in resp.json()


# ── Verticals ─────────────────────────────────────────────────────────────────

async def test_get_verticals(async_client: AsyncClient, auth_headers: dict):
    resp = await async_client.get("/api/settings/verticals", headers=auth_headers)
    assert resp.status_code == 200
    assert "verticals" in resp.json()


# ── Finance Categories ───────────────────────────────────────────────────────

async def test_get_finance_categories(async_client: AsyncClient, auth_headers: dict):
    resp = await async_client.get("/api/settings/finance/categories", headers=auth_headers)
    assert resp.status_code == 200
    assert "categories" in resp.json()


# ── Notification Preferences ─────────────────────────────────────────────────

async def test_get_notification_prefs(async_client: AsyncClient, auth_headers: dict):
    resp = await async_client.get("/api/settings/notifications", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "task_assigned" in data


async def test_update_notification_prefs(async_client: AsyncClient, auth_headers: dict):
    resp = await async_client.patch(
        "/api/settings/notifications",
        json={"task_assigned": False},
        headers=auth_headers,
    )
    assert resp.status_code == 200


# ── Account ───────────────────────────────────────────────────────────────────

async def test_get_account(async_client: AsyncClient, auth_headers: dict):
    resp = await async_client.get("/api/settings/account", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "owner@test.com"
    assert data["role"] == "owner"


# ── Update User Details ───────────────────────────────────────────────────────

async def test_update_user_details(async_client: AsyncClient, auth_headers: dict, test_member_user: dict):
    """Owner can update a member's details."""
    resp = await async_client.patch(
        f"/api/settings/team/{test_member_user['id']}",
        json={"name": "Renamed Member", "phone": "+9999999"},
        headers=auth_headers,
    )
    assert resp.status_code == 200


async def test_member_cannot_update_other_users(async_client: AsyncClient, member_auth_headers: dict, test_user: dict):
    """Member cannot update owner's details."""
    resp = await async_client.patch(
        f"/api/settings/team/{test_user['id']}",
        json={"name": "Hacked Owner"},
        headers=member_auth_headers,
    )
    assert resp.status_code == 403


# ── Remove User ───────────────────────────────────────────────────────────────

async def test_remove_team_member(async_client: AsyncClient, auth_headers: dict):
    """Owner can invite and then remove a user."""
    # Create a disposable user
    invite_resp = await async_client.post(
        "/api/settings/team/invite",
        json={"email": "disposable@test.com", "role": "member"},
        headers=auth_headers,
    )
    user_id = invite_resp.json()["user_id"]

    # Remove
    del_resp = await async_client.delete(f"/api/settings/team/{user_id}", headers=auth_headers)
    assert del_resp.status_code == 200


async def test_cannot_remove_self(async_client: AsyncClient, auth_headers: dict, test_user: dict):
    """Cannot remove yourself."""
    resp = await async_client.delete(f"/api/settings/team/{test_user['id']}", headers=auth_headers)
    assert resp.status_code == 400


# ── Update Verticals ──────────────────────────────────────────────────────────

async def test_update_verticals(async_client: AsyncClient, auth_headers: dict):
    """Owner can update verticals."""
    resp = await async_client.patch(
        "/api/settings/verticals",
        json={"verticals": [
            {"id": "wedding", "label": "Wedding Photography"},
            {"id": "corporate", "label": "Corporate Events"},
        ]},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    # Verify
    get_resp = await async_client.get("/api/settings/verticals", headers=auth_headers)
    assert any(v["id"] == "corporate" for v in get_resp.json()["verticals"])


# ── Reset Config ──────────────────────────────────────────────────────────────

async def test_reset_config(async_client: AsyncClient, auth_headers: dict):
    """Owner can reset agency config to defaults."""
    resp = await async_client.post("/api/settings/reset-config", headers=auth_headers)
    assert resp.status_code == 200
    assert "reset" in resp.json()["message"].lower() or "defaults" in resp.json()["message"].lower()


# ── Account Self-Edit ────────────────────────────────────────────────────────

async def test_update_own_account(async_client: AsyncClient, auth_headers: dict):
    """User can update their own name and phone."""
    resp = await async_client.patch(
        "/api/settings/account",
        json={"name": "Updated Owner", "phone": "+1111111"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["message"] == "Profile updated"

    # Verify the update
    get_resp = await async_client.get("/api/settings/account", headers=auth_headers)
    assert get_resp.json()["name"] == "Updated Owner"
    assert get_resp.json()["phone"] == "+1111111"

    # Reset name for other tests
    await async_client.patch(
        "/api/settings/account",
        json={"name": "Test Owner"},
        headers=auth_headers,
    )


async def test_update_account_empty_name_rejected(async_client: AsyncClient, auth_headers: dict):
    """Updating with an empty name should be rejected."""
    resp = await async_client.patch(
        "/api/settings/account",
        json={"name": "  "},
        headers=auth_headers,
    )
    assert resp.status_code == 400


# ── Invite Creates Associate ─────────────────────────────────────────────────

async def test_invite_creates_associate(async_client: AsyncClient, auth_headers: dict):
    """Inviting a user should auto-create an In-house associate."""
    test_email = "associate_test@example.com"

    # Invite user
    resp = await async_client.post(
        "/api/settings/team/invite",
        json={"email": test_email, "role": "member", "associate_role": "Photographer"},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    # Check that an associate was created
    assoc_resp = await async_client.get(
        "/api/associates?limit=50000",
        headers=auth_headers,
    )
    assert assoc_resp.status_code == 200
    associates = assoc_resp.json().get("data", [])
    match = [a for a in associates if a.get("email_id") == test_email]
    assert len(match) == 1
    assert match[0]["employment_type"] == "In-house"
    assert match[0]["primary_role"] == "Photographer"


# ── Remove User With Associate Deactivation ──────────────────────────────────

async def test_remove_user_deactivates_associate(async_client: AsyncClient, auth_headers: dict):
    """Removing a user with deactivate_associate=true should deactivate the linked associate."""
    test_email = "deactivate_test@example.com"

    # Invite
    invite_resp = await async_client.post(
        "/api/settings/team/invite",
        json={"email": test_email, "role": "member", "associate_role": "Editor"},
        headers=auth_headers,
    )
    user_id = invite_resp.json()["user_id"]

    # Remove with deactivate
    del_resp = await async_client.delete(
        f"/api/settings/team/{user_id}?deactivate_associate=true",
        headers=auth_headers,
    )
    assert del_resp.status_code == 200

    # Check associate is now inactive
    assoc_resp = await async_client.get(
        "/api/associates?limit=50000",
        headers=auth_headers,
    )
    associates = assoc_resp.json().get("data", [])
    match = [a for a in associates if a.get("email_id") == test_email]
    assert len(match) == 1
    assert match[0]["is_active"] is False

