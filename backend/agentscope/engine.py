"""The central event pipeline: sanitize -> enrich -> store -> alert -> broadcast.

Adapters only ever call `engine.emit(event)`; nothing here knows about any framework.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from typing import Any

from . import alerts as alerts_mod
from . import classify, config
from .netintel import NetIntel
from .redaction import Redactor
from .schema import Agent, Event
from .store import Store

log = logging.getLogger("agentscope.engine")

RISK_ORDER = ["info", "low", "medium", "high", "critical"]
STALE_WORKING_S = 120


class Hub:
    """Fan-out of messages to WebSocket clients with bounded per-client queues."""

    def __init__(self):
        self.clients: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self.clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self.clients.discard(q)

    def publish(self, msg: dict):
        for q in list(self.clients):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait({"type": "resync"})
                except Exception:
                    pass


class Engine:
    def __init__(self, store: Store):
        self.store = store
        self.hub = Hub()
        self.alerts = alerts_mod.AlertEngine()
        self.net = NetIntel()
        self.settings: dict[str, Any] = dict(config.DEFAULT_SETTINGS)
        self.redactor = Redactor()
        self.agents: dict[str, Agent] = {}
        self.metrics: dict[str, dict] = {}       # container metrics (latest + history)
        self.process_snapshots: dict[str, dict] = {}  # container -> latest process table
        self.adapter_status: dict[str, dict] = {}
        self.container_map: dict[str, str] = {}  # docker container name -> agent framework instance id
        self.started = time.time()
        self.counters = {"events": 0, "rejected": 0}
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------ lifecycle
    async def start(self):
        saved = await self.store.get_settings()
        self.settings.update({k: v for k, v in saved.items() if k in config.DEFAULT_SETTINGS})
        self.apply_settings()
        rules = {r["id"]: r for r in await self.store.list_rules()}
        for d in alerts_mod.DEFAULT_RULES:
            if d["id"] not in rules:
                rules[d["id"]] = d
                await self.store.put_rule(d)
        self.alerts.load_rules(list(rules.values()))
        for a in await self.store.load_agents():
            self.agents[a.agent_id] = a
        for cat in ("web", "api", "network"):
            for t in await self.store.distinct_targets(cat):
                host = classify.parse_target(t)["host"]
                if host:
                    self.alerts.learn_domain(host)
        self.spawn(self._retention_loop())

    def apply_settings(self):
        self.redactor = Redactor(self.settings.get("extra_redaction_patterns"), int(self.settings.get("max_text_len", 20000)))
        self.alerts.sensitive_extra = list(self.settings.get("sensitive_paths") or [])

    def spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    async def _retention_loop(self):
        while True:
            try:
                days = float(self.settings.get("retention_days", 14))
                if days > 0:
                    n = await self.store.prune(time.time() - days * 86400)
                    if n:
                        log.info("retention: pruned %d events", n)
            except Exception:
                log.exception("retention failed")
            await asyncio.sleep(3600)

    # ------------------------------------------------------------ pipeline
    def _sanitize(self, ev: Event):
        r = self.redactor
        ev.input = r.text(ev.input)
        ev.output = r.text(ev.output)
        ev.target = r.text(ev.target, 2000)
        ev.metadata = r.obj(ev.metadata)
        if ev.metadata.get("url"):
            ev.metadata["url"] = r.text(ev.metadata["url"], 2000)

    def _enrich(self, ev: Event):
        ev.category = ev.derive_category()
        net = ev.metadata.get("network")
        if ev.action == "web_search":
            ev.metadata.pop("network", None)   # target is a search query, not a destination
        elif ev.target and ev.category in ("web", "api", "network") and (net is None or not net.get("scope")):
            ev.metadata["network"] = NetIntel.base_meta(ev.target, net, ev.evidence)
        elif net is not None and not net.get("scope") and net.get("host"):
            net["scope"] = classify.classify_host(net["host"], net.get("ip"))

    async def emit(self, ev: Event) -> Event | None:
        """Entry point for every adapter."""
        try:
            if ev.status == "running":   # a late 'running' must never overwrite a finished record
                prior = await self.store.get_event(ev.event_id)
                if prior and prior.get("status") in ("completed", "failed", "blocked"):
                    return None
            self._sanitize(ev)
            self._enrich(ev)
            existed = await self.store.upsert_event(ev)
            if not existed:
                self.counters["events"] += 1
                triggered = self.alerts.evaluate(ev)
                if triggered:
                    top = ev.risk
                    for trig in triggered:
                        alert, is_update = self.alerts.build_alert(trig, ev)
                        await self.store.save_alert(alert)
                        self.hub.publish({"type": "alert", "alert": alert, "update": is_update})
                        if RISK_ORDER.index(alert["severity"]) > RISK_ORDER.index(top):
                            top = alert["severity"]
                    if top != ev.risk:
                        ev.risk = top
                        await self.store.upsert_event(ev)
            await self._touch_agent(ev, existed)
            self.hub.publish({"type": "event_update" if existed else "event", "event": ev.model_dump()})
            if ev.metadata.get("network") and not existed:
                self.spawn(self._enrich_network_async(ev))
            return ev
        except Exception:
            self.counters["rejected"] += 1
            log.exception("failed to ingest event")
            return None

    async def _enrich_network_async(self, ev: Event):
        net = ev.metadata.get("network") or {}
        host = net.get("host")
        if not host:
            return
        changed = False
        if not net.get("ip") and self.settings.get("resolve_dns") and net.get("scope") not in ("localhost",):
            ips = await self.net.resolve(host)
            if ips:
                net["ip"], net["resolved_ips"] = ips[0], ips
                net["ip_source"] = "agentscope_dns_resolution (may differ from the address the agent used)"
                net["scope"] = classify.classify_host(host, ips[0]) if net.get("scope") != "docker" else "docker"
                changed = True
        if net.get("ip") and not net.get("geo") and net.get("scope") == "external":
            geo = await self.net.geolocate(net["ip"], self.settings.get("geoip_provider", "off"))
            if geo:
                net["geo"] = geo
                changed = True
        if changed:
            ev.metadata["network"] = net
            await self.store.upsert_event(ev)
            self.hub.publish({"type": "event_update", "event": ev.model_dump()})

    # ------------------------------------------------------------ agents
    async def _touch_agent(self, ev: Event, existed: bool):
        a = self.agents.get(ev.agent_id)
        now = time.time()
        if a is None:
            a = Agent(agent_id=ev.agent_id, name=ev.agent_name or ev.agent_id, parent_agent_id=ev.parent_agent_id,
                      source=ev.source, instance_id=ev.instance_id, first_seen=ev.ts, last_seen=ev.ts,
                      kind=ev.metadata.get("agent_kind", "agent"))
            self.agents[ev.agent_id] = a
        if ev.agent_name:
            a.name = ev.agent_name
        if ev.parent_agent_id:
            a.parent_agent_id = ev.parent_agent_id
        a.last_seen = max(a.last_seen, ev.ts)
        if not existed:
            a.event_count += 1
        md = ev.metadata
        if ev.event_type == "agent_lifecycle":
            a.status = md.get("status", a.status)
            if md.get("current_task") is not None:
                a.current_task = md["current_task"] or None
            if a.status != "working":
                a.current_tool = None
        elif ev.status == "running" and ev.event_type in ("tool_execution", "llm_call", "process"):
            a.status, a.current_tool = "working", ev.tool or ev.action
        elif ev.status in ("completed", "failed") and a.current_tool in (ev.tool, ev.action):
            a.current_tool = None
        if ev.event_type == "task" and ev.action == "task_received":
            a.current_task = ev.target
        if md.get("model"):
            a.model = md["model"]
        usage = md.get("tokens")
        if isinstance(usage, dict) and not existed:
            a.tokens_in += int(usage.get("input") or 0)
            a.tokens_out += int(usage.get("output") or 0)
        if md.get("context_pct") is not None:
            a.context_pct = float(md["context_pct"])
        await self.store.save_agent(a)
        self.hub.publish({"type": "agent", "agent": self.agent_view(a)})

    def agent_view(self, a: Agent) -> dict:
        d = a.model_dump()
        if d["status"] == "working" and time.time() - a.last_seen > STALE_WORKING_S:
            d["status"] = "idle"
        return d

    # ------------------------------------------------------------ queries
    def overview(self) -> dict:
        now = time.time()
        views = [self.agent_view(a) for a in self.agents.values() if a.kind == "agent"]
        active = [v for v in views if v["status"] == "working"]
        return {
            "agents_total": len(views), "agents_active": len(active),
            "events_ingested": self.counters["events"], "rejected": self.counters["rejected"],
            "uptime_s": now - self.started,
            "adapters": self.adapter_status,
            "clients": len(self.hub.clients),
        }
