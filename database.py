import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from logging_config import get_logger

# 1. Force load the env file
load_dotenv()

logger = get_logger("database")

# 2. Get the URI
uri = os.getenv("MONGO_URI")

# 3. Log connection status
if uri:
    logger.info(f"MongoDB connection string found: {uri[:20]}...")
else:
    logger.error("MONGO_URI not found in .env file!")

# 4. Initialize client
client = AsyncIOMotorClient(uri, tlsAllowInvalidCertificates=True)
db = client.yugen_hub 

associates_collection = db.get_collection("associates")
clients_collection = db.get_collection("clients")
configs_collection = db.get_collection("agency_configs")
projects_collection = db.get_collection("projects")
users_collection = db.get_collection("users")
tasks_collection = db.get_collection("tasks")
task_history_collection = db.get_collection("task_history")
notifications_collection = db.get_collection("notifications")

# Finance Collections
accounts_collection = db.get_collection("finance_accounts")
transactions_collection = db.get_collection("finance_transactions")
ledgers_collection = db.get_collection("finance_ledgers")
invoices_collection = db.get_collection("finance_invoices")
payouts_collection = db.get_collection("finance_payouts")

logger.info("Database collections initialized", extra={"data": {"db": "yugen_hub"}})