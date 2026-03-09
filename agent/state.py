from typing import Annotated, TypedDict
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    """
    Shared state for the LangGraph agent.
    `messages` contains the history of the conversation, including HumanMessage,
    AIMessage, and ToolMessage. The `add_messages` reducer ensures messages
    are appended rather than overwritten.
    """
    messages: Annotated[list, add_messages]
