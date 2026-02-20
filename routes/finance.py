import uuid
from fastapi import APIRouter, HTTPException, Query, Depends, status, Body
from typing import List, Optional
from datetime import datetime
from models.finance import AccountModel, TransactionModel, ClientLedgerModel, InvoiceModel, AssociatePayoutModel
from bson import ObjectId

from routes.deps import get_current_user, get_db
from models.user import UserModel
from middleware.db_guard import ScopedDatabase
from constants import Roles
from logging_config import get_logger

logger = get_logger("finance")

async def ensure_finance_access(current_user: UserModel = Depends(get_current_user)):
    if current_user.role not in [Roles.ADMIN, Roles.OWNER]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access Denied: Finance data is restricted to Admins and Owners."
        )

router = APIRouter(prefix="/api/finance", tags=["Finance"], dependencies=[Depends(ensure_finance_access)])

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------
async def update_account_balance(db: ScopedDatabase, account_id: str, amount: float, transaction_type: str):
    inc_amount = 0.0
    if transaction_type == "income":
        inc_amount = amount
    elif transaction_type == "expense":
        inc_amount = -amount

    if inc_amount != 0:
        await db.accounts.update_one(
            {"id": account_id},
            {
                "$inc": {"current_balance": inc_amount},
                "$set": {"updated_at": datetime.now()}
            }
        )

async def update_client_ledger(db: ScopedDatabase, client_id: str, amount: float, operation: str):
    # 1. Ensure Ledger Exists (Atomic upsert)
    await db.ledgers.update_one(
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
        await db.ledgers.update_one({"client_id": client_id}, update_query)

    # 3. Recalculate Balance & Status
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
    await db.ledgers.update_one({"client_id": client_id}, pipeline)

# -----------------------------------------------------------------------------
# Accounts API
# -----------------------------------------------------------------------------
@router.get("/accounts", response_model=List[AccountModel])
async def get_accounts(db: ScopedDatabase = Depends(get_db)):
    cursor = db.accounts.find({})
    accounts = await cursor.to_list(length=100)
    return accounts

@router.post("/accounts", response_model=AccountModel)
async def create_account(account: AccountModel, db: ScopedDatabase = Depends(get_db)):
    existing = await db.accounts.find_one({"name": account.name})
    if existing:
        raise HTTPException(status_code=400, detail="Account with this name already exists")
    
    account.current_balance = account.opening_balance
    await db.accounts.insert_one(account.model_dump())
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
    limit: int = 50,
    db: ScopedDatabase = Depends(get_db)
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
    cursor = db.transactions.find(query).sort("date", -1).skip(skip).limit(limit)
    transactions = await cursor.to_list(length=limit)
    return transactions

@router.post("/transactions", response_model=TransactionModel)
async def create_transaction(transaction: TransactionModel, db: ScopedDatabase = Depends(get_db)):
    # 1. Validate Account
    account = await db.accounts.find_one({"id": transaction.account_id})
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # 2. Handle Transfer
    if transaction.type == "transfer":
        pass  # Frontend handles duality for now

    # 3. Update Account Balance
    await update_account_balance(db, transaction.account_id, transaction.amount, transaction.type)
    
    # 4. Save Transaction
    await db.transactions.insert_one(transaction.model_dump())
    
    # 5. Link to Client Ledger if applicable (Income)
    if transaction.type == "income" and transaction.client_id:
        await update_client_ledger(db, transaction.client_id, transaction.amount, "payment_received")
        
    return transaction

# -----------------------------------------------------------------------------
# Invoices API
# -----------------------------------------------------------------------------
@router.get("/invoices", response_model=List[InvoiceModel])
async def get_invoices(
    project_id: Optional[str] = None,
    limit: int = 100,
    db: ScopedDatabase = Depends(get_db)
):
    query = {}
    if project_id:
        query["project_id"] = project_id
        
    cursor = db.invoices.find(query).sort("created_at", -1)
    return await cursor.to_list(length=limit)

@router.post("/invoices", response_model=InvoiceModel)
async def create_invoice(invoice: InvoiceModel, db: ScopedDatabase = Depends(get_db)):
    existing = await db.invoices.find_one({"invoice_no": invoice.invoice_no})
    if existing:
        raise HTTPException(status_code=400, detail="Invoice number already exists")
    
    await db.invoices.insert_one(invoice.model_dump())
    
    if invoice.status != "draft":
        await update_client_ledger(db, invoice.client_id, invoice.total_amount, "invoice_created")
        
    return invoice
        
@router.put("/invoices/{invoice_id}", response_model=InvoiceModel)
async def update_invoice(invoice_id: str, invoice_data: InvoiceModel, db: ScopedDatabase = Depends(get_db)):
    old_invoice = await db.invoices.find_one({"id": invoice_id})
    if not old_invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    if old_invoice.get("status") in ["sent", "partially_paid", "paid"]:
        await update_client_ledger(db, old_invoice["client_id"], -old_invoice["total_amount"], "invoice_created")

    update_dict = invoice_data.model_dump(exclude={"id", "created_at"})
    update_dict["updated_at"] = datetime.now()
    
    await db.invoices.update_one(
        {"id": invoice_id},
        {"$set": update_dict}
    )
    
    if invoice_data.status in ["sent", "partially_paid", "paid"]:
         await update_client_ledger(db, invoice_data.client_id, invoice_data.total_amount, "invoice_created")

    return {**invoice_data.model_dump(), "id": invoice_id}

@router.post("/invoices/{invoice_id}/status")
async def update_invoice_status(invoice_id: str, status: str, db: ScopedDatabase = Depends(get_db)):
    invoice = await db.invoices.find_one({"id": invoice_id})
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
        
    if invoice["status"] == "draft" and status == "sent":
        await update_client_ledger(db, invoice["client_id"], invoice["total_amount"], "invoice_created")
        
    await db.invoices.update_one(
        {"id": invoice_id},
        {"$set": {"status": status, "updated_at": datetime.now()}}
    )
    return {"status": "success"}

# -----------------------------------------------------------------------------
# Client Ledger API
# -----------------------------------------------------------------------------
@router.get("/client-ledger/{client_id}", response_model=ClientLedgerModel)
async def get_client_ledger(client_id: str, db: ScopedDatabase = Depends(get_db)):
    ledger = await db.ledgers.find_one({"client_id": client_id})
    if not ledger:
        return ClientLedgerModel(client_id=client_id)
    return ledger

# -----------------------------------------------------------------------------
# Associate Payouts API
# -----------------------------------------------------------------------------
@router.get("/payouts", response_model=List[AssociatePayoutModel])
async def get_payouts(db: ScopedDatabase = Depends(get_db)):
    cursor = db.payouts.find({}).sort("created_at", -1)
    return await cursor.to_list(length=100)

@router.post("/payouts", response_model=AssociatePayoutModel)
async def create_payout(payout: AssociatePayoutModel, db: ScopedDatabase = Depends(get_db)):
    await db.payouts.insert_one(payout.model_dump())
    return payout

# -----------------------------------------------------------------------------
# Overview API
# -----------------------------------------------------------------------------
@router.get("/overview")
async def get_overview(db: ScopedDatabase = Depends(get_db)):
    pipeline = [
        {"$group": {"_id": "$type", "total": {"$sum": "$amount"}}}
    ]
    cursor = db.transactions.aggregate(pipeline)
    totals = {doc["_id"]: doc["total"] for doc in await cursor.to_list(length=None)}
    
    income = totals.get("income", 0.0)
    expenses = totals.get("expense", 0.0)
    
    ledger_cursor = db.ledgers.aggregate([
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
