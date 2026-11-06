"""Agent Zero adapter.

Integration mechanism (verified against Agent Zero v2.x source and a running container):

* Authentication: optional web login (`POST /login`, form fields username/password -> session
  cookie) plus a CSRF token from `GET /api/csrf_token` sent as `X-CSRF-Token`.
* Data: `POST /api/poll` returns every chat context plus the *structured* log of one context.
  `log_from` is an update-version cursor, so items that are modified after creation (streaming
  tool output, state changes) are delivered again - we upsert them by a stable event id.
* Optional: `POST /api/ctx_window_get` for context-window usage, `POST /api/pause` for the
  explicitly-enabled intervention feature.

What this adapter can and cannot know (also shown in the UI):

* Log items are what Agent Zero *reports* (`tool_call` evidence). They are not syscalls.
* URLs / file deletions found inside code the agent ran are *inferred*.
* Verified file/process/network facts come from the Docker adapter, not from this one.
* Hidden model reasoning is not accessible; only what Agent Zero places in its log
  (thoughts/headline fields) is shown, and it is labelled `exposed_reasoning`.
* `/api/poll` sets Agent Zero's display timezone for the session; configure `timezone` to match yours.
"""
from __future__ import annotations

import asyncio
import re
import shlex
import time
from typing import Any

import httpx

from .. import classify
from ..schema import Event, ts_to_iso
from .base import Adapter, register

TOOL_ACTIONS = {
    "browser": "browser_navigation", "browser_agent": "browser_navigation", "browser_open": "browser_navigation",
    "browser_do": "browser_action", "search_engine": "web_search", "searxng": "web_search",
    "call_subordinate": "delegate_task", "text_editor": "file_read", "document_query": "file_read",
    "memory_save": "memory_op", "memory_load": "memory_op", "memory_delete": "memory_op",
    "memory_forget": "memory_op", "knowledge_tool": "memory_op", "behaviour_adjustment": "memory_op",
    "vision_load": "file_read", "response": "task_completed",
}
EDITOR_ACTIONS = {"read": "file_read", "view": "file_read", "write": "file_write", "create": "file_create",
                  "patch": "file_modify", "edit": "file_modify", "update": "file_modify", "delete": "file_delete",
                  "remove": "file_delete", "append": "file_modify"}
RUNTIME_ACTIONS = {"terminal": ("terminal_command", "terminal"), "python": ("python_execution", "python"),
                   "nodejs": ("nodejs_execution", "node"), "output": ("terminal_output", "terminal"),
                   "reset": ("terminal_reset", "terminal")}
SENSITIVE_FIELD = re.compile(r"passw|secret|token|api[_-]?key|credential|\bpin\b|2fa|otp", re.I)
FAIL_HINT = re.compile(r"^\s*(error\b|.{0,60}\b(failed|not found|no such file|permission denied|timed out)\b)", re.I)
HEADING_PREFIX = re.compile(r"^icon://\S+\s*")


def clean_heading(h: str | None) -> str:
    return HEADING_PREFIX.sub("", h or "").strip()


def _s(v: Any, n: int = 4000) -> str | None:
    if v is None:
        return None
    if not isinstance(v, str):
        import json
        try:
            v = json.dumps(v, default=str, ensure_ascii=False)
        except Exception:
            v = str(v)
    return v[:n]


def _rm_targets(cmd: str) -> list[str]:
    out: list[str] = []
    for seg in re.split(r"(?:&&|\|\||;|\|)", cmd):
        try:
            toks = shlex.split(seg.strip())
        except ValueError:
            continue
        if toks and toks[0] in ("sudo",):
            toks = toks[1:]
        if toks and toks[0] in ("rm", "rmdir", "unlink", "shred"):
            out += [t for t in toks[1:] if not t.startswith("-")]
    return out[:5]


