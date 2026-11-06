# Writing adapters / connecting other frameworks

## 1. Generic HTTP ingest (any framework, today)

Integrations → Add → *Generic HTTP ingest*. Reveal the key, then POST events:

```bash
curl -X POST http://localhost:8000/api/ingest/<integration-id> \
  -H "X-AgentScope-Key: <key>" -H "Content-Type: application/json" \
  -d '[{"agent_id":"researcher","event_type":"tool_execution","action":"browser_navigation",
        "target":"https://example.com","tool":"browser","status":"completed","duration_ms":812,
        "source":"langgraph"}]'
```

Required: `agent_id`, `event_type` (`agent_lifecycle task tool_execution llm_call delegation message reasoning file_operation network process system error`), `action`, `source` (defaults to the integration's source label).
Useful: `parent_agent_id`, `target`, `tool`, `status` (`pending running completed failed blocked info`), `duration_ms`, `input`, `output`, `task_id`, `parent_event_id`, `evidence`, `metadata`.
Re-sending an event with the **same `event_id`** updates it (send `running`, then `completed`).

Action names the UI understands: `browser_navigation web_search http_request api_call mcp_call terminal_command python_execution file_read file_write file_create file_modify file_delete directory_access process_spawn delegate_task agent_message database_query llm_call reasoning task_received task_completed memory_op`. Anything else is shown as a generic tool.

`metadata.network` (`host`, `ip`, `port`, `protocol`, `direction`, `bytes_in`, `bytes_out`) enriches the Network view. Declare honest `evidence`: `tool_call` for what your framework reports, `execution` only if you actually observed it.

Python helper: `sdk/agentscope_client.py` (see its docstring). Callback sketches:

```python
# LangGraph / LangChain
from langchain_core.callbacks import BaseCallbackHandler
class Scope(BaseCallbackHandler):
    def __init__(self, s): self.s = s
    def on_tool_start(self, serialized, input_str, *, run_id, **kw):
        self.s.event(serialized.get("name", "tool"), target=str(input_str)[:200], status="running", event_id=str(run_id), tool=serialized.get("name"))
    def on_tool_end(self, output, *, run_id, **kw):
        self.s.event("tool", status="completed", event_id=str(run_id), output=str(output)[:1000])
```

AutoGen / CrewAI work the same way from their message / step callbacks (`step_callback` in CrewAI). OpenCode and OpenClaw: forward their plugin/event stream to the same endpoint. None of these have a native adapter yet.

## 2. OpenAI-compatible proxy (custom agents)

Add *OpenAI-compatible proxy*, set the upstream (OpenAI, Ollama, LM Studio, vLLM, OpenRouter …), reveal the connection details and set the agent's `OPENAI_BASE_URL` to `http://<agentscope>/proxy/<id>/<key>/v1`. Each call becomes an `llm_call` with model, duration, token usage and the tool calls the model **requested** (execution of those tools is not observed). The client's own `Authorization` header is forwarded upstream and never stored. Streaming is supported.

## 3. A native adapter (one file)

Create `backend/agentscope/adapters/my_framework.py`:

```python
from ..schema import Event
from .base import Adapter, register

@register
class MyFramework(Adapter):
    framework = "my_framework"
    display_name = "My Framework"
    description = "..."
    config_schema = [{"name": "url", "label": "URL", "type": "text", "default": "http://localhost:9000", "required": True}]
    capabilities = {"tool_calls": "reported via its event API", "files": "not visible"}

    async def run(self):                       # raise to be restarted with back-off
        self.set_status("running", "connected")
        async for item in my_event_stream(self.config["url"]):
            await self.emit(Event(agent_id=f"mf:{item.agent}", event_type="tool_execution",
                                  action="terminal_command", target=item.cmd, status="running",
                                  source=self.framework, event_id=item.id))

    async def test(self): return {"ok": True, "message": "reachable"}
```

That is all - it appears under Integrations (form generated from `config_schema`), is supervised, and flows through redaction, storage, alerts and the UI. Guidelines: keep event ids stable, set `evidence` truthfully, never copy secrets into `metadata`, and prefer read-only documented endpoints over modifying the monitored framework.
