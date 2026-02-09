import asyncio
from database import tasks_collection

async def count_tasks():
    count = await tasks_collection.count_documents({})
    print(f"✅ Total tasks in database: {count}")

if __name__ == "__main__":
    asyncio.run(count_tasks())
