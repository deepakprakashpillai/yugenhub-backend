import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from database import notifications_collection, projects_collection, tasks_collection

async def check_indexes():
    print("--- Notifications Indexes ---")
    async for index in notifications_collection.list_indexes():
        print(index)

    print("\n--- Projects Indexes ---")
    async for index in projects_collection.list_indexes():
        print(index)

    print("\n--- Tasks Indexes ---")
    async for index in tasks_collection.list_indexes():
        print(index)

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(check_indexes())
