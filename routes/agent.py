import asyncio
from fastapi import APIRouter, Depends, HTTPException, Body, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from middleware.db_guard import ScopedDatabase
from middleware.rate_limiter import check_agent_rate_limit
from routes.deps import get_integration_db
from logging_config import get_logger
from agent.graph import run_agent
from config import config

router = APIRouter(prefix="/api/agent", tags=["AI Agent"])
logger = get_logger("agent")

# Request timeout for agent queries (seconds)
AGENT_TIMEOUT = 90

class QueryRequest(BaseModel):
    query: str
    include_steps: bool = False

@router.post("/query")
async def process_query(
    request: QueryRequest,
    req: Request,
    db: ScopedDatabase = Depends(get_integration_db),
    _=Depends(check_agent_rate_limit),
):
    """
    Process a natural language query using the LangGraph ReAct agent.
    The agent determines which tools to invoke based on existing integration endpoints.
    Requires same API key auth and agency tracking as integration endpoints.
    """
    from langgraph.errors import GraphRecursionError
    try:
        # Run the agent with timeout protection
        result = await asyncio.wait_for(
            run_agent(query=request.query, db=db, recursion_limit=config.AGENT_RECURSION_LIMIT),
            timeout=AGENT_TIMEOUT,
        )
        
        # Extract the final AIMessage content
        final_message = result["messages"][-1].content
        
        # Extract execution steps (tool calls and results) and track total tokens from AIMessages
        execution_steps = []
        total_tokens = 0
        
        for msg in result["messages"]:
            if hasattr(msg, "usage_metadata") and msg.usage_metadata is not None:
                total_tokens += msg.usage_metadata.get("total_tokens", 0)
                
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for t in msg.tool_calls:
                    execution_steps.append({
                        "type": "tool_call",
                        "tool": t["name"],
                        "args": t["args"]
                    })
            elif msg.type == "tool":
                execution_steps.append({
                    "type": "tool_result",
                    "tool": msg.name,
                    "result": msg.content
                })
        
        response_data = {
            "response": final_message,
        }
        
        if request.include_steps:
            response_data["steps"] = execution_steps
            
        if total_tokens > 0:
            response_data["usage"] = {"total_tokens": total_tokens}
            
        return response_data

    except asyncio.TimeoutError:
        logger.warning(f"Agent timed out after {AGENT_TIMEOUT}s processing query: {request.query}")
        return {
            "response": f"The query took too long to process (>{AGENT_TIMEOUT}s). Please try a simpler or more specific question.",
            **({"steps": []} if request.include_steps else {}),
        }
    except GraphRecursionError:
        logger.warning(f"Agent hit recursion limit processing query: {request.query}")
        response_data = {
            "response": "I ran out of steps attempting to answer that question. Too many tool calls were required or I got caught in a loop. Please try a simpler or more specific query.",
        }
        if request.include_steps:
            response_data["steps"] = []
        return response_data
    except Exception as e:
        logger.error(f"Agent query failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal agent processing error: {str(e)}")