class LogMapper:
    """Pure translation of Agent Zero log items -> AgentScope events (unit-testable)."""

    def __init__(self, instance_id: str, ctx_id: str, ctx_name: str, source: str = "agent_zero"):
        self.instance, self.ctx, self.ctx_name, self.source = instance_id, ctx_id, ctx_name, source
        self.current_task: str | None = None
        self.items: dict[int, dict] = {}  # no -> first-seen info for duration/completion tracking

    def agent_id(self, n: int) -> str:
        return f"az:{self.instance}:{self.ctx}:A{n}"

    def agent_name(self, n: int) -> str:
        base = f"A{n}" + (" (main)" if n == 0 else " (subordinate)")
        return f"{base} · {self.ctx_name}" if self.ctx_name else base

    def parent_of(self, n: int) -> str | None:
        return self.agent_id(n - 1) if n > 0 else None

    def _base(self, item: dict, **kw) -> Event:
        n = int(item.get("agentno") or 0)
        ts = float(item.get("timestamp") or time.time())
        return Event(
            event_id=f"az:{self.instance}:{self.ctx}:{item['no']}", timestamp=ts_to_iso(ts),
            agent_id=self.agent_id(n), parent_agent_id=self.parent_of(n), agent_name=self.agent_name(n),
            source=self.source, instance_id=self.instance, task_id=self.current_task, **kw,
        )

    def map(self, item: dict, ctx_running: bool = True) -> list[Event]:
        typ = item.get("type") or ""
        kv = item.get("kvps") or {}
        heading = clean_heading(item.get("heading"))
        content = item.get("content") or ""
        n = int(item.get("agentno") or 0)
        no = item["no"]
        meta: dict[str, Any] = {"az_log_type": typ, "az_log_no": no, "context_id": self.ctx, "context_name": self.ctx_name,
                                "agent_number": n}
        events: list[Event] = []

        if typ == "user":
            if n == 0:
                self.current_task = f"az:{self.instance}:{self.ctx}:{no}"
                ev = self._base(item, event_type="task", action="task_received", target=_s(content, 300), status="running",
                                evidence="tool_call", input=content)
                ev.task_id = self.current_task
                ev.metadata = {**meta, "attachments": kv.get("attachments")}
            else:
                ev = self._base(item, event_type="message", action="agent_message", target=f"instruction from {self.agent_id(n-1).split(':')[-1]}",
                                status="completed", input=content)
                ev.metadata = meta
            events.append(ev)

        elif typ == "agent":
            thoughts = kv.get("thoughts") or kv.get("reasoning")
            if "calling llm" in heading.lower() and not thoughts:
                ev = self._base(item, event_type="llm_call", action="llm_call", target=heading, tool="llm", status="running")
                ev.metadata = meta
                events.append(ev)
            else:
                text = "\n".join(map(str, thoughts)) if isinstance(thoughts, list) else _s(thoughts)
                ev = self._base(item, event_type="reasoning", action="reasoning", target=_s(kv.get("headline") or heading or "progress", 200),
                                status="completed", evidence="exposed_reasoning", output=text or content or None)
                sel = {k: kv.get(k) for k in ("tool_name", "tool_args") if kv.get(k) is not None}
                ev.metadata = {**meta, "tool_selection": sel or None,
                               "note": "Exposed progress summary/thoughts as written by Agent Zero - not hidden model reasoning."}
                events.append(ev)

        elif typ in ("tool", "mcp", "browser"):
            events += self._tool(item, kv, heading, content, meta, typ)

        elif typ == "code_exe":
            events += self._code(item, kv, heading, content, meta)

        elif typ == "response":
            if n == 0:
                done = bool(kv.get("finished", True))
                ev = self._base(item, event_type="task", action="task_completed" if done else "status",
                                target=_s(content, 300), status="completed", output=content, evidence="tool_call")
                ev.metadata = {**meta, "final": done}
                if self.current_task and done:
                    start = self.items.get(int(self.current_task.rsplit(":", 1)[-1]), {}).get("ts")
                    if start:
                        ev.duration_ms = max(0.0, (float(item.get("timestamp") or time.time()) - start) * 1000)
            else:
                ev = self._base(item, event_type="message", action="agent_message", target=f"report to {self.agent_id(n-1).split(':')[-1]}",
                                status="completed", output=content)
                ev.metadata = meta
            events.append(ev)

        elif typ in ("error", "warning"):
            ev = self._base(item, event_type="error", action="error", target=heading or typ,
                            status="failed" if typ == "error" else "info", output=content, tool="agent_zero")
            ev.risk = "low" if typ == "warning" else "info"
            ev.metadata = meta
            events.append(ev)

        elif typ == "util":
            ev = self._base(item, event_type="tool_execution", action="memory_op", target=heading, tool="memory",
                            status="completed", output=_s(kv) if kv else content or None)
            ev.metadata = {**meta, "kvps": {k: _s(v, 500) for k, v in kv.items()}}
            events.append(ev)

        elif typ == "subagent":
            ev = self._base(item, event_type="delegation", action="delegate_task", target=heading, tool="call_subordinate",
                            status="completed", input=content)
            ev.metadata = meta
            events.append(ev)

        elif typ in ("info", "hint", "input"):
            ev = self._base(item, event_type="system", action="status", target=heading or typ, status="info",
                            output=content or None)
            ev.metadata = meta
            events.append(ev)
        elif typ != "progress":  # unknown / plugin-defined log types (e.g. text_editor) are tool items
            events += self._tool(item, kv, heading, content, meta, typ)

        return events

    # -- tool items --
    def _tool(self, item, kv, heading, content, meta, typ) -> list[Event]:
        tool = str(kv.get("_tool_name") or "").strip() or (typ if typ not in ("tool", "mcp", "browser") else (heading.split("Using")[-1].strip(" :'\"") if "Using" in heading else typ))
        tool_l = tool.lower()
        args = {k: v for k, v in kv.items() if k not in ("_tool_name",) and not str(k).startswith("browser_snapshot")
                and k.lower() != "screenshot"}
        if str(kv.get("action", "")).lower() in ("type", "fill", "input") and SENSITIVE_FIELD.search(
                " ".join(str(kv.get(k, "")) for k in ("selector", "ref", "field", "placeholder", "label"))):
            args["text"] = "[REDACTED]"   # text typed into a password/secret/token field
        meta = {**meta, "tool_name": tool, "tool_args": {k: _s(v, 1500) for k, v in args.items()}}
        if "screenshot" in {k.lower() for k in kv}:
            meta["has_screenshot"] = True
        action = TOOL_ACTIONS.get(tool_l) or ("mcp_call" if typ == "mcp" or "." in tool_l else "tool_call")
        target: str | None = heading
        etype = "tool_execution"
        if action in ("browser_navigation", "browser_action"):
            sub = str(kv.get("action") or "")
            target = _s(kv.get("url") or kv.get("goal") or kv.get("task"), 1000)
            if not target:
                detail = kv.get("selector") or kv.get("ref") or kv.get("text") or kv.get("key") or ""
                target = _s(f"{sub} {detail}".strip() or heading, 300)
            if kv.get("url") is None and action == "browser_navigation":
                action = "browser_action"
        elif tool_l == "text_editor":
            ea = str(kv.get("action") or "read").lower()
            action = EDITOR_ACTIONS.get(ea, "file_modify")
            target = _s(kv.get("path") or kv.get("file") or heading, 1000)
            etype = "file_operation"
        elif tool_l == "document_query":
            target = _s(kv.get("document") or kv.get("uri") or kv.get("url") or heading, 1000)
            action = "file_read" if not str(target).startswith(("http://", "https://")) else "http_request"
        elif action == "web_search":
            target = _s(kv.get("query") or kv.get("q") or heading, 300)
        elif action == "delegate_task":
            etype = "delegation"
            target = _s(kv.get("message") or kv.get("task") or heading, 300)
            n = int(item.get("agentno") or 0)
            meta["child_agent_id"] = self.agent_id(n + 1)
            meta["profile"] = kv.get("profile") or kv.get("agent_profile")
        elif action == "memory_op":
            target = _s(kv.get("query") or kv.get("text") or heading, 300)
        elif action == "mcp_call":
            target = tool
        elif action == "task_completed":
            return []  # the `response` tool also logs a response item
        ev = self._base(item, event_type=etype, action=action, target=target, tool=tool, status="running",
                        input=_s({k: v for k, v in args.items()}, 6000))
        ev.metadata = meta
        if content:
            ev.output = content
        events = [ev]
        if action in ("browser_navigation",) and kv.get("url"):
            pass  # the main event already carries the URL as target
        return events

    # -- code execution items --
    def _code(self, item, kv, heading, content, meta) -> list[Event]:
        runtime = str(kv.get("runtime") or "terminal").lower()
        action, tool = RUNTIME_ACTIONS.get(runtime, ("code_execution", runtime))
        code = kv.get("code")
        session = kv.get("session")
        target = _s((code or heading or "").strip().splitlines()[0] if (code or heading) else heading, 300) if code else heading
        meta = {**meta, "runtime": runtime, "session": session, "reset": kv.get("reset")}
        ev = self._base(item, event_type="tool_execution", action=action, target=target, tool=tool,
                        status="running", input=_s(code, 12000), output=content or None)
        ev.metadata = meta
        out = [ev]
        if code and runtime in ("terminal", "python", "nodejs"):
            for i, url in enumerate(classify.extract_urls(code, 6)):
                e = self._base(item, event_type="network", action="http_request", target=url, tool=tool, status="info",
                               evidence="inferred", parent_event_id=ev.event_id)
                e.event_id = f"{ev.event_id}:url{i}"
                e.metadata = {"inferred_from": "URL string found in executed code - the request itself was not observed",
                              "az_log_no": item["no"]}
                out.append(e)
            if runtime == "terminal" or classify.looks_like_delete(code):
                targets = _rm_targets(code) if runtime == "terminal" else []
                if not targets and classify.looks_like_delete(code):
                    targets = ["(path not parsed - delete call found in code)"]
                for i, p in enumerate(targets):
                    e = self._base(item, event_type="file_operation", action="file_delete", target=p, tool=tool,
                                   status="info", evidence="inferred", parent_event_id=ev.event_id)
                    e.event_id = f"{ev.event_id}:rm{i}"
                    e.metadata = {"inferred_from": "delete command found in executed code - deletion not verified"}
                    out.append(e)
        return out

    # -- completion tracking --
    def finalize(self, ev: Event, item: dict, next_ts: float | None, ctx_running: bool) -> None:
        """Mutate a running event into completed/failed once we have evidence it has ended."""
        if ev.status != "running":
            return
        start = float(item.get("timestamp") or 0) or ev.ts
        ended = next_ts is not None or not ctx_running
        out = (ev.output or "")
        if ev.event_type == "llm_call":
            if not ended:
                return
        elif ev.event_type == "task":
            if not ended:
                return
        elif not (ended or (out.strip() and ev.metadata.get("runtime") not in ("terminal", "python", "nodejs"))):
            return
        end = next_ts if next_ts is not None else time.time()
        ev.duration_ms = max(0.0, (end - start) * 1000)
        ev.metadata["duration_basis"] = "log timestamps (first sighting -> next log item)"
        if ev.event_type in ("tool_execution", "file_operation") and ev.action != "python_execution" and out and FAIL_HINT.search(out[:200]):
            ev.status = "failed"
            ev.metadata["status_basis"] = "output heuristic (output text reads like an error); Agent Zero exposes no explicit status"
        elif ev.action == "python_execution" and "Traceback (most recent call last)" in out:
            ev.status = "failed"
            ev.metadata["status_basis"] = "output heuristic (traceback in output); exit code not exposed by Agent Zero"
        else:
            ev.status = "completed"
        if ev.event_type == "task":
            ev.status = "completed"


