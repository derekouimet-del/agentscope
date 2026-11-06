"""Adapter contract. To add a framework: subclass `Adapter`, decorate with `@register`.

Nothing in the engine, API or UI needs to change - adapters are auto-discovered from
this package and their `config_schema` drives the Integrations form.
"""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from ..engine import Engine
from ..schema import Event

REGISTRY: dict[str, type["Adapter"]] = {}


def register(cls: type["Adapter"]) -> type["Adapter"]:
    REGISTRY[cls.framework] = cls
    return cls


class Adapter(ABC):
    framework: ClassVar[str]
    display_name: ClassVar[str]
    description: ClassVar[str] = ""
    # "pull": adapter polls/streams from the framework. "push": framework sends events to AgentScope.
    mode: ClassVar[str] = "pull"
    # Field spec rendered by the UI: {name, label, type(text|password|number|bool|list), default, help, required}
    config_schema: ClassVar[list[dict[str, Any]]] = []
    capabilities: ClassVar[dict[str, str]] = {}   # what this adapter can honestly observe

    def __init__(self, integration_id: str, name: str, config: dict[str, Any], engine: Engine):
        self.id = integration_id
        self.name = name
        self.config = config
        self.engine = engine
        self.log = logging.getLogger(f"agentscope.adapter.{self.framework}")
        self.state = "stopped"
        self.message = ""
        self.stats: dict[str, Any] = {}
        self.since: float | None = None

    # ---- status ----
    def set_status(self, state: str, message: str = "", **stats):
        self.state, self.message = state, message
        self.stats.update(stats)
        self.engine.adapter_status[self.id] = self.status()
        self.engine.hub.publish({"type": "adapter_status", "id": self.id, "status": self.status()})

    def status(self) -> dict:
        return {"state": self.state, "message": self.message, "stats": self.stats, "since": self.since,
                "framework": self.framework, "name": self.name}

    # ---- emit ----
    async def emit(self, ev: Event) -> Event | None:
        ev.source = ev.source or self.framework
        ev.instance_id = ev.instance_id or self.id
        return await self.engine.emit(ev)

    # ---- lifecycle ----
    @abstractmethod
    async def run(self) -> None:
        """Long-running coroutine. Raise to trigger a supervised restart with backoff."""

    async def test(self) -> dict:
        return {"ok": True, "message": "No connectivity test for this adapter."}

    async def stop(self) -> None:
        """Optional cleanup hook (called after cancellation)."""


class AdapterManager:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.instances: dict[str, Adapter] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    async def _supervise(self, ad: Adapter):
        backoff = 2
        while True:
            try:
                ad.since = time.time()
                ad.set_status("running", "starting")
                await ad.run()
                ad.set_status("stopped", "adapter finished")
                return
            except asyncio.CancelledError:
                ad.set_status("stopped", "stopped")
                raise
            except Exception as e:  # noqa: BLE001
                ad.log.warning("adapter %s failed: %s", ad.id, e)
                ad.set_status("error", f"{type(e).__name__}: {e}; retrying in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def start(self, integration: dict, secrets) -> Adapter | None:
        cls = REGISTRY.get(integration["framework"])
        if not cls:
            return None
        self.stop_sync(integration["id"])
        cfg = secrets.decrypt_config(integration["config"])
        ad = cls(integration["id"], integration["name"], cfg, self.engine)
        self.instances[ad.id] = ad
        if integration.get("enabled", True):
            self._tasks[ad.id] = asyncio.create_task(self._supervise(ad))
        else:
            ad.set_status("stopped", "disabled")
        return ad

    def stop_sync(self, id: str):
        t = self._tasks.pop(id, None)
        if t:
            t.cancel()
        self.instances.pop(id, None)
        self.engine.adapter_status.pop(id, None)

    async def stop(self, id: str):
        ad = self.instances.get(id)
        t = self._tasks.pop(id, None)
        if t:
            t.cancel()
            try:
                await t
            except BaseException:
                pass
        if ad:
            try:
                await ad.stop()
            except Exception:
                pass
        self.instances.pop(id, None)
        self.engine.adapter_status.pop(id, None)

    async def stop_all(self):
        for id in list(self._tasks):
            await self.stop(id)


def discover():
    import importlib
    import pkgutil
    from .. import adapters
    for m in pkgutil.iter_modules(adapters.__path__):
        if m.name != "base":
            importlib.import_module(f"{adapters.__name__}.{m.name}")
    return REGISTRY
