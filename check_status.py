import asyncio
from database import projects_collection

async def main():
    statuses = await projects_collection.distinct("status", {"vertical": "knots"})
    print(f"Statuses found: {statuses}")

if __name__ == "__main__":
    asyncio.run(main())
