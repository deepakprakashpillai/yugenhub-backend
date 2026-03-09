from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from langchain_groq import ChatGroq
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

    # 2. Configure LLM (Groq instead of OpenAI)
    llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0, api_key=config.GROQ_API_KEY)
    
    # Optional system prompt to guide agent behavior
    # Could be injected as a SystemMessage before compilation or in state,
    # but binding tools directly to the LLM is standard ReAct.
    llm_with_tools = llm.bind_tools(tools)

    # 3. Define the agent node calling the LLM
    async def agent_node(state: AgentState, config: RunnableConfig):
        response = await llm_with_tools.ainvoke(state["messages"], config)
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
