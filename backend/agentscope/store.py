"""SQLite persistence. All access is serialised through one worker thread."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .schema import Agent, Event, iso_to_ts

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY, ts REAL NOT NULL, timestamp TEXT NOT NULL,
  agent_id TEXT NOT NULL, parent_agent_id TEXT, event_type TEXT NOT NULL, action TEXT NOT NULL,
  category TEXT, target TEXT, tool TEXT, status TEXT, duration_ms REAL, source TEXT,
  instance_id TEXT, task_id TEXT, parent_event_id TEXT, risk TEXT, evidence TEXT, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS ix_events_agent ON events(agent_id, ts);
CREATE INDEX IF NOT EXISTS ix_events_cat ON events(category, ts);
CREATE INDEX IF NOT EXISTS ix_events_task ON events(task_id);
CREATE TABLE IF NOT EXISTS agents (agent_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, role TEXT NOT NULL, created REAL);
CREATE TABLE IF NOT EXISTS integrations (id TEXT PRIMARY KEY, framework TEXT NOT NULL, name TEXT NOT NULL,
  config TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, created REAL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rules (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alerts (id TEXT PRIMARY KEY, ts REAL NOT NULL, rule_id TEXT, severity TEXT, title TEXT,
  description TEXT, agent_id TEXT, event_id TEXT, acked INTEGER DEFAULT 0, count INTEGER DEFAULT 1, last_ts REAL, data TEXT);
CREATE INDEX IF NOT EXISTS ix_alerts_ts ON alerts(ts);
CREATE TABLE IF NOT EXISTS audit (ts REAL, username TEXT, action TEXT, detail TEXT);
"""

