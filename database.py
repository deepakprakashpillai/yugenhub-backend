from motor.motor_asyncio import AsyncIOMotorClient
from logging_config import get_logger
from config import config
import certifi

logger = get_logger("database")

uri = config.MONGO_URI
db_name = config.DB_NAME

if uri:
    logger.info(f"MongoDB connection string found: {uri[:20]}...")
else:
    logger.error("MONGO_URI not found in configuration!")

class DatabaseProxy:
    def __init__(self):
        self._client = None
        self._db = None

    def initialize(self):
        if self._client is None:
            if config.ENV == "production":
                self._client = AsyncIOMotorClient(uri, tlsCAFile=certifi.where())
            else:
                self._client = AsyncIOMotorClient(uri, tlsAllowInvalidCertificates=True)
            self._db = self._client[db_name]
            logger.info(f"Database collections initialized on DB: {db_name}")

    def reset(self):
        if self._client is not None:
            self._client.close()
            self._client = None
            self._db = None

    def __getattr__(self, name):
        self.initialize()
        return getattr(self._client, name)

    def __getitem__(self, name):
        self.initialize()
        return self._client[name]


client = DatabaseProxy()

class DBProxy:
    def get_collection(self, name):
        return client[db_name][name]

    def __getattr__(self, attr):
        return client[db_name][attr]
        
    def __getitem__(self, key):
        return client[db_name][key]

db = DBProxy()

class AsyncCollectionProxy:
    def __init__(self, name):
        self.name = name

    def _get_collection(self):
        # We access the configured db dynamically
        return db.get_collection(self.name)

    def __getattr__(self, attr):
        return getattr(self._get_collection(), attr)
        
    def __getitem__(self, key):
        return self._get_collection()[key]

users_collection = AsyncCollectionProxy("users")
associates_collection = AsyncCollectionProxy("associates")
clients_collection = AsyncCollectionProxy("clients")
configs_collection = AsyncCollectionProxy("agency_configs")
projects_collection = AsyncCollectionProxy("projects")
tasks_collection = AsyncCollectionProxy("tasks")
task_history_collection = AsyncCollectionProxy("task_history")
notifications_collection = AsyncCollectionProxy("notifications")

# Finance Collections
accounts_collection = AsyncCollectionProxy("finance_accounts")
transactions_collection = AsyncCollectionProxy("finance_transactions")
ledgers_collection = AsyncCollectionProxy("finance_ledgers")
invoices_collection = AsyncCollectionProxy("finance_invoices")
payouts_collection = AsyncCollectionProxy("finance_payouts")

