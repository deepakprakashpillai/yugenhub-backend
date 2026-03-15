from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.runnables import RunnableConfig
from datetime import datetime
import time
from cachetools import TTLCache
from langchain_core.messages import SystemMessage, HumanMessage

from middleware.db_guard import ScopedDatabase
from agent.state import AgentState
from agent.tools import get_tool_defs, create_tool_executors
from agent.nodes import should_continue
from config import config

# ─── Module-Level Singletons (created once on import) ──────────────────────

_llm = None
_tool_defs = None
_llm_with_tools = None
_compiled_graph = None

def get_llm():
    """Lazy initialization of the LLM to prevent crash on import in CI environments where no API key exists."""
    global _llm, _tool_defs, _llm_with_tools
    if _llm is None:
        _llm = ChatGoogleGenerativeAI(
            model=config.GEMINI_MODEL_NAME,
            temperature=0,
            api_key=config.GEMINI_API_KEY or "dummy_key_for_ci",
        )
        # Tool definitions (schemas only, no DB binding)
        _tool_defs = get_tool_defs()
        # LLM with tools bound — created once. Tool schemas never change between requests.
        _llm_with_tools = _llm.bind_tools(_tool_defs)
    return _llm_with_tools

# Agency config cache with bounded size and automatic TTL eviction
CONFIG_CACHE: TTLCache = TTLCache(maxsize=200, ttl=4 * 3600)


# ─── System Prompt Builder ─────────────────────────────────────────────────

async def _build_system_prompt(db: ScopedDatabase) -> SystemMessage:
    """Build the dynamic system prompt, using cached agency config when possible."""
    agency_id = db.agency_id

    # Fetch / cache agency config (TTLCache handles expiry automatically)
    agency_config = CONFIG_CACHE.get(agency_id)
    if agency_config is None:
        agency_config = await db.agency_configs.find_one({})
        if agency_config:
            CONFIG_CACHE[agency_id] = agency_config
        else:
            agency_config = {}

    # Dynamic context pieces — only include if non-empty
    verticals_list = agency_config.get("verticals", [])
    statuses_list = agency_config.get("status_options", [])

    now = datetime.now()
    current_date_str = now.strftime("%A, %B %d, %Y")
    current_year = now.year

    # Build dynamic sections conditionally
    dynamic_sections = []

    if statuses_list:
        status_lines = "\n".join(
            f"   - '{s.get('label', s.get('id'))}' → status `{s.get('id')}`"
            for s in statuses_list
        )
        dynamic_sections.append(f"   Agency-specific statuses:\n{status_lines}")

    if verticals_list:
        vert_lines = "\n".join(
            f"   - '{v.get('label', v.get('id'))}' ({v.get('description', '')}): "
            f"metadata fields: {', '.join(f['name'] for f in v.get('fields', []))}"
            for v in verticals_list
        )
        dynamic_sections.append(f"   Configured verticals:\n{vert_lines}")

    domain_dynamic = "\n".join(dynamic_sections) if dynamic_sections else ""

    return SystemMessage(
        content=(
            "You are the YugenHub Operational AI — seamlessly efficient, perceptive, and elegantly concise. "
            "You assist with agency operations using ONLY data from the current agency's scope.\n\n"

            "### TONE:\n"
            "- Operations: Crisp, professional, action-oriented.\n"
            "- Finance: Analytical, data-driven. Emphasize trends and margins.\n"
            "- Always offer a proactive insight (e.g., flag the nearest approaching unassigned event, highlight a significant metric).\n\n"

            f"### CONTEXT:\n"
            f"- TODAY: {current_date_str}. Calculate all relative dates from this.\n"
            f"- Dates without a year → assume {current_year}. Format dates as ISO strings for tools.\n"
            f"- Project codes: [PREFIX]-{current_year}-[SEQ] (e.g., 'KN-{current_year}-0001'). Always UPPERCASE. Backend auto-normalizes.\n"
            f"- Each project has a `project_name` resolved from metadata (e.g., 'Aswin & Priya'). Use it for display.\n\n"

            "### DOMAIN:\n"
            "- 'In Production'/'Ongoing' → `ongoing` status. 'New leads'/'Enquiries' → `enquiry` status.\n"
            f"{domain_dynamic}\n\n" if domain_dynamic else
            "### DOMAIN:\n"
            "- 'In Production'/'Ongoing' → `ongoing` status. 'New leads'/'Enquiries' → `enquiry` status.\n\n"

            "### QUERY STRATEGY:\n"
            "A. **Name + event type** (e.g., 'Aswin's reception') → `list_events(search=\"<name>\")`\n"
            "B. **Name only** (e.g., 'Aswin's project') → `list_projects(search=\"<name>\")`\n"
            "C. **Event type only** (e.g., 'receptions this month') → `list_events(search=\"<type>\")` + date filters\n"
            "D. **Project code** → `get_project_details(id_or_code=\"<code>\")` directly\n"
            "E. **Vague reference** → `list_projects(search=\"<keyword>\")`, proceed with best match, state your assumption\n\n"

            "### RULES:\n"
            "- NEVER dead-end the user. Search first, then state assumptions confidently.\n"
            "- Calculate exact date ranges for timeframe queries (e.g., 'events next week' → compute from_date/to_date).\n"
            "- Prefer specific `view` modes on `get_project_details` (`team`, `schedule`, `deliverables`) over `full` to stay concise.\n"
            "- Only state facts returned by tools. Explain errors gracefully.\n"
            "- Use Markdown **tables** for lists. **Bold** for key metrics, statuses, and codes."
        )
    )


# ─── Graph Builder ─────────────────────────────────────────────────────────

def _build_compiled_graph():
    """Build and compile the graph topology once. The graph is request-agnostic."""
    workflow = StateGraph(AgentState)
    workflow.add_node("agent", _agent_node)
    workflow.add_node("tools", _tool_node_placeholder)
    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", "END": END},
    )
    workflow.add_edge("tools", "agent")
    return workflow.compile()


async def _agent_node(state: AgentState, config: RunnableConfig):
    """Agent node: calls LLM with current messages. System prompt is already in state."""
    llm_with_tools = get_llm()
    response = await llm_with_tools.ainvoke(state["messages"], config)
    return {"messages": [response]}


async def _tool_node_placeholder(state: AgentState, config: RunnableConfig):
    """Tool execution node — delegates to request-scoped tool executors via config."""
    # Extract the db-bound tool executors from the configurable metadata
    db = config.get("configurable", {}).get("db")
    if db is None:
        raise ValueError("No database scope found in config. This is a bug.")

    # Create request-scoped tool executors (lightweight closures, schemas already cached)
    tools = create_tool_executors(db)
    tool_node = ToolNode(tools, handle_tool_errors=True)
    return await tool_node.ainvoke(state, config)


def get_compiled_graph():
    """Return the cached compiled graph, building it on first call."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = _build_compiled_graph()
    return _compiled_graph


async def run_agent(query: str, db: ScopedDatabase, recursion_limit: int = 15):
    """
    High-level entry point: run the agent with a query scoped to a database.
    System prompt is built once and injected into the initial state.
    The compiled graph is reused across all requests.
    """
    # Build system prompt once for this request
    system_prompt = await _build_system_prompt(db)

    # Initial state with system prompt + user message
    inputs = {"messages": [system_prompt, HumanMessage(content=query)]}

    # Config: pass db scope for tool execution + set recursion limit
    run_config = {
        "recursion_limit": recursion_limit,
        "configurable": {"db": db},
    }

    graph = get_compiled_graph()
    return await graph.ainvoke(inputs, run_config)
