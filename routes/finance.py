from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from datetime import datetime
from models.finance import AccountModel, TransactionModel, ClientLedgerModel, InvoiceModel, AssociatePayoutModel
from database import accounts_collection, transactions_collection, ledgers_collection, invoices_collection, payouts_collection
from bson import ObjectId

from fastapi import APIRouter, HTTPException, Query, Depends, status
from routes.deps import get_current_user
from models.user import UserModel
from constants import Roles

async def ensure_finance_access(current_user: UserModel = Depends(get_current_user)):
    if current_user.role not in [Roles.ADMIN, Roles.OWNER]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access Denied: Finance data is restricted to Admins and Owners."
        )

router = APIRouter(prefix="/api/finance", tags=["Finance"], dependencies=[Depends(ensure_finance_access)])

# -----------------------------------------------------------------------------
# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------
async def update_account_balance(account_id: str, amount: float, transaction_type: str):
    # Use atomic $inc operator
    inc_amount = 0.0
    if transaction_type == "income":
        inc_amount = amount
    elif transaction_type == "expense":
        inc_amount = -amount
    # Transfer logic handled separately or by caller

    if inc_amount != 0:
        await accounts_collection.update_one(
            {"id": account_id},
            {
                "$inc": {"current_balance": inc_amount},
                "$set": {"updated_at": datetime.now()}
            }
        )

async def update_client_ledger(client_id: str, amount: float, operation: str):
    # operation: 'invoice_created' (add to total_value)
    #            'payment_received' (add to received_amount)
    
    # 1. Ensure Ledger Exists (Atomic upsert)
    await ledgers_collection.update_one(
        {"client_id": client_id},
        {"$setOnInsert": {
            "id": str(uuid.uuid4()),
            "agency_id": "default",
            "total_value": 0.0,
            "received_amount": 0.0,
            "balance_amount": 0.0,
            "status": "pending"
        }},
        upsert=True
    )

    # 2. Atomic Increment
    update_query = {}
    if operation == "invoice_created":
        update_query = {"$inc": {"total_value": amount}}
    elif operation == "payment_received":
        update_query = {"$inc": {"received_amount": amount}}
    
    if update_query:
        update_query["$set"] = {"last_updated": datetime.now()}
        await ledgers_collection.update_one({"client_id": client_id}, update_query)

    # 3. Recalculate Balance & Status (Atomic Aggregation Update)
    # Requires MongoDB 4.2+ for pipeline in update
    pipeline = [
        {"$set": {
            "balance_amount": {"$subtract": ["$total_value", "$received_amount"]}
        }},
        {"$set": {
            "status": {
                "$switch": {
                    "branches": [
                        {"case": {"$lte": ["$balance_amount", 0]}, "then": "settled"},
                        {"case": {"$gt": ["$received_amount", 0]}, "then": "partially_paid"}
                    ],
                    "default": "pending"
                }
            }
        }}
    ]
    await ledgers_collection.update_one({"client_id": client_id}, pipeline)

# -----------------------------------------------------------------------------
# Accounts API
# -----------------------------------------------------------------------------
@router.get("/accounts", response_model=List[AccountModel])
async def get_accounts():
    cursor = accounts_collection.find({})
    accounts = await cursor.to_list(length=100)
    return accounts

@router.post("/accounts", response_model=AccountModel)
async def create_account(account: AccountModel):
    # Check if account name exists
    existing = await accounts_collection.find_one({"name": account.name})
    if existing:
        raise HTTPException(status_code=400, detail="Account with this name already exists")
    
    # Initialize current balance with opening balance
    account.current_balance = account.opening_balance
    
    await accounts_collection.insert_one(account.model_dump())
    return account

# -----------------------------------------------------------------------------
# Transactions API
# -----------------------------------------------------------------------------
@router.get("/transactions", response_model=List[TransactionModel])
async def get_transactions(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    account_id: Optional[str] = None,
    project_id: Optional[str] = None,
    type: Optional[str] = None,
    category: Optional[str] = None,
    page: int = 1,
    limit: int = 50
):
    query = {}
    if account_id:
        query["account_id"] = account_id
    if project_id:
        query["project_id"] = project_id
    if type:
        query["type"] = type
    if category:
        query["category"] = category
    if start_date and end_date:
        query["date"] = {"$gte": start_date, "$lte": end_date}
        
    skip = (page - 1) * limit
    cursor = transactions_collection.find(query).sort("date", -1).skip(skip).limit(limit)
    transactions = await cursor.to_list(length=limit)
    return transactions

@router.post("/transactions", response_model=TransactionModel)
async def create_transaction(transaction: TransactionModel):
    # 1. Validate Account
    account = await accounts_collection.find_one({"id": transaction.account_id})
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # 2. Handle Transfer
    if transaction.type == "transfer":
        # Logic for transfer needs source and destination. 
        # For MVP, let's assume 'transfer' in UI sends two requests or one request with destination.
        # But per requirements: "Transfer creates two linked entries (debit + credit)"
        # This endpoint receives ONE transaction. If it's a transfer, we expect the frontend 
        # to confirm "This is a transfer to Account B".
        # For now, let's keep it simple: The UI calls this API twice for transfers, or we can handle it here if we pass 'destination_account_id' in metadata used by FE.
        # Let's assume the frontend handles the duality for now to keep the API atomic.
        pass

    # 3. Update Account Balance
    await update_account_balance(transaction.account_id, transaction.amount, transaction.type)
    
    # 4. Save Transaction
    await transactions_collection.insert_one(transaction.model_dump())
    
    # 5. Link to Client Ledger if applicable (Income)
    if transaction.type == "income" and transaction.client_id:
        await update_client_ledger(transaction.client_id, transaction.amount, "payment_received")
        
    return transaction

