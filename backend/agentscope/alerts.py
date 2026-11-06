"""Configurable alert rules evaluated against observed events.

Rules react to *observable activity* only. Each alert records the evidence level of
the event that triggered it, so an alert based on an inferred event is never shown
as verified.
"""
from __future__ import annotations

import fnmatch
import time
import uuid
from collections import defaultdict, deque
from typing import Any

from . import classify
from .schema import Event

SEVERITIES = ["info", "low", "medium", "high", "critical"]

DEFAULT_RULES: list[dict[str, Any]] = [
    {"id": "new-external-domain", "name": "Previously unseen external domain", "kind": "new_external_domain",
     "enabled": True, "severity": "medium", "params": {"ignore": []},
     "description": "An agent contacts an external hostname AgentScope has never recorded before."},
    {"id": "sensitive-path", "name": "Sensitive path access", "kind": "sensitive_path",
     "enabled": True, "severity": "high", "params": {"paths": []},
     "description": "A file operation or command references a sensitive location (~/.ssh, /etc/shadow, .env, docker.sock …)."},
    {"id": "unexpected-executable", "name": "Unexpected executable", "kind": "unexpected_executable",
     "enabled": True, "severity": "medium", "params": {"allow": sorted(classify.COMMON_EXECUTABLES)},
     "description": "A command or process starts an executable that is not in the allow-list."},
    {"id": "mass-file-creation", "name": "Many files created", "kind": "mass_file_creation",
     "enabled": True, "severity": "medium", "params": {"count": 25, "window_s": 60},
     "description": "An agent creates or modifies a large number of files within a short window."},
    {"id": "file-delete", "name": "File deletion", "kind": "file_delete",
     "enabled": True, "severity": "medium", "params": {},
     "description": "An observed or inferred file deletion."},
    {"id": "unusual-port", "name": "Unusual destination port", "kind": "unusual_port",
     "enabled": True, "severity": "low", "params": {"allowed_ports": [80, 443, 53, 8080, 8443, 22, 11434, 1234]},
     "description": "An external connection targets a port outside the allow-list."},
    {"id": "subprocess-burst", "name": "Subprocess burst", "kind": "subprocess_burst",
     "enabled": True, "severity": "medium", "params": {"count": 30, "window_s": 60},
     "description": "An unusually large number of processes started in a short window."},
    {"id": "restricted-resource", "name": "Restricted resource", "kind": "restricted_resource",
     "enabled": True, "severity": "high", "params": {"patterns": []},
     "description": "Any event whose target matches a glob in the deny-list (hostnames, paths, tools). Empty by default."},
    {"id": "error-burst", "name": "Repeated failures", "kind": "error_burst",
     "enabled": False, "severity": "low", "params": {"count": 6, "window_s": 60},
     "description": "An agent produces many failed operations in a short window."},
]

RULE_KINDS = sorted({r["kind"] for r in DEFAULT_RULES})
MAX_EVENT_AGE_S = 180  # alerts fire for live activity; replayed history never alerts


