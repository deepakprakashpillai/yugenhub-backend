import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# 1. Force load the env file
load_dotenv()

# 2. Get the URI
uri = os.getenv("MONGO_URI")

# 3. Add a Print statement here temporarily to debug!
if uri:
    print(f"✅ Connection string found: {uri[:20]}...") 
else:
    print("❌ ERROR: MONGO_URI not found in .env file!")

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