EVENT_COLUMNS = ["event_id", "ts", "timestamp", "agent_id", "parent_agent_id", "event_type", "action", "category",
                 "target", "tool", "status", "duration_ms", "source", "instance_id", "task_id", "parent_event_id",
                 "risk", "evidence", "data"]


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sqlite")
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    async def _run(self, fn, *a):
        return await asyncio.get_running_loop().run_in_executor(self._pool, fn, *a)

    # ---------------- events ----------------
    @staticmethod
    def _event_row(ev: Event) -> tuple:
        data = ev.model_dump()
        return (ev.event_id, ev.ts, ev.timestamp, ev.agent_id, ev.parent_agent_id, ev.event_type, ev.action,
                ev.derive_category(), ev.target, ev.tool, ev.status, ev.duration_ms, ev.source, ev.instance_id,
                ev.task_id, ev.parent_event_id, ev.risk, ev.evidence, json.dumps(data, default=str))

    async def upsert_event(self, ev: Event) -> bool:
        """Returns True if the event already existed (i.e. this was an update)."""
        def op():
            cur = self._db.execute("SELECT 1 FROM events WHERE event_id=?", (ev.event_id,))
            existed = cur.fetchone() is not None
            self._db.execute(f"INSERT OR REPLACE INTO events ({','.join(EVENT_COLUMNS)}) VALUES ({','.join('?' * len(EVENT_COLUMNS))})",
                             self._event_row(ev))
            self._db.commit()
            return existed
        return await self._run(op)

    async def get_event(self, event_id: str) -> dict | None:
        def op():
            r = self._db.execute("SELECT data, category FROM events WHERE event_id=?", (event_id,)).fetchone()
            return self._decode(r) if r else None
        return await self._run(op)

    @staticmethod
    def _decode(r: sqlite3.Row) -> dict:
        d = json.loads(r["data"])
        d["category"] = r["category"]
        return d

    @staticmethod
    def _where(f: dict) -> tuple[str, list]:
        c, p = [], []
        def eq(col, key, many=True):
            v = f.get(key)
            if v in (None, "", []):
                return
            if isinstance(v, str) and many:
                v = [x for x in v.split(",") if x]
            if isinstance(v, list):
                c.append(f"{col} IN ({','.join('?' * len(v))})"); p.extend(v)
            else:
                c.append(f"{col}=?"); p.append(v)
        eq("agent_id", "agent"); eq("event_type", "event_type"); eq("category", "category")
        eq("tool", "tool"); eq("risk", "risk"); eq("source", "source"); eq("instance_id", "instance")
        eq("evidence", "evidence"); eq("task_id", "task"); eq("parent_event_id", "parent_event")
        status = f.get("status")
        if status == "success":
            c.append("status IN ('completed','info')")
        elif status == "failure":
            c.append("status IN ('failed','blocked')")
        elif status:
            eq("status", "status")
        if f.get("since") is not None:
            c.append("ts>=?"); p.append(float(f["since"]))
        if f.get("until") is not None:
            c.append("ts<=?"); p.append(float(f["until"]))
        for key, col in (("destination", "target"), ("path", "target"), ("domain", "target")):
            if f.get(key):
                c.append(f"{col} LIKE ?"); p.append(f"%{f[key]}%")
        if f.get("process"):
            c.append("(category IN ('subprocess','terminal','python') AND (target LIKE ? OR data LIKE ?))")
            p.extend([f"%{f['process']}%"] * 2)
        if f.get("ip"):
            c.append("data LIKE ?"); p.append(f"%{f['ip']}%")
        if f.get("q"):
            c.append("(target LIKE ? OR action LIKE ? OR tool LIKE ? OR data LIKE ?)")
            p.extend([f"%{f['q']}%"] * 4)
        return (" WHERE " + " AND ".join(c)) if c else "", p

    async def query_events(self, f: dict, limit: int = 500, offset: int = 0, order: str = "desc") -> list[dict]:
        where, params = self._where(f)
        order = "ASC" if order == "asc" else "DESC"
        def op():
            rows = self._db.execute(f"SELECT data, category FROM events{where} ORDER BY ts {order} LIMIT ? OFFSET ?",
                                    params + [limit, offset]).fetchall()
            return [self._decode(r) for r in rows]
        return await self._run(op)

    async def count_events(self, f: dict) -> int:
        where, params = self._where(f)
        return await self._run(lambda: self._db.execute(f"SELECT COUNT(*) FROM events{where}", params).fetchone()[0])

    async def events_for_agent_before(self, agent_id: str, ts: float, limit: int) -> list[dict]:
        def op():
            rows = self._db.execute("SELECT data, category FROM events WHERE agent_id=? AND ts<=? ORDER BY ts DESC LIMIT ?",
                                    (agent_id, ts, limit)).fetchall()
            return [self._decode(r) for r in rows][::-1]
        return await self._run(op)

    async def distinct_targets(self, category: str) -> set[str]:
        return await self._run(lambda: {r[0] for r in self._db.execute(
            "SELECT DISTINCT target FROM events WHERE category=? AND target IS NOT NULL", (category,))})

    async def prune(self, before_ts: float) -> int:
        def op():
            n = self._db.execute("DELETE FROM events WHERE ts<?", (before_ts,)).rowcount
            self._db.execute("DELETE FROM alerts WHERE ts<?", (before_ts,))
            self._db.commit()
            return n
        return await self._run(op)

    async def clear_source(self, source: str) -> int:
        def op():
            n = self._db.execute("DELETE FROM events WHERE source=?", (source,)).rowcount
            self._db.commit()
            return n
        return await self._run(op)

    # ---------------- agents ----------------
    async def save_agent(self, a: Agent):
        def op():
            self._db.execute("INSERT OR REPLACE INTO agents VALUES (?,?)", (a.agent_id, a.model_dump_json()))
            self._db.commit()
        await self._run(op)

    async def load_agents(self) -> list[Agent]:
        return await self._run(lambda: [Agent.model_validate_json(r[0]) for r in self._db.execute("SELECT data FROM agents")])

    async def delete_agents(self, source: str | None = None):
        def op():
            if source:
                for r in self._db.execute("SELECT agent_id, data FROM agents").fetchall():
                    if json.loads(r[1]).get("source") == source:
                        self._db.execute("DELETE FROM agents WHERE agent_id=?", (r[0],))
            self._db.commit()
        await self._run(op)

    # ---------------- generic kv ----------------
    async def get_settings(self) -> dict:
        return await self._run(lambda: {r[0]: json.loads(r[1]) for r in self._db.execute("SELECT key, value FROM settings")})

    async def set_setting(self, key: str, value: Any):
        def op():
            self._db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))
            self._db.commit()
        await self._run(op)

    # ---------------- users ----------------
    async def get_user(self, username: str) -> dict | None:
        def op():
            r = self._db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            return dict(r) if r else None
        return await self._run(op)

    async def list_users(self) -> list[dict]:
        return await self._run(lambda: [dict(r) for r in self._db.execute("SELECT username, role, created FROM users")])

    async def put_user(self, username: str, password_hash: str, role: str):
        def op():
            self._db.execute("INSERT OR REPLACE INTO users VALUES (?,?,?,?)", (username, password_hash, role, time.time()))
            self._db.commit()
        await self._run(op)

    async def delete_user(self, username: str):
        def op():
            self._db.execute("DELETE FROM users WHERE username=?", (username,)); self._db.commit()
        await self._run(op)

    # ---------------- integrations ----------------
    async def list_integrations(self) -> list[dict]:
        def op():
            out = []
            for r in self._db.execute("SELECT * FROM integrations ORDER BY created"):
                d = dict(r); d["config"] = json.loads(d["config"]); d["enabled"] = bool(d["enabled"]); out.append(d)
            return out
        return await self._run(op)

    async def put_integration(self, id: str, framework: str, name: str, config: dict, enabled: bool):
        def op():
            old = self._db.execute("SELECT created FROM integrations WHERE id=?", (id,)).fetchone()
            self._db.execute("INSERT OR REPLACE INTO integrations VALUES (?,?,?,?,?,?)",
                             (id, framework, name, json.dumps(config), int(enabled), old[0] if old else time.time()))
            self._db.commit()
        await self._run(op)

    async def delete_integration(self, id: str):
        def op():
            self._db.execute("DELETE FROM integrations WHERE id=?", (id,)); self._db.commit()
        await self._run(op)

    # ---------------- rules & alerts ----------------
    async def list_rules(self) -> list[dict]:
        return await self._run(lambda: [json.loads(r[0]) for r in self._db.execute("SELECT data FROM rules")])

    async def put_rule(self, rule: dict):
        def op():
            self._db.execute("INSERT OR REPLACE INTO rules VALUES (?,?)", (rule["id"], json.dumps(rule))); self._db.commit()
        await self._run(op)

    async def delete_rule(self, id: str):
        def op():
            self._db.execute("DELETE FROM rules WHERE id=?", (id,)); self._db.commit()
        await self._run(op)

    async def save_alert(self, a: dict):
        def op():
            self._db.execute("INSERT OR REPLACE INTO alerts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                             (a["id"], a["ts"], a["rule_id"], a["severity"], a["title"], a["description"], a["agent_id"],
                              a["event_id"], int(a.get("acked", 0)), a.get("count", 1), a.get("last_ts", a["ts"]),
                              json.dumps(a.get("data", {}))))
            self._db.commit()
        await self._run(op)

    async def list_alerts(self, limit: int = 300, unacked: bool = False) -> list[dict]:
        def op():
            q = "SELECT * FROM alerts" + (" WHERE acked=0" if unacked else "") + " ORDER BY last_ts DESC LIMIT ?"
            out = []
            for r in self._db.execute(q, (limit,)):
                d = dict(r); d["acked"] = bool(d["acked"]); d["data"] = json.loads(d["data"] or "{}"); out.append(d)
            return out
        return await self._run(op)

    async def ack_alert(self, id: str | None):
        def op():
            if id:
                self._db.execute("UPDATE alerts SET acked=1 WHERE id=?", (id,))
            else:
                self._db.execute("UPDATE alerts SET acked=1")
            self._db.commit()
        await self._run(op)

    async def audit(self, username: str, action: str, detail: str = ""):
        def op():
            self._db.execute("INSERT INTO audit VALUES (?,?,?,?)", (time.time(), username, action, detail)); self._db.commit()
        await self._run(op)

    async def list_audit(self, limit: int = 200) -> list[dict]:
        return await self._run(lambda: [dict(r) for r in self._db.execute("SELECT * FROM audit ORDER BY ts DESC LIMIT ?", (limit,))])

    async def stats(self) -> dict:
        def op():
            q = self._db.execute
            return {
                "events_total": q("SELECT COUNT(*) FROM events").fetchone()[0],
                "oldest": q("SELECT MIN(ts) FROM events").fetchone()[0],
                "newest": q("SELECT MAX(ts) FROM events").fetchone()[0],
            }
        return await self._run(op)
