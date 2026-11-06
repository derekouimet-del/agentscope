"""Simulated multi-agent activity for UI development and demos.

Every event produced here has source="simulator", evidence="simulated" and
metadata.simulated=true, and the UI renders a SIMULATED badge on them.
"""
from __future__ import annotations

import asyncio
import random
import uuid

from ..schema import Event
from .base import Adapter, register

SITES = {
    "en.wikipedia.org": ("208.80.154.224", "United States", "US", "San Francisco", 37.77, -122.42),
    "arxiv.org": ("151.101.67.42", "United States", "US", "Ithaca", 42.44, -76.50),
    "news.ycombinator.com": ("209.216.230.240", "United States", "US", "Walnut", 34.02, -117.86),
    "docs.python.org": ("151.101.0.223", "Germany", "DE", "Frankfurt", 50.11, 8.68),
    "api.github.com": ("140.82.114.6", "United States", "US", "Ashburn", 39.04, -77.49),
    "pypi.org": ("151.101.192.223", "United Kingdom", "GB", "London", 51.51, -0.13),
    "api.openweathermap.org": ("188.166.43.1", "Netherlands", "NL", "Amsterdam", 52.37, 4.90),
    "www.bbc.co.uk": ("151.101.64.81", "United Kingdom", "GB", "London", 51.51, -0.13),
    "example-unknown-host.xyz": ("185.220.101.7", "Romania", "RO", "Bucharest", 44.43, 26.10),
}
FILES = ["/workspace/notes.md", "/workspace/report.md", "/workspace/data/results.csv", "/workspace/src/main.py",
         "/workspace/src/utils.py", "/workspace/README.md", "/workspace/data/raw.json"]


