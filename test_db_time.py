import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timezone
import json

async def main():
    client = AsyncIOMotorClient("mongodb://localhost:27017")
    db = client.yugenhub
    
    # Get latest history
    doc = await db.task_history.find_one({}, sort=[("timestamp", -1)])
    if doc:
        print("Raw doc timestamp:", repr(doc.get("timestamp")), type(doc.get("timestamp")))
        print("Has tzinfo?", doc.get("timestamp").tzinfo is not None if isinstance(doc.get("timestamp"), datetime) else False)
        
        # Test serialization
        ts = doc.get("timestamp")
        if isinstance(ts, datetime) and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        print("Replaced ts:", repr(ts))
        print("ISO:", ts.isoformat())
    else:
        print("No task history found")

asyncio.run(main())
