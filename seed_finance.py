import asyncio
from database import accounts_collection, transactions_collection, ledgers_collection, invoices_collection, payouts_collection, clients_collection, associates_collection
from datetime import datetime, timedelta
import random
from bson import ObjectId

async def seed_finance():
    print("🗑️  Clearing existing finance data...")
    await accounts_collection.delete_many({})
    await transactions_collection.delete_many({})
    await ledgers_collection.delete_many({})
    await invoices_collection.delete_many({})
    await payouts_collection.delete_many({})
    print("✅ Cleared all finance collections.")

    # 1. Fetch existing Clients and Associates for linking
    clients = await clients_collection.find().to_list(length=100)
    associates = await associates_collection.find().to_list(length=100)

    if not clients:
        print("⚠️ No clients found. Please run seed_clients.py first.")
        # Create dummy client if none exist for testing
        clients = [{"_id": ObjectId(), "name": "Dummy Client", "agency_id": "default_agency"}]
    
    if not associates:
        print("⚠️ No associates found. Please run seed_associates.py first.")
        # Create dummy associate
        associates = [{"_id": ObjectId(), "name": "Dummy Associate", "agency_id": "default_agency"}]

    # 2. Create Accounts
    print("💰 Creating Accounts...")
    accounts = [
        {
            "name": "HDFC - Operational",
            "type": "bank",
            "opening_balance": 500000.0,
            "current_balance": 500000.0,
            "currency": "INR",
            "is_active": True,
            "agency_id": "default_agency",
            "created_at": datetime.now()
        },
        {
            "name": "Cash In Hand",
            "type": "cash",
            "opening_balance": 20000.0,
            "current_balance": 20000.0,
            "currency": "INR",
            "is_active": True,
            "agency_id": "default_agency",
            "created_at": datetime.now()
        },
        {
            "name": "ICICI - Reserve",
            "type": "bank",
            "opening_balance": 1000000.0,
            "current_balance": 1000000.0,
            "currency": "INR",
            "is_active": True,
            "agency_id": "default_agency",
            "created_at": datetime.now()
        },
        {
            "name": "Corporate Credit Card",
            "type": "card",
            "opening_balance": -50000.0, # Liability
            "current_balance": -50000.0,
            "currency": "INR",
            "is_active": True,
            "agency_id": "default_agency",
            "created_at": datetime.now()
        }
    ]
    
    acc_results = await accounts_collection.insert_many(accounts)
    account_ids = acc_results.inserted_ids
    print(f"✅ Created {len(account_ids)} accounts.")

    # 3. Create Transactions (Add complexity: update balances)
    print("💸 Creating Transactions...")
    transactions = []
    
    # 3a. Income
    for i in range(5):
        amount = random.randint(10000, 100000)
        client = random.choice(clients)
        account_idx = 0 # HDFC
        
        transactions.append({
            "type": "income",
            "amount": float(amount),
            "date": datetime.now() - timedelta(days=random.randint(0, 30)),
            "account_id": str(account_ids[account_idx]),
            "category": "Project Payment",
            "subcategory": "Advance",
            "client_id": str(client["_id"]),
            "notes": f"Payment from {client['name']}",
            "source": "manual",
            "created_by": "system",
            "created_at": datetime.now(),
            "agency_id": "default_agency"
        })
        
        # Update HDFC Balance
        accounts[account_idx]["current_balance"] += amount

    # 3b. Expense
    categories = ["Rent", "Salaries", "Software", "Equipment", "Travel"]
    for i in range(8):
        amount = random.randint(1000, 20000)
        account_idx = random.choice([0, 1]) # HDFC or Cash
        
        transactions.append({
            "type": "expense",
            "amount": float(amount),
            "date": datetime.now() - timedelta(days=random.randint(0, 30)),
            "account_id": str(account_ids[account_idx]),
            "category": random.choice(categories),
            "subcategory": "General",
            "notes": "Office expense",
            "source": "manual",
            "created_by": "system",
            "created_at": datetime.now(),
            "agency_id": "default_agency"
        })
        
        # Update Balance
        accounts[account_idx]["current_balance"] -= amount

    await transactions_collection.insert_many(transactions)
    
    # Sync Account Balances in DB
    for idx, acc_id in enumerate(account_ids):
        await accounts_collection.update_one(
            {"_id": acc_id}, 
            {"$set": {"current_balance": accounts[idx]["current_balance"]}}
        )
    
    print(f"✅ Created {len(transactions)} transactions and updated balances.")

    # 4. Create Invoices
    print("📄 Creating Invoices...")
    invoices = []
    for i in range(5):
        client = random.choice(clients)
        total = random.randint(50000, 200000)
        invoices.append({
            "invoice_no": f"INV-2024-{100+i}",
            "client_id": str(client["_id"]),
            "project_id": None,
            "line_items": [
                {"title": "Photography Services", "quantity": 1, "price": total * 0.6, "total": total * 0.6},
                {"title": "Editing", "quantity": 1, "price": total * 0.4, "total": total * 0.4}
            ],
            "total_amount": float(total),
            "status": random.choice(["draft", "sent", "paid"]),
            "agency_id": "default_agency",
            "created_at": datetime.now() - timedelta(days=random.randint(1, 60))
        })
    
    await invoices_collection.insert_many(invoices)
    print(f"✅ Created {len(invoices)} invoices.")

    # 5. Create Payouts
    print("👥 Creating Payouts...")
    payouts = []
    for i in range(5):
        assoc = random.choice(associates)
        amount = random.randint(5000, 25000)
        payouts.append({
            "associate_id": str(assoc["_id"]),
            "project_id": None,
            "role": "Editor",
            "agreed_amount": float(amount),
            "paid_amount": 0.0,
            "status": "pending",
            "due_date": datetime.now() + timedelta(days=15),
            "agency_id": "default_agency",
            "created_at": datetime.now()
        })
    
    await payouts_collection.insert_many(payouts)
    print(f"✅ Created {len(payouts)} payouts.")
    
    print("\n🎉 Finance Data Population Complete!")

if __name__ == "__main__":
    asyncio.run(seed_finance())
