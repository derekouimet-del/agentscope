from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import alerts as alerts_mod
from . import classify, config
from .adapters import REGISTRY, AdapterManager, discover
from .engine import Engine
from .schema import CATEGORIES, Event, iso_to_ts
from .security import Tokens, Vault, hash_password, role_at_least, verify_password
from .store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("agentscope")

store: Store
engine: Engine
manager: AdapterManager
vault = Vault()
tokens = Tokens()
_login_attempts: dict[str, list[float]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store, engine, manager
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    store = Store(config.DB_PATH)
    engine = Engine(store)
    manager = AdapterManager(engine)
    discover()
    await engine.start()
    await bootstrap_admin()
    for integ in await store.list_integrations():
        manager.start(integ, vault)
    log.info("AgentScope ready. Adapters: %s", ", ".join(sorted(REGISTRY)))
    yield
    await manager.stop_all()


app = FastAPI(title="AgentScope", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


async def bootstrap_admin():
    if await store.list_users():
        return
    pw = config.ADMIN_PASSWORD or secrets.token_urlsafe(12)
    await store.put_user(config.ADMIN_USER, hash_password(pw), "admin")
    if config.ADMIN_PASSWORD:
        log.info("Created admin user '%s' from AGENTSCOPE_ADMIN_PASSWORD", config.ADMIN_USER)
    else:
        p = config.DATA_DIR / "initial_admin_password.txt"
        p.write_text(f"username: {config.ADMIN_USER}\npassword: {pw}\n")
        log.warning("=" * 60 + "\nFIRST RUN - generated admin credentials\n  username: %s\n  password: %s\n(also saved to %s - delete after first login)\n" + "=" * 60,
                    config.ADMIN_USER, pw, p)


# ------------------------------------------------------------------ auth
def current_user(request: Request) -> dict:
    h = request.headers.get("authorization", "")
    tok = h[7:] if h.lower().startswith("bearer ") else request.query_params.get("token", "")
    data = tokens.verify(tok) if tok else None
    if not data:
        raise HTTPException(401, "Not authenticated")
    return {"username": data["u"], "role": data["r"]}


def need(role: str):
    def dep(user: dict = Depends(current_user)):
        if not role_at_least(user["role"], role):
            raise HTTPException(403, f"Requires role '{role}'")
        return user
    return dep


class LoginBody(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
async def login(body: LoginBody, request: Request):
    ip = request.client.host if request.client else "?"
    window = [t for t in _login_attempts.get(ip, []) if time.time() - t < 300]
    if len(window) >= 10:
        raise HTTPException(429, "Too many attempts; try again in a few minutes")
    user = await store.get_user(body.username)
    if not user or not verify_password(body.password, user["password_hash"]):
        window.append(time.time())
        _login_attempts[ip] = window
        await store.audit(body.username, "login_failed", ip)
        raise HTTPException(401, "Invalid credentials")
    await store.audit(body.username, "login", ip)
    return {"token": tokens.issue(user["username"], user["role"]), "username": user["username"], "role": user["role"]}


@app.get("/api/auth/me")
async def me(user=Depends(current_user)):
    return user


class UserBody(BaseModel):
    username: str
    password: str
    role: str = "viewer"


@app.get("/api/users")
async def list_users(user=Depends(need("admin"))):
    return await store.list_users()


@app.post("/api/users")
async def put_user(body: UserBody, user=Depends(need("admin"))):
    if body.role not in ("viewer", "operator", "admin") or len(body.password) < 8:
        raise HTTPException(400, "role must be viewer|operator|admin and password at least 8 characters")
    await store.put_user(body.username, hash_password(body.password), body.role)
    await store.audit(user["username"], "user_put", f"{body.username}:{body.role}")
    return {"ok": True}


@app.delete("/api/users/{username}")
async def del_user(username: str, user=Depends(need("admin"))):
    if username == user["username"]:
        raise HTTPException(400, "cannot delete yourself")
    await store.delete_user(username)
    await store.audit(user["username"], "user_delete", username)
    return {"ok": True}


# ------------------------------------------------------------------ overview / agents
@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/api/overview")
async def overview(user=Depends(current_user)):
    o = engine.overview()
    o["db"] = await store.stats()
    o["alerts_unacked"] = len(await store.list_alerts(500, unacked=True))
    o["containers"] = {k: {kk: vv for kk, vv in v.items() if kk != "history"} for k, v in engine.metrics.items()}
    since = time.time() - 300
    o["recent"] = {"events_5m": await store.count_events({"since": since}),
                   "failures_5m": await store.count_events({"since": since, "status": "failure"}),
                   "running_ops": await store.count_events({"status": "running", "since": time.time() - 300,
                                                            "event_type": "tool_execution,llm_call,file_operation,delegation,network"})}
    net = await store.query_events({"category": "network,web,api", "since": since}, 2000)
    o["recent"]["network_5m"] = len([e for e in net if (e.get("metadata") or {}).get("network")])
    o["settings"] = {k: engine.settings[k] for k in ("geoip_provider", "allow_exec_probe", "allow_file_content_view", "allow_intervention")}
    return o


@app.get("/api/agents")
async def agents(user=Depends(current_user)):
    return sorted((engine.agent_view(a) for a in engine.agents.values()), key=lambda a: -a["last_seen"])


@app.get("/api/agents/{agent_id:path}")
async def agent_detail(agent_id: str, user=Depends(current_user)):
    a = engine.agents.get(agent_id)
    if not a:
        raise HTTPException(404)
    recent = await store.query_events({"agent": agent_id}, 100)
    return {"agent": engine.agent_view(a), "recent": recent,
            "children": [engine.agent_view(x) for x in engine.agents.values() if x.parent_agent_id == agent_id]}


@app.delete("/api/agents/{agent_id:path}")
async def forget_agent(agent_id: str, user=Depends(need("admin"))):
    engine.agents.pop(agent_id, None)
    return {"ok": True}


# ------------------------------------------------------------------ events
FILTERS = ["agent", "event_type", "category", "tool", "risk", "source", "instance", "evidence", "task", "status", "destination",
           "path", "domain", "process", "ip", "q"]


def _filters(request: Request, since: float | None, until: float | None) -> dict:
    f: dict[str, Any] = {k: request.query_params.get(k) for k in FILTERS if request.query_params.get(k)}
    if since is not None:
        f["since"] = since
    if until is not None:
        f["until"] = until
    return f


@app.get("/api/events")
async def events(request: Request, since: float | None = None, until: float | None = None, limit: int = Query(300, le=5000),
                 offset: int = 0, order: str = "desc", user=Depends(current_user)):
    f = _filters(request, since, until)
    return {"events": await store.query_events(f, limit, offset, order), "total": await store.count_events(f)}


@app.get("/api/events/{event_id}")
async def event_detail(event_id: str, user=Depends(current_user)):
    ev = await store.get_event(event_id)
    if not ev:
        raise HTTPException(404)
    return ev


@app.get("/api/events/{event_id}/trace")
async def event_trace(event_id: str, user=Depends(current_user)):
    """Reconstruct what led to this event: task chain, ancestors, same-agent history and derived children."""
    ev = await store.get_event(event_id)
    if not ev:
        raise HTTPException(404)
    ts = iso_to_ts(ev["timestamp"])
    chain, seen, cur = [], {event_id}, ev
    while cur.get("parent_event_id") and cur["parent_event_id"] not in seen:
        parent = await store.get_event(cur["parent_event_id"])
        if not parent:
            break
        seen.add(parent["event_id"])
        chain.append(parent)
        cur = parent
    task = []
    if ev.get("task_id"):
        task = [e for e in await store.query_events({"task": ev["task_id"], "until": ts}, 200, 0, "asc") if e["event_id"] != event_id]
    agent_hist = [e for e in await store.events_for_agent_before(ev["agent_id"], ts, 30) if e["event_id"] != event_id]
    children = await store.query_events({"parent_event": event_id}, 100, 0, "asc")
    parent_agent = ev.get("parent_agent_id")
    delegation = []
    if parent_agent:
        delegation = [e for e in await store.events_for_agent_before(parent_agent, ts, 80) if e["event_type"] in ("delegation", "task")][-5:]
    after = await store.query_events({"agent": ev["agent_id"], "since": ts}, 10, 0, "asc")
    return {"event": ev, "ancestors": chain[::-1], "task_events": task, "agent_history": agent_hist, "children": children,
            "delegation_context": delegation, "next": [e for e in after if e["event_id"] != event_id][:8]}


@app.get("/api/tasks")
async def tasks(user=Depends(current_user), limit: int = 50):
    rows = await store.query_events({"event_type": "task"}, limit * 3)
    out: dict[str, dict] = {}
    for e in reversed(rows):
        tid = e.get("task_id") or e["event_id"]
        t = out.setdefault(tid, {"task_id": tid, "agent_id": e["agent_id"], "title": None, "started": None, "finished": None,
                                 "status": "running", "duration_ms": None, "instance_id": e.get("instance_id")})
        if e["action"] == "task_received":
            t["title"], t["started"] = e.get("target"), e["timestamp"]
        elif e["action"] == "task_completed":
            t["finished"], t["status"], t["duration_ms"] = e["timestamp"], "completed", e.get("duration_ms")
    return sorted(out.values(), key=lambda t: t["started"] or "", reverse=True)[:limit]


@app.get("/api/export")
async def export(request: Request, format: str = "json", since: float | None = None, until: float | None = None,
                 user=Depends(need("operator"))):
    f = _filters(request, since, until)
    rows = await store.query_events(f, 50000, 0, "asc")
    await store.audit(user["username"], "export", f"{format} {len(rows)} rows {json.dumps(f)}")
    if format == "csv":
        buf = io.StringIO()
        cols = ["event_id", "timestamp", "agent_id", "parent_agent_id", "event_type", "action", "category", "target", "tool", "status",
                "duration_ms", "risk", "evidence", "source", "instance_id", "task_id", "input", "output", "metadata"]
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            r = dict(r)
            r["metadata"] = json.dumps(r.get("metadata") or {}, default=str)
            for k in ("input", "output", "target"):
                v = r.get(k)
                if isinstance(v, str) and v[:1] in "=+-@":   # CSV formula injection guard
                    r[k] = "'" + v
            w.writerow(r)
        return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=agentscope-events.csv"})
    return Response(json.dumps(rows, default=str, indent=1), media_type="application/json",
                    headers={"Content-Disposition": "attachment; filename=agentscope-events.json"})


# ------------------------------------------------------------------ network / fs / processes
@app.get("/api/network")
async def network(since: float | None = None, user=Depends(current_user)):
    since = since if since is not None else time.time() - 6 * 3600
    rows = await store.query_events({"category": "network,web,api", "since": since}, 5000, 0, "asc")
    groups: dict[str, dict] = {}
    for e in rows:
        net = (e.get("metadata") or {}).get("network")
        if not net:
            continue
        host, port = net.get("host") or net.get("ip") or "?", net.get("port")
        key = f"{host}:{port}"
        g = groups.setdefault(key, {"key": key, "host": host, "ip": net.get("ip"), "port": port, "protocol": net.get("protocol"),
                                    "scope": net.get("scope"), "count": 0, "first": e["timestamp"], "last": e["timestamp"],
                                    "duration_ms": 0, "bytes_in": None, "bytes_out": None, "geo": None, "evidence": [], "visibility": [],
                                    "agents": [], "encrypted": net.get("encrypted"), "failed": 0, "last_event": e["event_id"],
                                    "resolved_ips": net.get("resolved_ips"), "directions": []})
        g["count"] += 1
        g["last"], g["last_event"] = e["timestamp"], e["event_id"]
        g["ip"] = g["ip"] or net.get("ip")
        g["geo"] = g["geo"] or net.get("geo")
        g["duration_ms"] += e.get("duration_ms") or 0
        g["failed"] += 1 if e.get("status") == "failed" else 0
        for k in ("bytes_in", "bytes_out"):
            if net.get(k) is not None:
                g[k] = (g[k] or 0) + net[k]
        for lst, v in ((g["evidence"], e.get("evidence")), (g["visibility"], net.get("visibility")),
                       (g["agents"], e["agent_id"]), (g["directions"], net.get("direction"))):
            if v and v not in lst:
                lst.append(v)
    conns = sorted(groups.values(), key=lambda g: g["last"], reverse=True)
    scopes: dict[str, int] = {}
    for g in conns:
        scopes[g["scope"] or "unknown"] = scopes.get(g["scope"] or "unknown", 0) + g["count"]
    return {"destinations": conns, "scopes": scopes, "containers": {k: {kk: vv for kk, vv in v.items() if kk != "history"} for k, v in engine.metrics.items()},
            "geo_provider": engine.settings.get("geoip_provider"), "notes": [
                "HTTPS/TLS payloads are never observable. 'reported_url_only' records come from tool arguments reported by the agent framework.",
                "'connection_metadata' records are socket-table samples (opt-in probe). DNS queries made by the agent are not observable; resolved IPs are AgentScope's own lookup.",
            ]}


@app.get("/api/fs/files")
async def fs_files(since: float | None = None, instance: str | None = None, user=Depends(current_user)):
    since = since if since is not None else time.time() - 24 * 3600
    f: dict[str, Any] = {"category": "file_read,file_write,file_delete", "since": since}
    if instance:
        f["instance"] = instance
    rows = await store.query_events(f, 5000, 0, "asc")
    files: dict[str, dict] = {}
    for e in rows:
        p = e.get("target")
        if not p or p.startswith("("):
            continue
        d = files.setdefault(p, {"path": p, "reads": 0, "writes": 0, "creates": 0, "deletes": 0, "last": e["timestamp"], "size": None,
                                 "agents": [], "evidence": [], "last_event": e["event_id"], "containers": [], "is_dir": False})
        a = e["action"]
        d["reads"] += a in ("file_read", "directory_access")
        d["writes"] += a in ("file_write", "file_modify")
        d["creates"] += a == "file_create"
        d["deletes"] += a == "file_delete"
        d["last"], d["last_event"] = e["timestamp"], e["event_id"]
        md = e.get("metadata") or {}
        d["size"] = md.get("size", d["size"])
        d["is_dir"] = d["is_dir"] or bool(md.get("is_dir")) or a == "directory_access"
        if md.get("container") and md["container"] not in d["containers"]:
            d["containers"].append(md["container"])
        for lst, v in ((d["agents"], e["agent_id"]), (d["evidence"], e["evidence"])):
            if v not in lst:
                lst.append(v)
    return {"files": sorted(files.values(), key=lambda x: x["last"], reverse=True)}


def _docker_adapters():
    return [a for a in manager.instances.values() if a.framework == "docker" and a.client]


@app.get("/api/fs/inspect")
async def fs_inspect(container: str, path: str, content: bool = False, user=Depends(need("operator"))):
    ads = _docker_adapters()
    if not ads:
        raise HTTPException(409, "No connected Docker monitor integration")
    ad = ads[0]
    try:
        stat = await ad.stat_path(container, path)
        out: dict[str, Any] = {"stat": stat}
        if content:
            if not engine.settings.get("allow_file_content_view"):
                raise HTTPException(403, "File content viewing is disabled (Settings > Permissions)")
            if classify.sensitive_path_hit(path, engine.settings.get("sensitive_paths")) and not role_at_least(user["role"], "admin"):
                raise HTTPException(403, "Sensitive path: admin role required")
            data = await ad.read_file(container, path)
            data["content"] = engine.redactor.text(data["content"]) if data["content"] else data["content"]
            out["file"] = data
            await store.audit(user["username"], "file_view", f"{container}:{path}")
        return out
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(404, f"{type(e).__name__}: {e}")


@app.get("/api/processes")
async def processes(user=Depends(current_user)):
    snaps = []
    for name, s in engine.process_snapshots.items():
        procs = []
        for p in s["processes"]:
            q = dict(p)
            q["cmd"] = engine.redactor.text(p["cmd"], 1000)
            procs.append(q)
        snaps.append({**s, "processes": procs})
    cmds = await store.query_events({"category": "terminal,python,subprocess", "since": time.time() - 3600}, 400, 0, "desc")
    return {"containers": snaps, "commands": cmds}


# ------------------------------------------------------------------ commands
def _command_rows(rows: list[dict]) -> list[dict]:
    out = []
    for e in rows:
        if e.get("event_type") not in ("tool_execution", "process") and e.get("category") not in ("terminal", "python"):
            continue
        if not (e.get("input") or e.get("target")):
            continue
        cmd = e.get("input") or e.get("target") or ""
        runtime = "python" if e.get("category") == "python" else ("nodejs" if e.get("tool") == "node" else "terminal")
        out.append({"event_id": e["event_id"], "timestamp": e["timestamp"], "agent_id": e["agent_id"], "agent_name": e.get("agent_name"),
                    "tool": e.get("tool"), "runtime": runtime, "command": cmd, "status": e["status"], "duration_ms": e.get("duration_ms"),
                    "output": e.get("output"), "evidence": e["evidence"], "source": e["source"], "risk": e["risk"]})
    return out


@app.get("/api/commands")
async def commands(since: float | None = None, limit: int = Query(300, le=2000), agent: str | None = None,
                   q: str | None = None, user=Depends(current_user)):
    f: dict[str, Any] = {"category": "terminal,python", "since": since if since is not None else time.time() - 24 * 3600}
    if agent:
        f["agent"] = agent
    if q:
        f["q"] = q
    rows = await store.query_events(f, limit, 0, "asc")
    return {"commands": _command_rows(rows)}


class ExplainBody(BaseModel):
    command: str
    runtime: str = "terminal"


@app.post("/api/explain")
async def explain_cmd(body: ExplainBody, user=Depends(current_user)):
    from .explain import explain
    return explain(body.command[:20000], body.runtime)


@app.get("/api/commands/script", response_class=Response)
async def commands_script(since: float | None = None, agent: str | None = None, user=Depends(need("operator"))):
    """Plain shell script of recorded commands, ready to paste into a terminal. Review before running."""
    f: dict[str, Any] = {"category": "terminal,python", "since": since if since is not None else time.time() - 24 * 3600}
    if agent:
        f["agent"] = agent
    cmds = _command_rows(await store.query_events(f, 2000, 0, "asc"))
    lines = ["#!/usr/bin/env bash", "# Commands recorded by AgentScope.", "# Review every line before running. Commands that were redacted contain [REDACTED]", "# and must be completed with your own values first.", ""]
    for c in cmds:
        lines.append(f"# {c['timestamp']} · {c['agent_name'] or c['agent_id']} · {c['status']}")
        if c["runtime"] == "python":
            lines.append("python3 - <<'PYEOF'")
            lines.append(c["command"])
            lines.append("PYEOF")
        else:
            lines.append(c["command"])
        lines.append("")
    return Response("\n".join(lines), media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=agentscope-commands.sh"})


# ------------------------------------------------------------------ alerts
@app.get("/api/alerts")
async def list_alerts(unacked: bool = False, user=Depends(current_user)):
    return await store.list_alerts(300, unacked)


@app.post("/api/alerts/ack")
async def ack(id: str | None = None, user=Depends(need("operator"))):
    await store.ack_alert(id)
    engine.hub.publish({"type": "alert_ack", "id": id})
    return {"ok": True}


@app.get("/api/rules")
async def rules(user=Depends(current_user)):
    return list(engine.alerts.rules.values())


class RuleBody(BaseModel):
    id: str | None = None
    name: str
    kind: str
    enabled: bool = True
    severity: str = "medium"
    params: dict[str, Any] = {}
    description: str = ""


@app.put("/api/rules/{rule_id}")
async def put_rule(rule_id: str, body: RuleBody, user=Depends(need("admin"))):
    if body.kind not in alerts_mod.RULE_KINDS or body.severity not in alerts_mod.SEVERITIES:
        raise HTTPException(400, "unknown rule kind or severity")
    rule = body.model_dump()
    rule["id"] = rule_id
    await store.put_rule(rule)
    engine.alerts.rules[rule_id] = rule
    await store.audit(user["username"], "rule_put", rule_id)
    return rule


@app.post("/api/rules")
async def new_rule(body: RuleBody, user=Depends(need("admin"))):
    return await put_rule("custom-" + uuid.uuid4().hex[:6], body, user)


@app.delete("/api/rules/{rule_id}")
async def del_rule(rule_id: str, user=Depends(need("admin"))):
    await store.delete_rule(rule_id)
    engine.alerts.rules.pop(rule_id, None)
    return {"ok": True}


# ------------------------------------------------------------------ integrations
@app.get("/api/frameworks")
async def frameworks(user=Depends(current_user)):
    return [{"framework": c.framework, "display_name": c.display_name, "description": c.description, "mode": c.mode,
             "config_schema": c.config_schema, "capabilities": c.capabilities} for c in REGISTRY.values()]


def _integ_view(i: dict) -> dict:
    ad = manager.instances.get(i["id"])
    return {**i, "config": Vault.mask_config(i["config"]), "status": ad.status() if ad else {"state": "stopped", "message": "disabled" if not i["enabled"] else ""}}


@app.get("/api/integrations")
async def integrations(user=Depends(current_user)):
    return [_integ_view(i) for i in await store.list_integrations()]


class IntegBody(BaseModel):
    framework: str
    name: str
    config: dict[str, Any] = {}
    enabled: bool = True


async def _save_integration(id: str, body: IntegBody, user) -> dict:
    cls = REGISTRY.get(body.framework)
    if not cls:
        raise HTTPException(400, "unknown framework")
    old = next((i for i in await store.list_integrations() if i["id"] == id), None)
    cfg = {f["name"]: f.get("default") for f in cls.config_schema}
    cfg.update(body.config)
    for k, v in list(cfg.items()):
        if v == "********" and old:
            cfg[k] = old["config"].get(k, "")        # keep stored secret when UI sends the mask back
        if k == "api_key" and cls.mode == "push" and not cfg[k]:
            cfg[k] = "as_" + secrets.token_urlsafe(24)
    enc = vault.encrypt_config(cfg)
    await store.put_integration(id, body.framework, body.name, enc, body.enabled)
    await store.audit(user["username"], "integration_put", f"{id}:{body.framework}")
    integ = {"id": id, "framework": body.framework, "name": body.name, "config": enc, "enabled": body.enabled}
    manager.start(integ, vault)
    return _integ_view(integ)


@app.post("/api/integrations")
async def add_integration(body: IntegBody, user=Depends(need("admin"))):
    return await _save_integration(body.framework.replace("_", "-") + "-" + uuid.uuid4().hex[:5], body, user)


@app.put("/api/integrations/{id}")
async def put_integration(id: str, body: IntegBody, user=Depends(need("admin"))):
    return await _save_integration(id, body, user)


@app.delete("/api/integrations/{id}")
async def del_integration(id: str, purge: bool = False, user=Depends(need("admin"))):
    await manager.stop(id)
    await store.delete_integration(id)
    if purge:
        await store.clear_source(id)
    await store.audit(user["username"], "integration_delete", id)
    return {"ok": True}


@app.post("/api/integrations/test")
async def test_integration(body: IntegBody, id: str | None = None, user=Depends(need("admin"))):
    cls = REGISTRY.get(body.framework)
    if not cls:
        raise HTTPException(400, "unknown framework")
    cfg = {f["name"]: f.get("default") for f in cls.config_schema}
    cfg.update(body.config)
    if id:
        old = next((i for i in await store.list_integrations() if i["id"] == id), None)
        if old:
            stored = vault.decrypt_config(old["config"])
            for k, v in cfg.items():
                if v == "********":
                    cfg[k] = stored.get(k, "")
    return await cls("test", body.name, cfg, engine).test()


@app.get("/api/integrations/{id}/connect-info")
async def connect_info(id: str, user=Depends(need("admin"))):
    """Admin-only: the key + URLs a push-style client needs (audit-logged)."""
    integ = next((i for i in await store.list_integrations() if i["id"] == id), None)
    if not integ or integ["framework"] not in ("generic_http", "openai_proxy"):
        raise HTTPException(404)
    key = vault.decrypt_config(integ["config"]).get("api_key", "")
    await store.audit(user["username"], "reveal_key", id)
    if integ["framework"] == "generic_http":
        return {"key": key, "ingest_path": f"/api/ingest/{id}", "header": "X-AgentScope-Key"}
    return {"key": key, "base_url_path": f"/proxy/{id}/{key}/v1"}


@app.post("/api/integrations/{id}/intervene")
async def intervene(id: str, action: str, context_id: str, user=Depends(need("admin"))):
    if not engine.settings.get("allow_intervention"):
        raise HTTPException(403, "Active intervention is disabled. Enable it explicitly in Settings > Permissions.")
    ad = manager.instances.get(id)
    if not ad or not hasattr(ad, "intervene"):
        raise HTTPException(400, "integration does not support intervention")
    await store.audit(user["username"], "intervene", f"{id} {action} {context_id}")
    try:
        res = await ad.intervene(action, context_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e))
    return {"ok": True, "result": res}


@app.post("/api/simulator/{state}")
async def simulator(state: str, user=Depends(need("admin"))):
    """Convenience: create/enable or disable/clear the simulator."""
    existing = next((i for i in await store.list_integrations() if i["framework"] == "simulator"), None)
    if state == "start":
        body = IntegBody(framework="simulator", name="Simulator (demo)", config={}, enabled=True)
        return await _save_integration(existing["id"] if existing else "simulator", body, user)
    if state in ("stop", "clear"):
        if existing:
            await manager.stop(existing["id"])
            await store.put_integration(existing["id"], "simulator", existing["name"], existing["config"], False)
        if state == "clear":
            await store.clear_source("simulator")
            await store.delete_agents("simulator")
            for k in [k for k, a in engine.agents.items() if a.source == "simulator"]:
                engine.agents.pop(k)
            engine.hub.publish({"type": "resync"})
        return {"ok": True}
    raise HTTPException(400)


# ------------------------------------------------------------------ settings / audit
@app.get("/api/settings")
async def get_settings(user=Depends(current_user)):
    return engine.settings


@app.put("/api/settings")
async def put_settings(body: dict[str, Any], user=Depends(need("admin"))):
    for k, v in body.items():
        if k in config.DEFAULT_SETTINGS and type(v) is type(config.DEFAULT_SETTINGS[k]):
            engine.settings[k] = v
            await store.set_setting(k, v)
    engine.apply_settings()
    await store.audit(user["username"], "settings_put", json.dumps({k: body[k] for k in body if k in config.DEFAULT_SETTINGS}))
    return engine.settings


@app.get("/api/audit")
async def audit(user=Depends(need("admin"))):
    return await store.list_audit()


# ------------------------------------------------------------------ ingest + proxy (key-authenticated, not user tokens)
@app.post("/api/ingest/{integration_id}")
async def ingest(integration_id: str, request: Request):
    ad = manager.instances.get(integration_id)
    if not ad or ad.framework != "generic_http":
        raise HTTPException(404)
    if not ad.check_key(request.headers.get("x-agentscope-key")):
        raise HTTPException(401, "bad ingest key")
    body = await request.json()
    return await ad.ingest(body if isinstance(body, list) else [body])


@app.api_route("/proxy/{integration_id}/{key}/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def openai_proxy(integration_id: str, key: str, path: str, request: Request):
    ad = manager.instances.get(integration_id)
    if not ad or ad.framework != "openai_proxy" or not ad.http:
        raise HTTPException(404)
    if not ad.check_key(key):
        raise HTTPException(401, "bad proxy key")
    return await ad.handle(request, path)


# ------------------------------------------------------------------ websocket
@app.websocket("/ws")
async def ws(sock: WebSocket):
    data = tokens.verify(sock.query_params.get("token", ""))
    if not data:
        await sock.close(code=4401)
        return
    await sock.accept()
    q = engine.hub.subscribe()
    try:
        await sock.send_json({"type": "hello", "user": data["u"], "role": data["r"],
                              "agents": [engine.agent_view(a) for a in engine.agents.values()],
                              "adapters": engine.adapter_status,
                              "metrics": {k: {kk: vv for kk, vv in v.items() if kk != "history"} for k, v in engine.metrics.items()},
                              "server_time": time.time()})

        async def pump():
            while True:
                msg = await q.get()
                await sock.send_text(json.dumps(msg, default=str))

        async def beat():
            while True:
                await asyncio.sleep(15)
                await sock.send_json({"type": "ping", "t": time.time(), "overview": engine.overview()})

        async def recv():
            while True:
                await sock.receive_text()

        tasks = [asyncio.create_task(f()) for f in (pump, beat, recv)]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
    except WebSocketDisconnect:
        pass
    except Exception:
        log.debug("ws closed", exc_info=True)
    finally:
        engine.hub.unsubscribe(q)


# ------------------------------------------------------------------ static frontend
if config.FRONTEND_DIST and config.FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        if full_path.startswith(("api/", "proxy/", "ws")):
            raise HTTPException(404)
        f = (config.FRONTEND_DIST / full_path).resolve()
        if full_path and f.is_file() and str(f).startswith(str(config.FRONTEND_DIST.resolve())):
            return FileResponse(f)
        return FileResponse(config.FRONTEND_DIST / "index.html")
