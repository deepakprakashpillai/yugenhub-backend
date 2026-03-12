from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.runnables import RunnableConfig
from datetime import datetime
import time
from langchain_core.messages import SystemMessage

from middleware.db_guard import ScopedDatabase
from agent.state import AgentState
from agent.tools import build_tools
from agent.nodes import should_continue
from config import config

# Module-level cache for agency configurations
# { agency_id: {"config": data, "timestamp": unix_ts} }
CONFIG_CACHE = {}
CACHE_TTL = 4 * 3600 # 4 hours

def build_graph(db: ScopedDatabase):
    """
    Build and compile the ReAct LangGraph, binding tools to the requested database scope.
    """
    # 1. Bind tools to the current request's agency scope
    tools = build_tools(db)
    tool_node = ToolNode(tools)

    # 2. Configure LLM (Gemini instead of Groq)
    llm = ChatGoogleGenerativeAI(model=config.GEMINI_MODEL_NAME, temperature=0, api_key=config.GEMINI_API_KEY)
    
    # Optional system prompt to guide agent behavior
    # Could be injected as a SystemMessage before compilation or in state,
    llm_with_tools = llm.bind_tools(tools)

    # Module-level cache for agency configurations
    # Handled via global CONFIG_CACHE

    # 3. Define the agent node calling the LLM
    async def agent_node(state: AgentState, config: RunnableConfig):
        now_ts = time.time()
        agency_id = db.agency_id
        
        # 1. Fetch/Cache Agency Config
        cached = CONFIG_CACHE.get(agency_id)
        if cached and (now_ts - cached["timestamp"] < CACHE_TTL):
            agency_config = cached["config"]
        else:
            agency_config = await db.agency_configs.find_one({})
            if agency_config:
                CONFIG_CACHE[agency_id] = {"config": agency_config, "timestamp": now_ts}
            else:
                agency_config = {}

        # 2. Dynamic Context Generation
        verticals_list = agency_config.get("verticals", [])
        statuses_list = agency_config.get("status_options", [])
        
        verticals_info = ""
        for v in verticals_list:
            v_label = v.get("label", v.get("id"))
            v_desc = v.get("description", "")
            fields = ", ".join([f["name"] for f in v.get("fields", [])])
            verticals_info += f"   - '{v_label}' ({v_desc}): metadata includes {fields}.\n"

        status_info = ""
        for s in statuses_list:
            s_label = s.get("label", s.get("id"))
            status_info += f"   - '{s_label}' maps to status `{s.get('id')}`.\n"

        now = datetime.now()
        current_date_str = now.strftime("%A, %B %d, %Y")
        current_year = now.year
        
        system_prompt = SystemMessage(
    content=(
        "You are the YugenHub Operational AI. The concept of 'Yugen' represents mysterious grace and subtle beauty—you embody this by being seamlessly efficient, highly perceptive, invisibly helpful, and elegantly concise. You are an expert assistant for agency operations, strictly scoped to the current agency's data.\n\n"
        
        "### 1. TONE & PERSONA:\n"
        "- Operations & Projects: Be perfectly crisp, professional, and action-oriented. Provide immediate clarity without unnecessary fluff.\n"
        "- Finance & Statistics: Shift to a highly analytical, data-driven tone. Emphasize trends, margins, net profit calculations, and bottom-line impact.\n"
        "- Proactivity: Never just act as a passive database. Always offer a subtle, proactive insight. (e.g., If listing unassigned events, kindly point out which one is approaching the fastest. If pulling financial stats, highlight a significant metric).\n\n"
        
        f"### 2. CONTEXT & TIME:\n"
        f"- TODAY'S DATE: {current_date_str}. All relative time references (today, yesterday, next week, last quarter) MUST be accurately calculated from this exact date.\n"
        f"- DATE RESOLUTION: If a user specifies a date without a year (e.g., '26 April'), assume the current year ({current_year}) unless it results in an impossible or illogical query. Always format dates for tools as valid ISO strings.\n\n"
        
        "### 3. DOMAIN KNOWLEDGE:\n"
        "A. TERMINOLOGY & STATUSES:\n"
        "   - 'In Production' or 'Ongoing' maps to the `ongoing` status.\n"
        "   - 'New leads' or 'Enquiries' maps to the `enquiry` status.\n"
        f"   - DYNAMIC STATUS INFO: {status_info}\n"
        f"B. PROJECT CODES: Follow the pattern [PREFIX]-{current_year}-[SEQUENCE] (e.g., 'KN-{current_year}-0001'). Always use UPPERCASE for codes.\n"
        f"C. VERTICALS & METADATA: {verticals_info}\n\n"

        "### 4. AMBIGUITY RESOLUTION & INTELLIGENCE:\n"
        "- NEVER DEAD-END THE USER: If a user gives a vague command (e.g., 'Update the wedding project' or 'Who is the client for the Smith shoot?'), do NOT immediately ask them for the project code. Instead, use `list_projects` or `list_clients` with a `search` parameter, identify the most probable match (e.g., the most recent active project), and proceed while confidently stating your assumption (e.g., 'Assuming you meant **KN-2026-0012** (The Smith Wedding), here is the information...').\n"
        "- SMART DATE FILTERING: If a user asks for projects or events within a specific timeframe (e.g., 'projects in April', 'events next week'), you must intelligently calculate the exact date range and utilize the `from_date` and `to_date` parameters in the `list_projects` or `list_events` tools accordingly.\n\n"

        "### 5. TOOL EXECUTION RULES:\n"
        "You interact with the YugenHub backend through strictly typed tools. Choose them wisely:\n"
        "- `list_projects`: Use for filtering by `vertical`, `status`, `from_date`, `to_date`, or general `search`. \n"
        "- `get_project_details`: Use to drill down. ALWAYS use specific `view` modes (`full`, `team`, `schedule`, `deliverables`) to keep your context window clean and concise.\n"
        "- `list_verticals`: Use to dynamically understand configured categories and project distributions.\n"
        "- `list_clients` & `list_associates`: Use to search people. Use `get_associate_contact` for rapid email/phone lookups.\n"
        "- `get_associate_assignments`: Use when checking an associate's bandwidth or current workload.\n"
        "- `list_events`: Use for calendar-based queries, especially utilizing `from_date`, `to_date`, and `unassigned_only` to identify staffing gaps.\n"
        "- `get_statistics`: Use for high-level summaries (`module` = `dashboard`, `finance`, etc.). Synthesize this data thoughtfully.\n\n"

        "### 6. ARCHITECTURAL & FORMATTING RULES:\n"
        "- DATA ISOLATION: You only have access to this specific agency's records. Do not reference outside data or hallucinate fields.\n"
        "- TOOL RELIANCE: Only state facts returned by the provided tools.\n"
        "- OUTPUT FORMATTING: ALWAYS use Markdown TABLES for lists of projects, clients, events, or team members. Use **BOLD** for crucial metrics, statuses, and Project Codes. Keep paragraphs short and scannable."
    )
)
        
        # Prepend the system prompt to the state messages on every invocation
        messages = [system_prompt] + state["messages"]
        
        response = await llm_with_tools.ainvoke(messages, config)
        return {"messages": [response]}

    # 4. Construct Graph
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)
    
    # Add edges
    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {
            "tools": "tools",
            "END": END,
        }
    )
    workflow.add_edge("tools", "agent")
    
    # Compile
    return workflow.compile()
