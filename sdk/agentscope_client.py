"""Tiny dependency-free client for the AgentScope *Generic HTTP ingest* integration.

    from agentscope_client import AgentScope
    scope = AgentScope("http://localhost:8000", integration_id="generic-http-ab12c", key="as_...")
    with scope.tool("web_search", target="rust async", tool="search", agent_id="researcher") as ev:
        result = do_search()
        ev.output = result[:500]

Events are sent in the standard AgentScope schema (see docs/ADAPTERS.md). Failures to reach AgentScope
never raise into your agent - observability must not break the agent.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class _Ev:
    def __init__(self, base: dict):
        self.base = base
        self.output: str | None = None
        self.metadata: dict = {}


class AgentScope:
    def __init__(self, url: str, integration_id: str, key: str, agent_id: str = "agent", agent_name: str | None = None, timeout: float = 3.0):
        self.endpoint = f"{url.rstrip('/')}/api/ingest/{integration_id}"
        self.key, self.agent_id, self.agent_name, self.timeout = key, agent_id, agent_name, timeout
        self._q: queue.Queue = queue.Queue(maxsize=10000)
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        while True:
            events = self._q.get()
            try:
                req = urllib.request.Request(self.endpoint, data=json.dumps(events).encode(), method="POST",
                                             headers={"Content-Type": "application/json", "X-AgentScope-Key": self.key})
                urllib.request.urlopen(req, timeout=self.timeout).read()
            except Exception:
                pass

    def send(self, events: list[dict]) -> None:
        """Queued and delivered in order by one background thread (running always precedes completed)."""
        self._q.put(events)

    def flush(self, timeout: float = 5.0) -> None:
        end = time.time() + timeout
        while not self._q.empty() and time.time() < end:
            time.sleep(0.05)
        time.sleep(0.1)

    def event(self, action: str, *, event_type: str = "tool_execution", target: str | None = None, tool: str | None = None,
              status: str = "completed", agent_id: str | None = None, parent_agent_id: str | None = None, **extra) -> str:
        eid = extra.pop("event_id", uuid.uuid4().hex)
        self.send([{"event_id": eid, "timestamp": _iso(), "agent_id": agent_id or self.agent_id, "agent_name": self.agent_name,
                    "parent_agent_id": parent_agent_id, "event_type": event_type, "action": action, "target": target, "tool": tool,
                    "status": status, **extra}])
        return eid

    @contextmanager
    def tool(self, action: str, *, target: str | None = None, tool: str | None = None, event_type: str = "tool_execution",
             agent_id: str | None = None, parent_agent_id: str | None = None, input: str | None = None, evidence: str = "tool_call"):
        """Reports running -> completed/failed with duration. Same event_id => the UI updates one record."""
        eid, t0 = uuid.uuid4().hex, time.time()
        base = {"event_id": eid, "agent_id": agent_id or self.agent_id, "agent_name": self.agent_name, "parent_agent_id": parent_agent_id,
                "event_type": event_type, "action": action, "target": target, "tool": tool, "input": input, "evidence": evidence}
        ev = _Ev(base)
        self.send([{**base, "timestamp": _iso(), "status": "running"}])
        try:
            yield ev
        except Exception as e:
            self.send([{**base, "timestamp": datetime.fromtimestamp(t0, timezone.utc).isoformat(), "status": "failed",
                        "duration_ms": (time.time() - t0) * 1000, "output": str(e)[:2000], "metadata": ev.metadata}])
            raise
        else:
            self.send([{**base, "timestamp": datetime.fromtimestamp(t0, timezone.utc).isoformat(), "status": "completed",
                        "duration_ms": (time.time() - t0) * 1000, "output": ev.output, "metadata": ev.metadata}])
