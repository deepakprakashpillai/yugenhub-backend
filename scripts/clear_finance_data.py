
import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import logging

# Setup basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("clear_finance")

load_dotenv()

async def clear_finance_data():
    uri = os.getenv("MONGO_URI")
    if not uri:
        logger.error("MONGO_URI not found in .env")
        return

    client = AsyncIOMotorClient(uri, tlsAllowInvalidCertificates=True)
    db = client.yugen_hub

    collections_to_clear = [
        "finance_invoices",
        "finance_transactions",
        "finance_accounts",
        "finance_payouts",
        "finance_ledgers"
    ]

    logger.info("Starting to clear finance data...")

    for col_name in collections_to_clear:
        try:
            result = await db[col_name].delete_many({})
            logger.info(f"Cleared {result.deleted_count} documents from {col_name}")
        except Exception as e:
            logger.error(f"Failed to clear {col_name}: {e}")

    logger.info("Finance data clearance complete.")

if __name__ == "__main__":
    asyncio.run(clear_finance_data())
