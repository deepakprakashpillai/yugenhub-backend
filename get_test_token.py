import asyncio
from database import users_collection 
from routes.deps import create_access_token
from logging_config import setup_logging

setup_logging()

async def get_token():
    user = await users_collection.find_one({})
    if user:
        token = create_access_token({"sub": user["id"], "agency_id": user["agency_id"]})
        print(f"TOKEN={token}")
        print(f"USER_ID={user['id']}")
        print(f"AGENCY_ID={user['agency_id']}")
    else:
        print("No users found")

if __name__ == "__main__":
    asyncio.run(get_token())