@register
class AgentZeroAdapter(Adapter):
    framework = "agent_zero"
    display_name = "Agent Zero"
    description = "Observes Agent Zero chats via its web API (/api/poll). Read-only; no changes to Agent Zero."
    config_schema = [
        {"name": "url", "label": "Agent Zero URL", "type": "text", "default": "http://localhost:5081", "required": True,
         "help": "Base URL of the Agent Zero web UI, e.g. http://host.docker.internal:5081 from inside Docker."},
        {"name": "username", "label": "Username", "type": "text", "default": "", "help": "Only if Agent Zero has a login enabled (AUTH_LOGIN)."},
        {"name": "password", "label": "Password", "type": "password", "default": "", "help": "Stored encrypted."},
        {"name": "api_key", "label": "External API key", "type": "password", "default": "",
         "help": "Optional. Used only for api_log_get fallback when web login is unavailable (Settings > External Services)."},
        {"name": "container", "label": "Docker container name", "type": "text", "default": "",
         "help": "Optional. Links Docker telemetry (processes, filesystem, network) to this instance."},
        {"name": "timezone", "label": "Timezone", "type": "text", "default": "UTC",
         "help": "Agent Zero's poll API sets its display timezone; use your own to avoid changing it."},
        {"name": "poll_interval", "label": "Poll interval (s)", "type": "number", "default": 1.0},
        {"name": "backfill_minutes", "label": "History to import on connect (min)", "type": "number", "default": 30},
        {"name": "verify_tls", "label": "Verify TLS certificate", "type": "bool", "default": True},
    ]
    capabilities = {
        "tool_calls": "observed via Agent Zero log (reported by the framework)",
        "reasoning": "only progress summaries/thoughts that Agent Zero chooses to log",
        "subagents": "observed (agent number hierarchy and call_subordinate tool)",
        "files_processes_network": "NOT visible from the log; use the Docker adapter for verified telemetry",
        "tokens": "not exposed in the log; context-window usage is polled when available",
    }

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.client: httpx.AsyncClient | None = None
        self.csrf: str | None = None
        self.mappers: dict[str, LogMapper] = {}
        self.cursors: dict[str, tuple[str, int]] = {}   # ctx -> (log_guid, log_version)
        self.pending: dict[str, dict[str, tuple[Event, dict]]] = {}  # ctx -> event_id -> (event, item)
        self.last_item_ts: dict[str, dict[int, float]] = {}
        self.known_agents: set[str] = set()
        self.versions: dict[str, int] = {}
        self.backfill_cutoff = 0.0
        self.next_ctx_probe = 0.0

    # ---------- http ----------
    @property
    def base(self) -> str:
        return str(self.config.get("url", "")).rstrip("/")

    def _client(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(base_url=self.base, timeout=20, follow_redirects=False,
                                            verify=bool(self.config.get("verify_tls", True)))
        return self.client

    async def _login(self):
        c = self._client()
        r = await c.get("/api/health")
        r.raise_for_status()
        self.version = (r.json().get("gitinfo") or {}).get("version")
        user, pw = self.config.get("username"), self.config.get("password")
        if user:
            r = await c.post("/login", data={"username": user, "password": pw or "", "next": "/"})
            if r.status_code not in (302, 303):
                raise RuntimeError("Agent Zero rejected the username/password")
        r = await c.get("/api/csrf_token")
        if r.status_code in (301, 302, 303):
            raise RuntimeError("Agent Zero requires a login - set username and password")
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("error") or "csrf_token failed")
        self.csrf = data["token"]
        c.headers["X-CSRF-Token"] = self.csrf

    async def _post(self, path: str, body: dict) -> dict:
        c = self._client()
        for attempt in (0, 1):
            r = await c.post(path, json=body)
            if r.status_code in (301, 302, 303, 401, 403) and attempt == 0:
                await self._login()
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("unreachable")

    async def test(self) -> dict:
        try:
            await self._login()
            snap = await self._post("/api/poll", self._poll_body(None, 0))
            n = len(snap.get("contexts") or [])
            return {"ok": True, "message": f"Connected to Agent Zero {getattr(self, 'version', '')}. {n} chat context(s) found.",
                    "version": getattr(self, "version", None), "contexts": n}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": f"{type(e).__name__}: {e}"}
        finally:
            if self.client:
                await self.client.aclose()
                self.client = None

    def _poll_body(self, ctx: str | None, log_from: int) -> dict:
        return {"context": ctx, "log_from": log_from, "notifications_from": 0,
                "timezone": self.config.get("timezone") or "UTC"}

    # ---------- main loop ----------
    async def run(self):
        await self._login()
        cont = (self.config.get("container") or "").strip()
        if cont:
            self.engine.container_map[cont] = self.id
        self.backfill_cutoff = time.time() - float(self.config.get("backfill_minutes") or 0) * 60
        self.set_status("running", f"connected (Agent Zero {getattr(self, 'version', '?')})", contexts=0, items=0)
        items_seen = self.stats.get("items", 0)
        while True:
            snap = await self._post("/api/poll", self._poll_body(None, 0))
            ctxs = snap.get("contexts") or []
            any_running = False
            for ctx in ctxs:
                cid = ctx["id"]
                running = bool(ctx.get("running"))
                any_running |= running
                if cid not in self.cursors and not running and self._last_message_ts(ctx) < self.backfill_cutoff:
                    # historical idle chat: remember its position, don't import or poll it
                    self.cursors[cid] = (ctx.get("log_guid") or "", int(ctx.get("log_version", 0)))
                    continue
                guid, ver = self.cursors.get(cid, ("", 0))
                changed = ctx.get("log_guid") != guid or int(ctx.get("log_version", 0)) != ver
                if changed or (running and cid in self.pending):
                    items_seen += await self._sync_ctx(ctx, running)
                elif not running and self.pending.get(cid):
                    await self._flush_pending(cid, running=False)
            await self._lifecycle(ctxs)
            if time.time() > self.next_ctx_probe and any_running:
                self.next_ctx_probe = time.time() + 10
                await self._context_usage([c for c in ctxs if c.get("running")])
            self.set_status("running", f"connected (Agent Zero {getattr(self, 'version', '?')})", contexts=len(ctxs), items=items_seen)
            await asyncio.sleep(max(0.3, float(self.config.get("poll_interval") or 1.0)) if any_running else 2.5)

    @staticmethod
    def _last_message_ts(ctx: dict) -> float:
        from datetime import datetime
        try:
            return datetime.fromisoformat(str(ctx.get("last_message")).replace("Z", "+00:00")).timestamp()
        except Exception:
            return time.time()

    def _mapper(self, ctx: dict) -> LogMapper:
        m = self.mappers.get(ctx["id"])
        if not m:
            m = self.mappers[ctx["id"]] = LogMapper(self.id, ctx["id"], ctx.get("name") or "", self.framework)
        else:
            m.ctx_name = ctx.get("name") or m.ctx_name
        return m

    async def _sync_ctx(self, ctx: dict, running: bool) -> int:
        cid = ctx["id"]
        guid, ver = self.cursors.get(cid, ("", 0))
        if ctx.get("log_guid") != guid:  # chat reset or first time
            ver = 0
            self.mappers.pop(cid, None)
            self.pending.pop(cid, None)
            self.last_item_ts.pop(cid, None)
        snap = await self._post("/api/poll", self._poll_body(cid, ver))
        items = snap.get("logs") or []
        self.cursors[cid] = (snap.get("log_guid") or ctx.get("log_guid") or "", int(snap.get("log_version") or ver))
        mapper = self._mapper(ctx)
        pend = self.pending.setdefault(cid, {})
        times = self.last_item_ts.setdefault(cid, {})
        count = 0
        for item in sorted(items, key=lambda i: i["no"]):
            ts = float(item.get("timestamp") or 0)
            n = int(item.get("agentno") or 0)
            mapper.items.setdefault(item["no"], {"ts": ts})
            if ts and ts < self.backfill_cutoff and item["no"] not in pend:
                times[n] = max(times.get(n, 0), ts)
                if (item.get("type") == "user" and n == 0):
                    mapper.current_task = f"az:{self.id}:{cid}:{item['no']}"
                continue
            # a newer item from the same agent proves earlier tool items ended
            for eid, (pev, pitem) in list(pend.items()):
                if int(pitem.get("agentno") or 0) == n and pitem["no"] < item["no"] and pev.event_type != "task":
                    mapper.finalize(pev, pitem, ts or time.time(), running)
                    if pev.status != "running":
                        await self.emit(pev)
                        pend.pop(eid, None)
            for ev in mapper.map(item, running):
                if ev.agent_id not in self.known_agents:
                    self.known_agents.add(ev.agent_id)
                    if ev.parent_agent_id:
                        await self.emit(self._delegation_edge(ev))
                mapper.finalize(ev, item, None, running)
                if ev.status == "running":
                    pend[ev.event_id] = (ev, item)
                elif ev.event_id in pend:
                    pend.pop(ev.event_id, None)
                await self.emit(ev)
                count += 1
            times[n] = max(times.get(n, 0), ts)
        if not running:
            await self._flush_pending(cid, running=False)
        return count

    def _delegation_edge(self, ev: Event) -> Event:
        """Explicit hierarchy fact: this agent number exists because its superior delegated to it."""
        return Event(event_id=f"{ev.agent_id}:spawn", timestamp=ev.timestamp, agent_id=ev.parent_agent_id or ev.agent_id,
                     parent_agent_id=None, agent_name=None, event_type="agent_lifecycle", action="status",
                     target=f"subordinate {ev.agent_id.rsplit(':', 1)[-1]} active", status="info", source=self.framework,
                     instance_id=self.id, evidence="tool_call",
                     metadata={"status": "working", "child_agent_id": ev.agent_id})

    async def _flush_pending(self, cid: str, running: bool):
        pend = self.pending.get(cid) or {}
        for eid, (pev, pitem) in list(pend.items()):
            self._mapper({"id": cid}).finalize(pev, pitem, None, running)
            if pev.status != "running":
                await self.emit(pev)
                pend.pop(eid, None)

    async def _lifecycle(self, ctxs: list[dict]):
        for ctx in ctxs:
            cid = ctx["id"]
            running = bool(ctx.get("running"))
            prev = self.versions.get(cid)
            state = "working" if running else "idle"
            key = f"{state}:{ctx.get('paused')}"
            if prev == key or (prev is None and not running):
                self.versions.setdefault(cid, key)
                continue
            self.versions[cid] = key
            m = self._mapper(ctx)
            if ctx.get("paused"):
                state = "paused"
            await self.emit(Event(
                event_id=f"az:{self.id}:{cid}:life:{int(time.time() * 1000)}", agent_id=m.agent_id(0), agent_name=m.agent_name(0),
                event_type="agent_lifecycle", action="status", target=state, status="info", source=self.framework,
                instance_id=self.id, evidence="tool_call",
                metadata={"status": state, "context_name": ctx.get("name"),
                          "profile": ctx.get("agent_profile_label") or ctx.get("agent_profile")}))

    async def _context_usage(self, ctxs: list[dict]):
        for ctx in ctxs[:3]:
            try:
                d = await self._post("/api/ctx_window_get", {"context": ctx["id"]})
            except Exception:
                return  # endpoint absent in this version - not an error
            tokens = d.get("tokens")
            if tokens is None:
                continue
            m = self._mapper(ctx)
            await self.emit(Event(
                event_id=f"az:{self.id}:{ctx['id']}:ctx:{int(time.time())}", agent_id=m.agent_id(0), agent_name=m.agent_name(0),
                event_type="agent_lifecycle", action="status", target="context window", status="info", source=self.framework,
                instance_id=self.id, evidence="execution", metadata={"status": "working", "context_tokens": tokens}))

    async def stop(self):
        if self.client:
            await self.client.aclose()
            self.client = None
        for k in [k for k, v in self.engine.container_map.items() if v == self.id]:
            self.engine.container_map.pop(k, None)

    # ---------- explicit, opt-in intervention ----------
    async def intervene(self, action: str, context_id: str) -> dict:
        if action not in ("pause", "resume"):
            raise ValueError("unsupported action")
        if not self.client:
            await self._login()
        return await self._post("/api/pause", {"paused": action == "pause", "context": context_id})
