"""Generic push adapter: any framework (LangGraph, AutoGen, CrewAI, OpenCode plugins, scripts …)
can POST standard AgentScope events to /api/ingest/{integration_id}.

See sdk/agentscope_client.py and docs/ADAPTERS.md for callback examples.
"""
from __future__ import annotations

import asyncio
import secrets
from typing import Any

from ..schema import Event
from .base import Adapter, register


@register
class GenericHTTP(Adapter):
    framework = "generic_http"
    display_name = "Generic HTTP ingest (SDK / callbacks)"
    mode = "push"
    description = ("Receive events pushed by any framework in the AgentScope event format. Use for LangGraph, AutoGen, CrewAI, "
                   "OpenCode, OpenClaw or your own agents.")
    config_schema = [
        {"name": "source_name", "label": "Source label", "type": "text", "default": "custom",
         "help": "Shown as the event source, e.g. langgraph, crewai, autogen, opencode, openclaw."},
        {"name": "api_key", "label": "Ingest key", "type": "password", "default": "",
         "help": "Sent by the client as X-AgentScope-Key. Leave blank to auto-generate on save."},
    ]
    capabilities = {"all": "whatever the sender reports - evidence level is declared per event by the sender (default: tool_call)"}

    async def run(self):
        self.set_status("running", "listening on /api/ingest/" + self.id, received=self.stats.get("received", 0))
        while True:
            await asyncio.sleep(3600)

    def check_key(self, key: str | None) -> bool:
        expected = self.config.get("api_key") or ""
        return bool(expected) and secrets.compare_digest(expected, key or "")

    async def ingest(self, payload: list[dict[str, Any]]) -> dict:
        ok = bad = 0
        errors: list[str] = []
        src = (self.config.get("source_name") or "custom").strip() or "custom"
        for raw in payload[:500]:
            try:
                raw = dict(raw)
                raw["source"] = raw.get("source") or src
                raw.setdefault("instance_id", self.id)
                ev = Event.model_validate(raw)
                if ev.evidence == "simulated":
                    ev.metadata["simulated"] = True
                await self.emit(ev)
                ok += 1
            except Exception as e:  # noqa: BLE001
                bad += 1
                if len(errors) < 5:
                    errors.append(str(e)[:200])
        self.stats["received"] = self.stats.get("received", 0) + ok
        self.set_status("running", f"listening; last batch {ok} ok / {bad} rejected")
        return {"accepted": ok, "rejected": bad, "errors": errors}
