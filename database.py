from motor.motor_asyncio import AsyncIOMotorClient
from logging_config import get_logger
from config import config

logger = get_logger("database")

# 1. Get the URI and DB Name from config
uri = config.MONGO_URI
db_name = config.DB_NAME

# 2. Log connection status
if uri:
    logger.info(f"MongoDB connection string found: {uri[:20]}...")
else:
    logger.error("MONGO_URI not found in configuration!")

# 3. Initialize client
client = AsyncIOMotorClient(uri, tlsAllowInvalidCertificates=True)
db = client[db_name]

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

logger.info(f"Database collections initialized on DB: {db_name}")