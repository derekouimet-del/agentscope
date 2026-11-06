# Architecture

```
 Adapters (pull/push)            Engine (one pipeline)                       Delivery
 ┌──────────────────┐   Event   ┌───────────────────────────────────────┐   ┌──────────────┐
 │ agent_zero       │──────────▶│ sanitize/redact → enrich (scope, DNS, │──▶│ SQLite store │
 │ docker           │           │ geo) → upsert → alert rules → agent   │   ├──────────────┤
 │ generic_http     │           │ registry → hub                        │──▶│ WebSocket /ws│──▶ React UI
 │ openai_proxy     │           └───────────────────────────────────────┘   │ REST /api/*  │
 │ simulator        │                                                       └──────────────┘
 └──────────────────┘
```

* `backend/agentscope/schema.py` - the standard `Event` (the spec's fields + `evidence`, `risk`, `category`, `task_id`, `parent_event_id`, `input`, `output`). Events are **upserted by `event_id`**: an adapter emits `running`, later re-emits the same id as `completed`/`failed` with a duration, and the UI updates one record.
* `engine.py` - the only place events are processed. Adapters call `engine.emit(event)`; nothing else.
* `adapters/` - one file per integration, auto-discovered; `config_schema` drives the Integrations form, `capabilities` documents what the adapter can honestly observe. Supervised with exponential back-off.
* `alerts.py` - rule kinds with parameters stored in SQLite; alerts only evaluate live events and record the evidence level of the trigger.
* `netintel.py` - host-scope classification, optional DNS resolution, optional geolocation (off by default).
* `store.py` - SQLite (WAL) behind a single worker thread; filtering, search, export, retention.
* `security.py` - roles, password hashing, tokens, credential vault.
* Frontend: React + TypeScript + Vite + Tailwind 4, React Flow (map), d3-force (layout), d3-geo + bundled world-atlas (offline map), Zustand (store; batches WebSocket bursts).

## Agent Zero integration research

Investigated against the Agent Zero v2.15 source inside a running container (`/a0`) plus its docs:

| Mechanism | Finding | Used? |
|---|---|---|
| `POST /api/poll` | Returns **all chat contexts** (id, name, running, paused, `log_guid`, `log_version`, last_message, profile) and the structured log of one context. `log_from` is an *update-version cursor*: items modified after creation (streaming output, tool state) are returned again. Requires login session + `X-CSRF-Token`. | **Yes - primary source** |
| Log item shape | `type` (agent, tool, code_exe, browser, mcp, subagent, response, error, util, text_editor …), `heading`, `content`, `kvps` (`_tool_name`, `action`, `url`, `path`, `runtime`, `code`, `thoughts`, …), `agentno`, `timestamp` | Mapped in `LogMapper` |
| Multi-agent | `agentno` 0 = main agent, N = subordinate at depth N; `call_subordinate` tool carries the delegation | Hierarchy + delegation edges |
| Login | `POST /login` form (`username`,`password`) → session cookie; credentials hash compared server-side | Yes |
| External API (`X-API-KEY`): `/api_message`, `/api_log_get`, `/api_terminate_chat` … | Needs a known `context_id`; cannot list contexts | Not needed (poll covers it) |
| `GET /api/health` | Unauthenticated, version info | Reachability + version |
| `/api/ctx_window_get` | Context-window usage | Best-effort (absent → ignored) |
| `/api/pause` | Pause/resume a chat | Only behind the admin "intervention" permission |
| Python extension hooks (`tool_execute_before/after`, `message_loop_*`, …) | Would give exact timings, arguments and exit information, but means **installing code inside Agent Zero** | Deliberately *not* used (no changes to Agent Zero). Candidate for a future opt-in plugin. |
| Docker | Container is `agentzero…:v2.x`, `/a0` app, `/a0/usr` data volume | Docker adapter |

Side effect to know: `/api/poll` sets Agent Zero's display timezone for the session, hence the adapter's `timezone` setting.

### Limits that follow from the above

1. The log tells us what the agent **asked for / reported**. Tool results appear as `content` text; there are no exit codes. Failure is therefore flagged only through explicit `error` items or an output heuristic, and says so (`metadata.status_basis`).
2. Durations come from log timestamps (first sighting → next item), labelled as such.
3. Agent Zero logs no token counts; the UI says "not exposed". Models are shown only if a framework reports them.
4. Files, processes and sockets touched *inside* the container are not in the log. The Docker adapter provides verified process/file/network-counter data without attribution to a specific agent.

## Evidence levels

`exposed_reasoning` · `tool_call` · `execution` · `inferred` · `simulated` - see README. Alerts and exports carry them through.