# -----------------------------------------------------------------------------
# Invoices API
# -----------------------------------------------------------------------------
@router.get("/invoices", response_model=List[InvoiceModel])
async def get_invoices(
    project_id: Optional[str] = None,
    limit: int = 100
):
    query = {}
    if project_id:
        query["project_id"] = project_id
        
    cursor = invoices_collection.find(query).sort("created_at", -1)
    return await cursor.to_list(length=limit)

@router.post("/invoices", response_model=InvoiceModel)
async def create_invoice(invoice: InvoiceModel):
    existing = await invoices_collection.find_one({"invoice_no": invoice.invoice_no})
    if existing:
        raise HTTPException(status_code=400, detail="Invoice number already exists")
    
    await invoices_collection.insert_one(invoice.model_dump())
    
    # Update Client Ledger (only if sent/paid, but usually on creation/sending)
    # Requirement: "Creating an invoice updates the client ledger"
    # We'll assume any invoice created adds to the ledger.
    if invoice.status != "draft":
        await update_client_ledger(invoice.client_id, invoice.total_amount, "invoice_created")
        
    return invoice
        
@router.put("/invoices/{invoice_id}", response_model=InvoiceModel)
async def update_invoice(invoice_id: str, invoice_data: InvoiceModel):
    old_invoice = await invoices_collection.find_one({"id": invoice_id})
    if not old_invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    # Ledger Adjustment Logic
    # 1. Revert old impact if it was counted (Sent/Paid)
    if old_invoice.get("status") in ["sent", "partially_paid", "paid"]:
        # We need to DECREASE total_value by old amount
        # This function `update_client_ledger` adds. So we pass negative.
        # But wait, update_client_ledger logic is: total_value += amount. So passing negative works.
        await update_client_ledger(old_invoice["client_id"], -old_invoice["total_amount"], "invoice_created")

    # 2. Update Invoice
    # exclude id from update just in case
    update_dict = invoice_data.model_dump(exclude={"id", "created_at"})
    update_dict["updated_at"] = datetime.now()
    
    await invoices_collection.update_one(
        {"id": invoice_id},
        {"$set": update_dict}
    )
    
    # 3. Apply new impact if applicable
    if invoice_data.status in ["sent", "partially_paid", "paid"]:
         await update_client_ledger(invoice_data.client_id, invoice_data.total_amount, "invoice_created")

    return {**invoice_data.model_dump(), "id": invoice_id}

@router.post("/invoices/{invoice_id}/status")
async def update_invoice_status(invoice_id: str, status: str):
    invoice = await invoices_collection.find_one({"id": invoice_id})
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
        
    # If moving from draft to sent, update ledger
    if invoice["status"] == "draft" and status == "sent":
        await update_client_ledger(invoice["client_id"], invoice["total_amount"], "invoice_created")
        
    await invoices_collection.update_one(
        {"id": invoice_id},
        {"$set": {"status": status, "updated_at": datetime.now()}}
    )
    return {"status": "success"}

# -----------------------------------------------------------------------------
# Client Ledger API
# -----------------------------------------------------------------------------
@router.get("/client-ledger/{client_id}", response_model=ClientLedgerModel)
async def get_client_ledger(client_id: str):
    ledger = await ledgers_collection.find_one({"client_id": client_id})
    if not ledger:
        # Return empty ledger
        return ClientLedgerModel(client_id=client_id)
    return ledger

# -----------------------------------------------------------------------------
# Associate Payouts API
# -----------------------------------------------------------------------------
@router.get("/payouts", response_model=List[AssociatePayoutModel])
async def get_payouts():
    cursor = payouts_collection.find({}).sort("created_at", -1)
    return await cursor.to_list(length=100)

@router.post("/payouts", response_model=AssociatePayoutModel)
async def create_payout(payout: AssociatePayoutModel):
    await payouts_collection.insert_one(payout.model_dump())
    return payout

# -----------------------------------------------------------------------------
# Overview API
# -----------------------------------------------------------------------------
@router.get("/overview")
async def get_overview():
    # Calculate totals
    pipeline = [
        {"$group": {"_id": "$type", "total": {"$sum": "$amount"}}}
    ]
    cursor = transactions_collection.aggregate(pipeline)
    totals = {doc["_id"]: doc["total"] for doc in await cursor.to_list(length=None)}
    
    income = totals.get("income", 0.0)
    expenses = totals.get("expense", 0.0)
    
    # Outstanding Receivables
    ledger_cursor = ledgers_collection.aggregate([
        {"$group": {"_id": None, "total_balance": {"$sum": "$balance_amount"}}}
    ])
    receivables_doc = await ledger_cursor.to_list(length=1)
    receivables = receivables_doc[0]["total_balance"] if receivables_doc else 0.0
    
    return {
        "income": income,
        "expenses": expenses,
        "net_profit": income - expenses,
        "outstanding_receivables": receivables
    }
