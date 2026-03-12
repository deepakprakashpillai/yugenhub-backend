from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.runnables import RunnableConfig

from middleware.db_guard import ScopedDatabase
from agent.state import AgentState
from agent.tools import build_tools
from agent.nodes import should_continue
from config import config

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

    from langchain_core.messages import SystemMessage

    # 3. Define the agent node calling the LLM
    async def agent_node(state: AgentState, config: RunnableConfig):
        system_prompt = SystemMessage(
            content=(
                "You are an operational AI assistant for YugenHub. "
                "You are strictly designed to fetch and analyze business data regarding projects, clients, events, and team members. "
                "When presenting lists, statistics, or multiple items, ALWAYS use neatly structured Markdown: "
                "- Use Markdown TABLES for lists of projects, clients, or team members with multiple attributes. "
                "- Use BULLET POINTS for simple lists or summaries. "
                "- Use BOLD text to highlight important metrics or codes. "
                "Avoid long paragraphs; keep your responses professional, concise, and easy to scan. "
                "If no data is found, clearly state that. "
                "You must politely refuse any requests that fall outside of this specific operational scope."
            )
        )
        
        # Prepend the system prompt to the state messages on every invocation
        # ensuring the LLM is always grounded by this rule
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