@router.get("/playground", response_class=HTMLResponse)
async def agent_playground():
    """
    Serves a simple HTML/JS UI for testing the LangGraph agent in the browser.
    Injects the backend's N8N API Key automatically from the environment.
    """
    api_key_value = config.N8N_API_KEY if config.N8N_API_KEY else ""

    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>YugenHub AI Agent Playground</title>
        <style>
            * {
                box-sizing: border-box;
            }
            :root {
                --bg: #1e1e2e;
                --text: #cdd6f4;
                --accent: #cba6f7;
                --surface: #313244;
                --user-msg: #b4befe;
                --ai-msg: #313244;
            }
            html {
                height: 100%;
                overflow: hidden;
            }
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                background: var(--bg);
                color: var(--text);
                margin: 0;
                padding: 20px;
                display: flex;
                height: 100%;
                box-sizing: border-box;
                justify-content: center;
                gap: 20px;
                overflow: hidden;
            }
            .panel {
                flex: 1 1 0;
                min-height: 0;
                background: #11111b;
                border-radius: 12px;
                display: flex;
                flex-direction: column;
                box-shadow: 0 10px 30px rgba(0,0,0,0.5);
                overflow: hidden;
            }
            .header {
                flex-shrink: 0;
                padding: 20px;
                background: var(--surface);
                border-bottom: 1px solid #45475a;
            }
            .header h1 {
                margin: 0;
                font-size: 1.2rem;
                color: var(--accent);
            }
            .chat-box {
                flex: 1 1 0;
                min-height: 0;
                padding: 20px;
                overflow-y: auto;
                display: flex;
                flex-direction: column;
                gap: 15px;
            }
            .message {
                max-width: 80%;
                padding: 12px 16px;
                border-radius: 12px;
                line-height: 1.5;
            }
            .message.user {
                background: var(--user-msg);
                color: #11111b;
                align-self: flex-end;
                border-bottom-right-radius: 4px;
            }
            .message.ai {
                background: var(--ai-msg);
                align-self: flex-start;
                border-bottom-left-radius: 4px;
                white-space: pre-wrap;
            }
            .input-area {
                flex-shrink: 0;
                padding: 20px;
                background: var(--surface);
                display: flex;
                flex-direction: column;
                gap: 10px;
            }
            .config-row {
                display: flex;
                gap: 10px;
            }
            input, button {
                padding: 10px;
                border: 1px solid #45475a;
                border-radius: 6px;
                background: #181825;
                color: var(--text);
                outline: none;
            }
            input:focus {
                border-color: var(--accent);
            }
            .config-row input {
                flex: 1;
            }
            .chat-row {
                display: flex;
                gap: 10px;
            }
            .chat-row input {
                flex: 1;
            }
            button {
                background: var(--accent);
                color: #11111b;
                font-weight: bold;
                border: none;
                cursor: pointer;
                transition: opacity 0.2s;
            }
            button:hover {
                opacity: 0.9;
            }
            button:disabled {
                opacity: 0.5;
                cursor: not-allowed;
            }
            .loading {
                align-self: flex-start;
                color: #7f849c;
                font-size: 0.9em;
                display: none;
            }
            .trace-box {
                flex: 1 1 0;
                min-height: 0;
                padding: 20px;
                overflow-y: auto;
                background: #181825;
                font-family: monospace;
                font-size: 0.9em;
            }
            .trace-step {
                background: var(--surface);
                border-radius: 8px;
                padding: 12px;
                margin-bottom: 12px;
                border-left: 4px solid var(--accent);
            }
            .trace-header {
                font-weight: bold;
                color: var(--accent);
                margin-bottom: 8px;
            }
            .trace-result {
                background: #11111b;
                padding: 12px;
                border-radius: 4px;
                white-space: pre-wrap;
                word-break: break-all;
                border-left: 4px solid #a6e3a1; /* green */
                margin-top: 8px;
                max-height: 400px;
                overflow-y: auto;
            }
            details summary {
                cursor: pointer;
                color: #a6e3a1;
                font-size: 0.9em;
                user-select: none;
            }
            details summary:hover {
                text-decoration: underline;
            }
        </style>
    </head>
    <body>

    <div class="panel">
        <div class="header">
            <h1>LangGraph Agent Chat</h1>
        </div>
        
        <div class="chat-box" id="chat">
            <div class="message ai">Hello! I am connected to the YugenHub LangGraph backend. I can answer operational queries using internal tools.<br><br>Please configure your <b>N8N API Key</b> and <b>Agency ID</b> below to start chatting.</div>
        </div>
        <div class="loading" id="loading">Agent is reasoning with tools...</div>
        
        <div class="input-area">
            <div class="config-row">
                <input type="password" id="apiKey" value="API_KEY_PLACEHOLDER" placeholder="X-API-Key (Backend Integration Key)">
                <input type="text" id="agencyId" placeholder="Agency ID (e.g., default_agency)">
            </div>
            <form class="chat-row" id="chatForm">
                <input type="text" id="query" placeholder="Ask a question... (e.g., How many active projects do we have?)" required>
                <button type="submit" id="sendBtn">Send</button>
            </form>
            <div id="tokenInfo" style="margin-top:5px; color:#a6e3a1; font-size:0.85em;"></div>
        </div>
    </div>

    <div class="panel">
        <div class="header">
            <h1>Tool Execution Trace</h1>
        </div>
        <div class="trace-box" id="traceBox">
            <div style="color: #7f849c; text-align: center; margin-top: 50px;">
                Internal LLM logic and tool executions will appear here.
            </div>
        </div>
    </div>

    <script>
        const chat = document.getElementById('chat');
        const chatForm = document.getElementById('chatForm');
        const queryInput = document.getElementById('query');
        const apiKeyInput = document.getElementById('apiKey');
        const agencyIdInput = document.getElementById('agencyId');
        const sendBtn = document.getElementById('sendBtn');
        const loading = document.getElementById('loading');

        function addMessage(text, sender) {
            const div = document.createElement('div');
            div.className = `message ${sender}`;
            div.textContent = text;
            chat.appendChild(div);
            chat.scrollTop = chat.scrollHeight;
        }

        chatForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const query = queryInput.value.trim();
            const apiKey = apiKeyInput.value.trim();
            const agencyId = agencyIdInput.value.trim();

            if (!query) return;
            if (!apiKey || !agencyId) {
                alert("Please provide the API Key and Agency ID configuration.");
                return;
            }

            addMessage(query, 'user');
            queryInput.value = '';
            queryInput.disabled = true;
            sendBtn.disabled = true;
            loading.style.display = 'block';
            if(document.getElementById('tokenInfo')) {
                document.getElementById('tokenInfo').textContent = '';
            }

            try {
                const response = await fetch(`/api/agent/query?agency_id=${encodeURIComponent(agencyId)}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-API-Key': apiKey
                    },
                    body: JSON.stringify({
                        query: query,
                        include_steps: true
                    })
                });

                if (!response.ok) {
                    const error = await response.json();
                    const errMsg = typeof error.detail === 'string' ? error.detail : JSON.stringify(error.detail);
                    throw new Error(errMsg || `Server returned ${response.status}`);
                }

                const data = await response.json();
                
                let aiText = data.response;
                if (data.usage && data.usage.total_tokens) {
                    aiText += `\n\n[Tokens Used: ${data.usage.total_tokens}]`;
                }
                
                addMessage(aiText, 'ai');
                renderTrace(query, data.steps);
                
            } catch (err) {
                addMessage(`Error: ${err.message}`, 'ai');
            } finally {
                queryInput.disabled = false;
                sendBtn.disabled = false;
                loading.style.display = 'none';
                queryInput.focus();
            }
        });

        function renderTrace(query, steps) {
            const traceBox = document.getElementById('traceBox');
            
            // Clear placeholder if it's the first time
            if (traceBox.innerHTML.includes('text-align: center')) {
                traceBox.innerHTML = '';
            }

            const reqDiv = document.createElement('div');
            reqDiv.innerHTML = `<h3 style="color:#cba6f7; margin-top:20px;">▶ Query: "${query}"</h3>`;
            traceBox.appendChild(reqDiv);

            if (!steps || steps.length === 0) {
                traceBox.innerHTML += `<div class="trace-step" style="border-left-color: #fab387;">No tools executed. Answered directly.</div>`;
            } else {
                steps.forEach(step => {
                    if (step.type === 'tool_call') {
                        traceBox.innerHTML += `
                            <div class="trace-step">
                                <div class="trace-header">⚙️ Tool Call: ${step.tool}</div>
                                <div><b>Input Args:</b> <br><pre style="margin:5px 0 0 0; color:#b4befe;">${JSON.stringify(step.args, null, 2)}</pre></div>
                            </div>
                        `;
                    } else if (step.type === 'tool_result') {
                        let resText = typeof step.result === 'string' ? step.result : JSON.stringify(step.result, null, 2);
                        
                        traceBox.innerHTML += `
                            <div class="trace-step" style="border-left-color: #a6e3a1;">
                                <div class="trace-header" style="color: #a6e3a1;">✅ Output: ${step.tool}</div>
                                <details>
                                    <summary>View JSON Output (${resText.length} characters)</summary>
                                    <div class="trace-result">${resText}</div>
                                </details>
                            </div>
                        `;
                    }
                });
            }
            
            traceBox.scrollTop = traceBox.scrollHeight;
        }
    </script>
    </body>
    </html>
    """
    
    return HTMLResponse(content=html_content.replace("API_KEY_PLACEHOLDER", api_key_value))

