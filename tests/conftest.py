import pytest
from httpx import AsyncClient, ASGITransport
import os
import asyncio
from datetime import timedelta

# Set up test environment variables before anything else
worker_id = os.environ.get("PYTEST_XDIST_WORKER", "master")
db_name = f"yugen_hub_test_{worker_id}"

os.environ["ENV"] = "testing"
os.environ["DB_NAME"] = db_name
os.environ["SECRET_KEY"] = "test_secret_key_12345"

from config import config
config.ENV = "testing"
config.DB_NAME = db_name

from main import app
from database import client, users_collection
from routes.deps import create_access_token



from pymongo import MongoClient

# Setup sync client for testing fixtures
sync_client = MongoClient(config.MONGO_URI if hasattr(config, "MONGO_URI") and config.MONGO_URI else "mongodb://localhost:27017/")
sync_db = sync_client[config.DB_NAME]



# Seed function (re-used per test)
def seed_default_config():
    sync_db.agency_configs.update_one(
        {"agency_id": "test_agency"},
        {"$set": {
            "agency_id": "test_agency",
            "org_name": "Test Agency",
            "org_email": "test@agency.com",
            "org_phone": "+1234567890",
            "theme_mode": "dark",
            "accent_color": "#ef4444",
            "verticals": [{"id": "wedding", "label": "Wedding Photography"}],
            "lead_sources": ["Website"],
            "project_statuses": [{"id": "enquiry", "label": "Enquiry"}],
            "status_options": [
                {"id": "enquiry", "label": "Enquiry", "color": "#aaa", "fixed": True},
                {"id": "booked", "label": "Booked", "color": "#bbb", "fixed": True},
                {"id": "ongoing", "label": "Ongoing", "color": "#ccc", "fixed": True},
                {"id": "completed", "label": "Completed", "color": "#ddd", "fixed": True},
                {"id": "cancelled", "label": "Cancelled", "color": "#eee", "fixed": True},
            ],
            "deliverable_types": ["Photo"],
            "associate_roles": ["Photographer"],
            "finance_categories": [
                {"id": "operations", "label": "Operations"},
                {"id": "salary", "label": "Salary"},
            ],
        }},
        upsert=True
    )

@pytest.fixture(scope="session", autouse=True)
def test_db_session():
    # Ensure clean state from any previously crashed runs on startup
    sync_client.drop_database(config.DB_NAME)
    yield sync_db
    # Teardown: drop the database after tests are done
    sync_client.drop_database(config.DB_NAME)

@pytest.fixture(scope="function", autouse=True)
def clean_db(test_db_session):
    """Drop all collections before each test and re-seed defaults to ensure test isolation."""
    for collection in sync_db.list_collection_names():
        sync_db.drop_collection(collection)
    seed_default_config()
    yield

@pytest.fixture(scope="function")
async def async_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client

@pytest.fixture(scope="function")
def test_user():
    # Insert a dummy user in test db synchronously
    user_data = {
        "id": "test_owner_id",
        "email": "owner@test.com",
        "name": "Test Owner",
        "role": "owner",
        "agency_id": "test_agency",
        "google_id": "test_google_id"
    }
    sync_db.users.update_one(
        {"id": user_data["id"]},
        {"$set": user_data},
        upsert=True
    )
    return user_data

@pytest.fixture(scope="function")
def test_member_user():
    """Insert a member user for RBAC testing."""
    user_data = {
        "id": "test_member_id",
        "email": "member@test.com",
        "name": "Test Member",
        "role": "member",
        "agency_id": "test_agency",
        "google_id": "test_member_google_id"
    }
    sync_db.users.update_one(
        {"id": user_data["id"]},
        {"$set": user_data},
        upsert=True
    )
    return user_data

@pytest.fixture(scope="function")
def auth_token(test_user):
    token = create_access_token(
        data={"sub": test_user["id"], "agency_id": test_user["agency_id"]},
        expires_delta=timedelta(minutes=60)
    )
    return token

@pytest.fixture(scope="function")
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}

@pytest.fixture(scope="function")
def member_auth_headers(test_member_user):
    token = create_access_token(
        data={"sub": test_member_user["id"], "agency_id": test_member_user["agency_id"]},
        expires_delta=timedelta(minutes=60)
    )
    return {"Authorization": f"Bearer {token}"}

@pytest.fixture(scope="function", autouse=True)
def reset_motor_client():
    from database import client
    client.reset()
