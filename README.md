# AgentScope

Real-time observability and visualization for autonomous AI agents - an "air traffic control" view of what agents are doing: which tools they call, where they browse, which files and commands they touch, which sub-agents they spawn, and what happened five minutes ago.

First supported framework: **[Agent Zero](https://github.com/agent0ai/agent-zero)**. Other frameworks plug in through one small adapter file, the generic HTTP ingest (SDK) or the OpenAI-compatible proxy.

> **Honesty by design.** Every record carries an *evidence level* and the UI never blurs them:
> `VERIFIED` (observed directly: Docker API, process table, proxy) · `REPORTED` (tool call reported by the framework) ·
> `EXPOSED` (progress summary / thought the framework chose to log - *not* hidden reasoning) · `INFERRED` (heuristic, e.g. a URL found inside a script) · `SIMULATED` (demo data).

## Quick start (Docker Compose)

```bash
cd agentscope
cp .env.example .env            # optional: set AGENTSCOPE_ADMIN_PASSWORD, DOCKER_GID
docker compose up -d --build
docker compose logs agentscope  # first run prints the generated admin password
```

Open <http://localhost:8000>, sign in, then **Integrations**:

1. **Add → Agent Zero** - URL (from inside Docker use `http://host.docker.internal:<port>`), username/password if Agent Zero has a login, and the *Docker container name* of that Agent Zero. **Test connection**, then Save.
2. **Add → Docker system monitor** - leave defaults. Containers named in Agent Zero integrations are monitored automatically.
3. No agent handy? Integrations → **Start simulator** (everything it produces is labelled SIMULATED).

## Local development

```bash
# backend (Python 3.11+)
cd backend && python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt pytest
AGENTSCOPE_ADMIN_PASSWORD=change-me-locally uvicorn agentscope.main:app --reload --port 8000
pytest -q tests

# frontend (Node 20+)
cd frontend && npm install && npm run dev        # http://localhost:5173, proxies /api and /ws to :8000
npm run build                                    # type-check + production build
```

Environment: `AGENTSCOPE_DATA_DIR` (SQLite DB + keys, default `./data`), `AGENTSCOPE_ADMIN_USER/PASSWORD`, `AGENTSCOPE_FRONTEND_DIST` (serve a built UI), `AGENTSCOPE_CORS_ORIGINS`, `AGENTSCOPE_TOKEN_TTL`, `AGENTSCOPE_SECRET_KEY` / `AGENTSCOPE_TOKEN_KEY` (override generated keys).

## What you get

| View | What it shows |
|---|---|
| Overview | agents running now, KPIs, latest task journey, containers, live feed, time-travel shortcuts |
| Live Activity Map | agents as nodes, resources (domains, APIs, terminal, code, file directories, DBs, LLMs, processes, containers) as nodes; animated directional links (● read/call, ◆ write, ✕ delete); click anything to inspect; pause / rewind / replay / speed |
| Agents | hierarchy, status, current task/tool, model, tokens, context; **Isolate** one agent across every view |
| Network | internal vs localhost vs Docker vs external, ports, protocols, resolved IPs, durations, bytes where available, world map (opt-in geolocation) |
| Filesystem | expandable tree of touched paths with R/W/D counters, metadata and (opt-in) content viewer |
| Processes | live process tree (Docker `top`), agent-reported commands with output and duration, inferred links between them |
| Timeline / Event History | live and historical events, filters, task grouping, full-text search, **"reconstruct what led to this"**, JSON/CSV export |
| Alerts | configurable rules + severities (new external domain, sensitive paths, unexpected executables, mass file creation, deletions, unusual ports, subprocess bursts, deny-lists) |
| Integrations / Settings | adapters, encrypted credentials, retention, redaction, permissions, users and roles, audit log |

## Security model

* Login required; roles `viewer` < `operator` < `admin`; PBKDF2 password hashes; HMAC-signed tokens; login rate-limiting.
* Integration passwords/keys are encrypted at rest (Fernet) and never returned by the API.
* Everything is redacted (API keys, bearer tokens, passwords, JWTs, cookies, URL credentials, private keys, typed password fields, your own regexes) **before** storage or broadcast.
* Monitoring is read-only. Opt-in capabilities, all off by default, admin-controlled and audit-logged: file-content viewing, the TCP exec probe, pause/resume intervention.
* Configurable retention (default 14 days). Exports are role-gated and audited; CSV is formula-injection safe.
* Mounting the Docker socket is powerful even when `:ro`. For least privilege run a read-only socket proxy (e.g. one that only allows `GET` on containers) and set *Docker host* in the Docker integration.

## Known limitations (stated in the UI too)

* Agent Zero's log is *reported* activity, not syscalls. File/process/network facts that are **verified** come from the Docker API and are attributed to the *container*, not an individual agent.
* Not observable: hidden model reasoning, TLS payloads, DNS queries made by the agent, process exit codes, short-lived processes between samples.
* HTTPS requests show destination metadata only; "reported URL" records come from agent tool arguments.
* Storage is SQLite. PostgreSQL is **not implemented yet**.
* No native OpenCode / OpenClaw / LangGraph / AutoGen / CrewAI adapters yet - use Generic HTTP ingest or the OpenAI proxy ([docs/ADAPTERS.md](docs/ADAPTERS.md)).

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design and the Agent Zero integration research.
