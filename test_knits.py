import asyncio
import os
from agent.graph import build_graph
from database import db
from middleware.db_guard import ScopedDatabase
import json

async def main():
    scoped_db = ScopedDatabase(db, "test_agency")
    
    graph = build_graph(scoped_db)
    
    query = "list the projects for knits"
    inputs = {"messages": [("user", query)]}
    
    # We will step through the graph to see what it does
    try:
        async for output in graph.astream(inputs, config={"recursion_limit": 10}, stream_mode="values"):
            # Output is the state
            if "messages" in output:
                last_message = output["messages"][-1]
                print(f"Message type: {last_message.type}")
                if last_message.type == "ai":
                    if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
                        print(f"Tool calls: {last_message.tool_calls}")
                    else:
                        print(f"Content: {last_message.content}")
                elif last_message.type == "tool":
                    print(f"Tool Result: {last_message.name} => {str(last_message.content)[:200]}")
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    asyncio.run(main())
