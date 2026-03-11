import pytest
from httpx import AsyncClient
import os

pytestmark = pytest.mark.asyncio

# The test API key matches conftest's env setup
TEST_API_KEY = "test_n8n_api_key_for_testing"
AGENCY_ID = "test_agency"

@pytest.fixture(scope="function", autouse=True)
def set_n8n_key():
    """Ensure N8N_API_KEY is set for agent tests (reuses integration auth)."""
    from config import config
    original_api = config.N8N_API_KEY
    config.N8N_API_KEY = TEST_API_KEY
    
    # Mock GEMINI API key for tests so ChatGoogleGenerativeAI initialization doesn't fail
    original_gemini_env = os.environ.get("GEMINI_API_KEY")
    os.environ["GEMINI_API_KEY"] = "dummy_gemini_key"
    original_gemini_config = config.GEMINI_API_KEY
    config.GEMINI_API_KEY = "dummy_gemini_key"
    
    yield
    config.N8N_API_KEY = original_api
    config.GEMINI_API_KEY = original_gemini_config
    if original_gemini_env:
        os.environ["GEMINI_API_KEY"] = original_gemini_env
    else:
        if "GEMINI_API_KEY" in os.environ:
            del os.environ["GEMINI_API_KEY"]

def api_headers():
    return {"X-API-Key": TEST_API_KEY}

def base_params():
    return {"agency_id": AGENCY_ID}

def base_payload():
    return {
        "query": "How many active projects do we have?"
    }

# ─── Auth ──────────────────────────────────────────────────────────────

async def test_agent_no_api_key_returns_401(async_client: AsyncClient):
    """Requests without API key are rejected."""
    resp = await async_client.post("/api/agent/query", params=base_params(), json=base_payload())
    assert resp.status_code == 401

async def test_agent_wrong_api_key_returns_403(async_client: AsyncClient):
    """Requests with wrong API key are rejected."""
    resp = await async_client.post(
        "/api/agent/query",
        headers={"X-API-Key": "wrong_key"},
        params=base_params(),
        json=base_payload(),
    )
    assert resp.status_code == 403

async def test_agent_missing_agency_id_returns_422(async_client: AsyncClient):
    """Requests without agency_id are rejected."""
    resp = await async_client.post(
        "/api/agent/query", 
        headers=api_headers(), 
        json={"query": "test"}
    )
    assert resp.status_code == 422


# ─── Tools Binding ────────────────────────────────────────────────────────

async def test_build_tools_binds_correctly():
    """Verify tool factory returns all expected tools wrapped."""
    from agent.tools import build_tools
    from middleware.db_guard import ScopedDatabase
    from database import db as raw_db
    
    db = ScopedDatabase(raw_db, AGENCY_ID)
    tools = build_tools(db)
    
    assert len(tools) == 9
    tool_names = [t.name for t in tools]
    assert "list_projects" in tool_names
    assert "get_statistics" in tool_names
    assert "get_project_details" in tool_names


# ─── Endpoint Functionality (Mocked LLM) ───────────────────────────────────

from unittest.mock import patch, AsyncMock
from langchain_core.messages import AIMessage

@patch("agent.graph.ChatGoogleGenerativeAI.ainvoke")
async def test_agent_query_endpoint(mock_ainvoke, async_client: AsyncClient):
    """Test the endpoint returns the final AI message from the graph."""
    
    # Mock the LLM's ainvoke method to return a valid AIMessage
    mock_ainvoke.return_value = AIMessage(content="You have 5 active projects.")
    
    resp = await async_client.post(
        "/api/agent/query",
        headers=api_headers(),
        params=base_params(),
        json=base_payload()
    )
    
    assert resp.status_code == 200
    data = resp.json()
    assert "response" in data
    assert data["response"] == "You have 5 active projects."

# ─── UI Playground ─────────────────────────────────────────────────────────

async def test_agent_playground_ui(async_client: AsyncClient):
    """Test the playground HTML UI is served correctly."""
    resp = await async_client.get("/api/agent/playground")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "LangGraph Agent Chat" in resp.text