class AlertEngine:
    def __init__(self):
        self.rules: dict[str, dict] = {}
        self.known_domains: set[str] = set()
        self._windows: dict[tuple[str, str], deque] = defaultdict(deque)
        self._dedup: dict[str, dict] = {}
        self.sensitive_extra: list[str] = []

    def load_rules(self, rules: list[dict]):
        self.rules = {r["id"]: r for r in rules}

    def learn_domain(self, host: str):
        self.known_domains.add(host.lower())

    def _window_hit(self, kind: str, agent: str, ev: Event, count: int, window: float) -> int | None:
        dq = self._windows[(kind, agent)]
        now = ev.ts
        dq.append(now)
        while dq and now - dq[0] > window:
            dq.popleft()
        return len(dq) if len(dq) >= count else None

    def evaluate(self, ev: Event) -> list[dict]:
        """Return a list of {rule, title, description, key} for triggered rules."""
        if ev.metadata.get("baseline") or ev.metadata.get("ended"):
            return []
        if time.time() - ev.ts > MAX_EVENT_AGE_S:
            if ev.category == "network" or ev.metadata.get("network"):
                host = (ev.metadata.get("network") or {}).get("host")
                if host:
                    self.learn_domain(host)
            return []
        out: list[dict] = []
        cat = ev.derive_category()
        net = ev.metadata.get("network") or {}
        text = " ".join(filter(None, [ev.target, ev.input]))[:4000]
        for rule in self.rules.values():
            if not rule.get("enabled"):
                continue
            k, p = rule["kind"], rule.get("params", {})
            hit = None
            if k == "new_external_domain" and net.get("host") and net.get("scope") == "external":
                host = net["host"].lower()
                if host not in self.known_domains and not any(fnmatch.fnmatch(host, g) for g in p.get("ignore", [])):
                    self.known_domains.add(host)
                    hit = (f"New external domain: {host}", f"{ev.agent_id} contacted {host} for the first time ({ev.action}).", host)
                elif host:
                    self.known_domains.add(host)
            elif k == "sensitive_path":
                extra = list(p.get("paths", [])) + self.sensitive_extra
                target_hit = classify.sensitive_path_hit(ev.target, extra) if cat in ("file_read", "file_write", "file_delete") else None
                if target_hit is None and cat in ("terminal", "python"):
                    target_hit = classify.sensitive_in_text(text, extra)
                if target_hit:
                    hit = (f"Sensitive path referenced: {target_hit}", f"{ev.action} on {ev.target or '(command)'} matched '{target_hit}'.", f"{ev.event_id}")
            elif k == "unexpected_executable" and cat in ("terminal", "process", "subprocess"):
                allow = set(p.get("allow", []))
                cmd = ev.input if cat == "terminal" and ev.input else ev.target
                exes = classify.all_executables(cmd or "") if cat == "terminal" else [classify.first_executable(ev.target or "") or ""]
                bad = [e for e in exes if e and e not in allow]
                if bad:
                    hit = (f"Unexpected executable: {bad[0]}", f"'{bad[0]}' is not in the allow-list (command: {(cmd or '')[:140]}).", bad[0] + ev.agent_id)
            elif k == "mass_file_creation" and ev.action in ("file_create", "file_write", "file_modify") and cat == "file_write":
                n = self._window_hit(k, ev.agent_id, ev, p.get("count", 25), p.get("window_s", 60))
                if n:
                    hit = (f"{n} files written within {p.get('window_s', 60)}s", f"{ev.agent_id} wrote {n} files in {p.get('window_s', 60)} seconds.", ev.agent_id)
            elif k == "file_delete" and (cat == "file_delete" or ev.action == "file_delete"):
                hit = (f"File deletion: {ev.target}", f"{ev.agent_id} deleted {ev.target} ({ev.evidence}).", ev.event_id)
            elif k == "unusual_port" and net.get("scope") == "external" and net.get("port"):
                if int(net["port"]) not in set(p.get("allowed_ports", [])):
                    hit = (f"Unusual port {net['port']} on {net.get('host')}", f"{ev.agent_id} -> {net.get('host')}:{net['port']}", f"{net.get('host')}:{net['port']}")
            elif k == "subprocess_burst" and cat == "subprocess" and ev.action == "process_spawn":
                n = self._window_hit(k, ev.agent_id, ev, p.get("count", 30), p.get("window_s", 60))
                if n:
                    hit = (f"{n} processes started within {p.get('window_s', 60)}s", f"{ev.agent_id}: {n} spawns.", ev.agent_id)
            elif k == "restricted_resource":
                pats = p.get("patterns", [])
                cand = [ev.target or "", net.get("host") or "", ev.tool or ""]
                for g in pats:
                    if any(c and fnmatch.fnmatch(c.lower(), g.lower()) for c in cand):
                        hit = (f"Restricted resource: {g}", f"{ev.action} touched '{ev.target}' matching deny-list entry '{g}'.", g + ev.agent_id)
                        break
            elif k == "error_burst" and ev.status == "failed":
                n = self._window_hit(k, ev.agent_id, ev, p.get("count", 6), p.get("window_s", 60))
                if n:
                    hit = (f"{n} failed operations in {p.get('window_s', 60)}s", f"{ev.agent_id} failure burst.", ev.agent_id)
            if hit:
                title, desc, key = hit
                out.append({"rule": rule, "title": title, "description": desc, "key": f"{rule['id']}:{key}"})
        return out

    def build_alert(self, trig: dict, ev: Event) -> tuple[dict, bool]:
        """Return (alert, is_update). Repeats within 5 minutes increment a counter."""
        now = time.time()
        existing = self._dedup.get(trig["key"])
        if existing and now - existing["last_ts"] < 300:
            existing["count"] += 1
            existing["last_ts"] = now
            return existing, True
        alert = {
            "id": uuid.uuid4().hex, "ts": now, "last_ts": now, "rule_id": trig["rule"]["id"],
            "severity": trig["rule"]["severity"], "title": trig["title"], "description": trig["description"],
            "agent_id": ev.agent_id, "event_id": ev.event_id, "acked": False, "count": 1,
            "data": {"evidence": ev.evidence, "action": ev.action, "target": ev.target, "source": ev.source,
                     "rule_name": trig["rule"]["name"]},
        }
        self._dedup[trig["key"]] = alert
        if len(self._dedup) > 5000:
            for k in sorted(self._dedup, key=lambda x: self._dedup[x]["last_ts"])[:1000]:
                self._dedup.pop(k, None)
        return alert, False
