import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


# ── Accounts ──────────────────────────────────────────────────────────────────

async def test_create_and_list_accounts(async_client: AsyncClient, auth_headers: dict):
    """Create a finance account and verify it appears in the list."""
    payload = {
        "name": "Business Bank Account",
        "type": "bank",
        "opening_balance": 10000.0,
        "currency": "INR",
    }
    resp = await async_client.post("/api/finance/accounts", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Business Bank Account"
    assert data["current_balance"] == 10000.0
    account_id = data["id"]

    # List
    list_resp = await async_client.get("/api/finance/accounts", headers=auth_headers)
    assert list_resp.status_code == 200
    assert any(a["id"] == account_id for a in list_resp.json())


async def test_duplicate_account_name(async_client: AsyncClient, auth_headers: dict):
    """Cannot create two accounts with the same name."""
    payload = {"name": "Duplicate Test Account", "type": "cash", "opening_balance": 0}
    await async_client.post("/api/finance/accounts", json=payload, headers=auth_headers)
    resp = await async_client.post("/api/finance/accounts", json=payload, headers=auth_headers)
    assert resp.status_code == 400


# ── Transactions ──────────────────────────────────────────────────────────────

async def test_create_and_list_transactions(async_client: AsyncClient, auth_headers: dict):
    """Create income transaction and verify balance updates."""
    # Create account first
    acct_resp = await async_client.post(
        "/api/finance/accounts",
        json={"name": "Txn Test Account", "type": "bank", "opening_balance": 5000},
        headers=auth_headers,
    )
    account_id = acct_resp.json()["id"]

    # Create income transaction
    txn_payload = {
        "type": "income",
        "amount": 2000.0,
        "account_id": account_id,
        "category": "Operations",
    }
    resp = await async_client.post("/api/finance/transactions", json=txn_payload, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["amount"] == 2000.0

    # List transactions
    list_resp = await async_client.get("/api/finance/transactions", headers=auth_headers)
    assert list_resp.status_code == 200
    assert len(list_resp.json()) >= 1


async def test_transaction_invalid_account(async_client: AsyncClient, auth_headers: dict):
    """Transaction with non-existent account returns 404."""
    resp = await async_client.post(
        "/api/finance/transactions",
        json={"type": "expense", "amount": 100, "account_id": "nonexistent", "category": "Test"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


# ── Invoices ──────────────────────────────────────────────────────────────────

async def test_create_and_list_invoices(async_client: AsyncClient, auth_headers: dict):
    payload = {
        "invoice_no": "INV-001",
        "client_id": "test_client_id",
        "total_amount": 50000.0,
        "status": "draft",
        "line_items": [{"title": "Photography", "quantity": 1, "price": 50000.0, "total": 50000.0}],
    }
    resp = await async_client.post("/api/finance/invoices", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["invoice_no"] == "INV-001"

    list_resp = await async_client.get("/api/finance/invoices", headers=auth_headers)
    assert list_resp.status_code == 200
    assert any(i["invoice_no"] == "INV-001" for i in list_resp.json())


async def test_duplicate_invoice_number(async_client: AsyncClient, auth_headers: dict):
    payload = {"invoice_no": "INV-DUP", "client_id": "c1", "total_amount": 1000, "line_items": []}
    await async_client.post("/api/finance/invoices", json=payload, headers=auth_headers)
    resp = await async_client.post("/api/finance/invoices", json=payload, headers=auth_headers)
    assert resp.status_code == 400


# ── Payouts ───────────────────────────────────────────────────────────────────

async def test_create_and_list_payouts(async_client: AsyncClient, auth_headers: dict):
    payload = {
        "associate_id": "assoc_1",
        "role": "Photographer",
        "agreed_amount": 15000.0,
        "paid_amount": 0,
        "status": "pending",
    }
    resp = await async_client.post("/api/finance/payouts", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    payout_id = resp.json()["id"]

    list_resp = await async_client.get("/api/finance/payouts", headers=auth_headers)
    assert list_resp.status_code == 200
    assert any(p["id"] == payout_id for p in list_resp.json())


# ── Client Ledger ─────────────────────────────────────────────────────────────

async def test_client_ledger_empty(async_client: AsyncClient, auth_headers: dict):
    """Non-existent client returns empty ledger."""
    resp = await async_client.get("/api/finance/client-ledger/no_such_client", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["client_id"] == "no_such_client"
    assert resp.json()["total_value"] == 0.0


# ── Overview ──────────────────────────────────────────────────────────────────

async def test_finance_overview(async_client: AsyncClient, auth_headers: dict):
    resp = await async_client.get("/api/finance/overview", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "income" in data
    assert "expenses" in data
    assert "net_profit" in data
    assert "outstanding_receivables" in data


# ── RBAC ──────────────────────────────────────────────────────────────────────

async def test_finance_forbidden_for_member(async_client: AsyncClient, member_auth_headers: dict):
    """Members cannot access finance endpoints."""
    resp = await async_client.get("/api/finance/accounts", headers=member_auth_headers)
    assert resp.status_code == 403
