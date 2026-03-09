from typing import Literal
from langchain_core.messages import HumanMessage
from langgraph.prebuilt import ToolNode
from agent.state import AgentState

# The tool node itself is essentially ToolNode(tools), we just pass it
# to the graph builder.

def should_continue(state: AgentState) -> Literal["tools", "END"]:
    """
    Determine next routing step based on LLM response.
    Return 'tools' if LLM made a tool call, else 'END'.
    """
    messages = state["messages"]
    last_message = messages[-1]
    
    # If LLM makes a tool call, route to tools node
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"
    
    # Otherwise, LLM decided it has final answer
    return "END"