@register
class Simulator(Adapter):
    framework = "simulator"
    display_name = "Simulator (demo data)"
    description = "Generates clearly-labelled SIMULATED agent activity so the dashboard can be explored without a live agent."
    config_schema = [
        {"name": "speed", "label": "Speed multiplier", "type": "number", "default": 1.0},
        {"name": "include_alerts", "label": "Include alert-triggering behaviour", "type": "bool", "default": True},
    ]
    capabilities = {"all": "SIMULATED - nothing here reflects a real agent"}

    def _agent(self, n: str):
        return f"sim:{n}"

    async def _sleep(self, a: float, b: float):
        await asyncio.sleep(random.uniform(a, b) / max(0.1, float(self.config.get("speed") or 1)))

    async def _op(self, agent: str, parent: str | None, task: str, etype: str, action: str, target: str, tool: str,
                  dur: tuple[float, float] = (0.4, 1.5), inp: str | None = None, out: str | None = None, fail: bool = False,
                  meta: dict | None = None, evidence: str = "simulated") -> Event:
        eid = uuid.uuid4().hex
        md = {"simulated": True, **(meta or {})}
        target_host = target.split("://", 1)[-1].split("/", 1)[0]
        if target_host in SITES and action != "web_search":
            ip, country, cc, city, lat, lon = SITES[target_host]
            md["network"] = {"host": target_host, "ip": ip, "port": 443, "scheme": "https", "protocol": "https", "encrypted": True,
                             "visibility": "reported_url_only",
                             "geo": {"country": country, "country_code": cc, "city": city, "lat": lat, "lon": lon, "provider": "simulated"}}
        base = dict(event_id=eid, agent_id=self._agent(agent), agent_name=agent, parent_agent_id=self._agent(parent) if parent else None,
                    event_type=etype, action=action, target=target, tool=tool, task_id=task, source="simulator",
                    evidence="simulated", input=inp, metadata=md)
        await self.emit(Event(status="running", **base))
        d = random.uniform(*dur)
        await self._sleep(d, d)
        await self.emit(Event(status="failed" if fail else "completed", duration_ms=d * 1000, output=out, **base))
        return Event(**base)

    async def _status(self, agent: str, parent: str | None, status: str, task: str | None = None):
        await self.emit(Event(agent_id=self._agent(agent), agent_name=agent, parent_agent_id=self._agent(parent) if parent else None,
                              event_type="agent_lifecycle", action="status", target=status, status="info", source="simulator",
                              evidence="simulated", task_id=task,
                              metadata={"simulated": True, "status": status, "current_task": task if status == "working" else None,
                                        "model": {"Atlas": "sim-large-1", "Scout": "sim-small-1", "Forge": "sim-code-1"}[agent]}))

    async def run(self):
        self.set_status("running", "SIMULATED data", cycles=0)
        n = 0
        while True:
            n += 1
            topic = random.choice(["quantum error correction", "Rust async runtimes", "EU AI Act", "battery chemistry", "vector databases"])
            await self._scenario(topic)
            self.set_status("running", "SIMULATED data", cycles=n)
            await self._sleep(4, 9)

    async def _scenario(self, topic: str):
        task = f"sim-task-{uuid.uuid4().hex[:6]}"
        A, S, F = "Atlas", "Scout", "Forge"
        await self._status(A, None, "working", task)
        await self.emit(Event(agent_id=self._agent(A), agent_name=A, event_type="task", action="task_received",
                              target=f"Research {topic} and write a summary with a small analysis script", status="running",
                              source="simulator", evidence="simulated", task_id=task,
                              input=f"Research {topic} and write a summary with a small analysis script", metadata={"simulated": True}))
        await self._sleep(0.5, 1)
        await self._op(A, None, task, "llm_call", "llm_call", "sim-large-1", "llm", (0.6, 1.4),
                       meta={"model": "sim-large-1", "tokens": {"input": random.randint(900, 3200), "output": random.randint(80, 400)},
                             "context_pct": random.uniform(8, 40)})
        await self._op(A, None, task, "reasoning", "reasoning", "Plan: delegate source gathering to Scout, code to Forge", "agent", (0.1, 0.3),
                       out="1. Gather sources 2. Summarise 3. Analyse with python", evidence="exposed_reasoning")
        await self._op(A, None, task, "delegation", "delegate_task", f"{S}: gather sources on {topic}", "call_subordinate", (0.2, 0.5))
        await self._status(S, A, "working", task)
        await self._op(S, A, task, "tool_execution", "web_search", topic, "search_engine", (0.5, 1.2))
        for host in random.sample([h for h in SITES if h != "example-unknown-host.xyz"], 3):
            await self._op(S, A, task, "tool_execution", "browser_navigation", f"https://{host}/", "browser", (0.8, 2.2),
                           out="Page loaded (content not captured in simulation)")
            await self._op(S, A, task, "file_operation", "file_write", random.choice(FILES[:2]), "text_editor", (0.1, 0.4), out="wrote 2.1 KB")
        await self._op(S, A, task, "tool_execution", "http_request", "https://api.github.com/search/repositories", "requests", (0.4, 1.0),
                       out="200 OK", meta={"method": "GET", "status_code": 200})
        if self.config.get("include_alerts", True) and random.random() < 0.35:
            await self._op(S, A, task, "tool_execution", "browser_navigation", "https://example-unknown-host.xyz/dl", "browser", (0.5, 1.0))
        await self._op(S, A, task, "message", "agent_message", f"report to {A}", "call_subordinate", (0.1, 0.2), out="3 sources gathered")
        await self._status(S, A, "idle")
        await self._op(A, None, task, "delegation", "delegate_task", f"{F}: analyse findings", "call_subordinate", (0.2, 0.5))
        await self._status(F, A, "working", task)
        await self._op(F, A, task, "file_operation", "file_read", "/workspace/notes.md", "text_editor", (0.1, 0.3))
        await self._op(F, A, task, "tool_execution", "python_execution", "analysis.py", "python", (1.0, 2.5),
                       inp="import pandas as pd\ndf = pd.read_csv('/workspace/data/raw.json')\nprint(df.describe())",
                       out="count 42 mean 3.2 …")
        await self.emit(Event(agent_id=self._agent(F), agent_name=F, parent_agent_id=self._agent(A), event_type="process",
                              action="process_spawn", target="python3 analysis.py", tool="python3", status="completed",
                              duration_ms=1800, source="simulator", evidence="simulated", task_id=task,
                              metadata={"simulated": True, "pid": random.randint(2000, 9000), "ppid": 1200, "cmd": "python3 analysis.py"}))
        await self._op(F, A, task, "tool_execution", "terminal_command", "pip install pandas", "terminal", (1.0, 2.0),
                       inp="pip install pandas", out="Successfully installed pandas", fail=random.random() < 0.15)
        await self._op(F, A, task, "tool_execution", "database_query", "sqlite:///workspace/data/cache.db", "sqlite", (0.2, 0.6),
                       inp="SELECT count(*) FROM results", out="42")
        await self._op(F, A, task, "file_operation", "file_create", "/workspace/data/results.csv", "text_editor", (0.1, 0.3))
        if self.config.get("include_alerts", True) and random.random() < 0.3:
            await self._op(F, A, task, "file_operation", "file_read", "/root/.ssh/id_rsa", "terminal", (0.1, 0.2), evidence="simulated",
                           meta={"note": "simulated sensitive-path access to demonstrate alerting"})
            await self._op(F, A, task, "file_operation", "file_delete", "/workspace/data/raw.json", "terminal", (0.1, 0.2))
        await self._op(F, A, task, "message", "agent_message", f"report to {A}", "call_subordinate", (0.1, 0.2), out="analysis complete")
        await self._status(F, A, "idle")
        await self._op(A, None, task, "llm_call", "llm_call", "sim-large-1", "llm", (0.6, 1.2),
                       meta={"model": "sim-large-1", "tokens": {"input": random.randint(2000, 5000), "output": random.randint(200, 700)},
                             "context_pct": random.uniform(30, 70)})
        await self._op(A, None, task, "file_operation", "file_write", "/workspace/report.md", "text_editor", (0.1, 0.3))
        await self.emit(Event(agent_id=self._agent(A), agent_name=A, event_type="task", action="task_completed", target=f"Summary of {topic} written",
                              status="completed", source="simulator", evidence="simulated", task_id=task, duration_ms=random.uniform(18000, 40000),
                              output=f"Wrote /workspace/report.md for {topic}", metadata={"simulated": True}))
        await self._status(A, None, "idle")
