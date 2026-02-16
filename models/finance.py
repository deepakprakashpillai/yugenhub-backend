from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Literal
from datetime import datetime
from bson import ObjectId
import uuid

# -----------------------------------------------------------------------------
# 1. Accounts Model
# -----------------------------------------------------------------------------
class AccountModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agency_id: str = "default"
    name: str
    type: Literal['cash', 'bank', 'card', 'wallet', 'loan']
    opening_balance: float = 0.0
    current_balance: float = 0.0
    currency: str = "INR"
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str}
    )

# -----------------------------------------------------------------------------
# 2. Transactions Model
# -----------------------------------------------------------------------------
class TransactionModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agency_id: str = "default"
    type: Literal['income', 'expense', 'transfer']
    amount: float
    date: datetime = Field(default_factory=datetime.now)
    account_id: str  # The account affected
    # For transfers, we might linked_transaction_id or create two records.
    # Pattern: Transfer creates 2 transaction records:
    # 1. Expense from Source Account
    # 2. Income to Destination Account
    # linked_transaction_id helps identify the pair.
    linked_transaction_id: Optional[str] = None
    
    category: str  # e.g., "Operations", "Salary", "Project"
    subcategory: Optional[str] = None
    
    # Context Links
    project_id: Optional[str] = None
    client_id: Optional[str] = None
    associate_id: Optional[str] = None
    
    notes: Optional[str] = None
    source: Literal['manual', 'invoice', 'payout', 'expense'] = 'manual'
    status: Literal['completed', 'pending', 'cancelled'] = 'completed'
    
    created_by: Optional[str] = "system"
    created_at: datetime = Field(default_factory=datetime.now)

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str}
    )

# -----------------------------------------------------------------------------
# 3. Client Ledger Model
# -----------------------------------------------------------------------------
class ClientLedgerModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agency_id: str = "default"
    client_id: str
    project_id: Optional[str] = None # Optional: Ledger per project or per client?
                                     # Requirement says "optionally per project"
    
    total_value: float = 0.0      # Sum of all Invoices/Agreed Amounts
    received_amount: float = 0.0  # Sum of all Income Transactions linked
    balance_amount: float = 0.0   # total_value - received_amount
    
    status: Literal['pending', 'partially_paid', 'settled'] = 'pending'
    last_updated: datetime = Field(default_factory=datetime.now)

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str}
    )

# -----------------------------------------------------------------------------
# 4. Invoices Model
# -----------------------------------------------------------------------------
class InvoiceLineItem(BaseModel):
    title: str
    quantity: int = 1
    price: float
    total: float

class InvoiceModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agency_id: str = "default"
    invoice_no: str
    client_id: str
    project_id: Optional[str] = None
    
    line_items: List[InvoiceLineItem] = Field(default_factory=list)
    total_amount: float = 0.0
    
    status: Literal['draft', 'sent', 'partially_paid', 'paid'] = 'draft'
    due_date: Optional[datetime] = None
    
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str}
    )

# -----------------------------------------------------------------------------
# 5. Associate Payouts Model
# -----------------------------------------------------------------------------
class AssociatePayoutModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agency_id: str = "default"
    associate_id: str
    project_id: Optional[str] = None
    
    role: str
    agreed_amount: float = 0.0
    paid_amount: float = 0.0
    
    status: Literal['pending', 'partially_paid', 'paid'] = 'pending'
    due_date: Optional[datetime] = None
    
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str}
    )
