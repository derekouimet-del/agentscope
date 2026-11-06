"""Docker system-observability adapter.

Everything here uses the Docker Engine API (read-only calls) from *outside* the monitored
container - nothing is installed in or executed inside it unless the operator explicitly
enables the network probe.

Observed (evidence = "execution"):
  * container lifecycle events            (docker events)
  * CPU / memory / network byte counters  (stats)
  * process table samples                 (top)  - sampled, so very short-lived processes can be missed
  * filesystem changes vs. image          (diff) - paths + kind only; not attributable to a specific agent
  * TCP connections (OPT-IN)              (exec `cat /proc/net/tcp*`) - sampled; metadata only, no payloads
Not observable here: DNS queries, TLS payloads, per-agent attribution of processes/files, exit codes.
"""
from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import io
import ipaddress
import struct
import tarfile
import time
from collections import deque
from typing import Any

from ..schema import Event
from .base import Adapter, register

DEFAULT_IGNORE = ["/proc*", "/sys*", "/dev*", "*/__pycache__*", "*.pyc", "/run*", "/var/log/*", "/var/cache/*", "/var/lib/apt*",
                  "/tmp/*.lock", "*/.cache/*", "/root/.cache*", "/etc/hosts", "/etc/hostname", "/etc/resolv.conf", "/etc/mtab"]
TCP_STATES = {"01": "ESTABLISHED", "02": "SYN_SENT", "03": "SYN_RECV", "04": "FIN_WAIT1", "05": "FIN_WAIT2", "06": "TIME_WAIT",
              "07": "CLOSE", "08": "CLOSE_WAIT", "09": "LAST_ACK", "0A": "LISTEN", "0B": "CLOSING"}


def _hex_ip(h: str) -> str:
    raw = bytes.fromhex(h)
    if len(raw) == 4:
        return str(ipaddress.IPv4Address(raw[::-1]))
    words = struct.unpack("<4I", raw)
    return str(ipaddress.IPv6Address(b"".join(struct.pack(">I", w) for w in words)))


def parse_proc_net_tcp(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or ":" not in parts[1] or parts[0] == "sl":
            continue
        try:
            lip, lport = parts[1].split(":")
            rip, rport = parts[2].split(":")
            out.append({"local_ip": _hex_ip(lip), "local_port": int(lport, 16), "remote_ip": _hex_ip(rip),
                        "remote_port": int(rport, 16), "state": TCP_STATES.get(parts[3].upper(), parts[3])})
        except Exception:
            continue
    return out


@register
class DockerMonitor(Adapter):
    framework = "docker"
    display_name = "Docker system monitor"
    description = "Verified process, filesystem, network-counter and lifecycle telemetry from the Docker Engine API."
    config_schema = [
        {"name": "docker_host", "label": "Docker host", "type": "text", "default": "",
         "help": "Blank = default (unix:///var/run/docker.sock or Windows named pipe). Example: tcp://docker-proxy:2375"},
        {"name": "containers", "label": "Containers to monitor", "type": "text", "default": "",
         "help": "Comma-separated container names. Blank = containers named in Agent Zero integrations."},
        {"name": "interval", "label": "Sample interval (s)", "type": "number", "default": 3},
        {"name": "process_monitor", "label": "Process monitor (docker top)", "type": "bool", "default": True},
        {"name": "fs_diff", "label": "Filesystem changes (docker diff)", "type": "bool", "default": True},
        {"name": "watch_paths", "label": "Paths to stat for modification times", "type": "list", "default": ["/a0/usr/workdir"],
         "help": "Files under these directories are re-checked so repeated edits are noticed."},
        {"name": "ignore_paths", "label": "Ignore path globs", "type": "list", "default": DEFAULT_IGNORE},
        {"name": "network_probe", "label": "TCP connection probe (executes `cat /proc/net/tcp` in the container)", "type": "bool",
         "default": False, "help": "Requires 'Allow exec probe' in Settings. Off by default; every probe is audit-logged."},
    ]
    capabilities = {
        "processes": "verified by sampling `docker top` (short-lived processes may be missed; no exit codes)",
        "filesystem": "verified changes versus the image via `docker diff` (no agent attribution)",
        "network": "byte counters always; connection list only if the opt-in probe is enabled; never payloads",
        "dns": "not observable",
    }

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.client = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.procs: dict[str, dict[str, dict]] = {}
        self.fs_seen: dict[str, set] = {}
        self.fs_stat: dict[str, dict[str, tuple]] = {}
        self.conns: dict[str, dict[tuple, dict]] = {}
        self.baselined: set[str] = set()
        self.probe_audited: set[str] = set()

    # ---------- helpers ----------
    def _connect(self):
        import docker
        host = (self.config.get("docker_host") or "").strip()
        self.client = docker.DockerClient(base_url=host) if host else docker.from_env()
        self.client.ping()

    async def _x(self, fn, *a, timeout: float = 30):
        return await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(None, lambda: fn(*a)), timeout)

    def _targets(self) -> list[str]:
        names = [n.strip() for n in str(self.config.get("containers") or "").split(",") if n.strip()]
        return names or sorted(self.engine.container_map.keys())

    def _instance_for(self, name: str) -> str | None:
        return self.engine.container_map.get(name)

    def _agent_id(self, name: str) -> str:
        return f"docker:{name}"

    async def _ev(self, name: str, **kw) -> Event:
        meta = kw.pop("metadata", {})
        meta.setdefault("container", name)
        meta["agent_kind"] = "container"
        meta["linked_instance"] = self._instance_for(name)
        ev = Event(agent_id=self._agent_id(name), agent_name=f"container · {name}", source="docker", instance_id=self.id,
                   evidence=kw.pop("evidence", "execution"), metadata=meta, **kw)
        await self.emit(ev)
        return ev

    async def test(self) -> dict:
        try:
            await self._x(self._connect, timeout=10)
            info = await self._x(lambda: self.client.version())
            names = [c.name for c in await self._x(lambda: self.client.containers.list())]
            return {"ok": True, "message": f"Docker {info.get('Version')} reachable. Running containers: {', '.join(names) or 'none'}",
                    "containers": names}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": f"{type(e).__name__}: {e}"}

    # ---------- main ----------
    async def run(self):
        self.loop = asyncio.get_running_loop()
        await self._x(self._connect, timeout=15)
        self.set_status("running", "connected to Docker", containers=0)
        tasks = [asyncio.create_task(self._events_loop()), asyncio.create_task(self._stats_loop()),
                 asyncio.create_task(self._sample_loop())]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()

    async def stop(self):
        try:
            if self.client:
                self.client.close()
        except Exception:
            pass

    # ---------- lifecycle events ----------
    async def _events_loop(self):
        q: asyncio.Queue = asyncio.Queue()
        stop = {"v": False}

        def reader():
            try:
                for e in self.client.events(decode=True, filters={"type": ["container", "network"]}):
                    if stop["v"]:
                        break
                    self.loop.call_soon_threadsafe(q.put_nowait, e)
            except Exception as ex:  # noqa: BLE001
                self.loop.call_soon_threadsafe(q.put_nowait, {"_error": str(ex)})

        fut = self.loop.run_in_executor(None, reader)
        try:
            while True:
                e = await q.get()
                if "_error" in e:
                    raise RuntimeError(e["_error"])
                attrs = (e.get("Actor") or {}).get("Attributes") or {}
                name = attrs.get("name") or ""
                action = (e.get("Action") or "").split(":")[0]
                if e.get("Type") == "container" and name in self._targets() and action in (
                        "start", "stop", "die", "kill", "oom", "restart", "pause", "unpause", "health_status", "destroy"):
                    await self._ev(name, event_type="system", action="container_state", target=f"{name}: {e.get('Action')}",
                                   status="info" if action not in ("die", "oom", "kill") else "failed",
                                   metadata={"docker_action": e.get("Action"), "exit_code": attrs.get("exitCode"),
                                             "image": attrs.get("image")})
                elif e.get("Type") == "network" and action in ("connect", "disconnect"):
                    cont = attrs.get("container")
                    names = {c for c in self._targets()}
                    try:
                        cname = (await self._x(lambda: self.client.containers.get(cont).name)) if cont else ""
                    except Exception:
                        cname = ""
                    if cname in names:
                        await self._ev(cname, event_type="system", action="container_state", target=f"network {attrs.get('name')}: {action}",
                                       status="info", metadata={"docker_network": attrs.get("name"), "docker_action": action})
        finally:
            stop["v"] = True
            fut.cancel()

    # ---------- stats ----------
    async def _stats_loop(self):
        while True:
            for name in self._targets():
                try:
                    c = await self._x(lambda n=name: self.client.containers.get(n))
                    s = await self._x(lambda c=c: c.stats(stream=False), timeout=20)
                except Exception:
                    self.engine.metrics.pop(name, None)
                    continue
                cpu = s["cpu_stats"]["cpu_usage"]["total_usage"] - s["precpu_stats"]["cpu_usage"].get("total_usage", 0)
                sysd = s["cpu_stats"].get("system_cpu_usage", 0) - s["precpu_stats"].get("system_cpu_usage", 0)
                ncpu = s["cpu_stats"].get("online_cpus") or len(s["cpu_stats"]["cpu_usage"].get("percpu_usage") or [1])
                pct = (cpu / sysd * ncpu * 100.0) if sysd > 0 else 0.0
                mem = s["memory_stats"].get("usage", 0) - (s["memory_stats"].get("stats", {}).get("inactive_file", 0)
                                                            or s["memory_stats"].get("stats", {}).get("cache", 0))
                nets = s.get("networks") or {}
                rx, tx = sum(n["rx_bytes"] for n in nets.values()), sum(n["tx_bytes"] for n in nets.values())
                prev = self.engine.metrics.get(name) or {"history": deque(maxlen=90)}
                now = time.time()
                m = {"container": name, "ts": now, "cpu_pct": round(pct, 2), "mem_bytes": mem,
                     "mem_limit": s["memory_stats"].get("limit"), "net_rx": rx, "net_tx": tx,
                     "pids": s.get("pids_stats", {}).get("current"), "status": c.status,
                     "image": (c.image.tags[0] if c.image.tags else c.image.short_id),
                     "networks": list(nets.keys()), "linked_instance": self._instance_for(name), "history": prev["history"]}
                if prev.get("ts"):
                    dt = max(now - prev["ts"], 0.001)
                    m["rx_rate"], m["tx_rate"] = max(0, (rx - prev["net_rx"]) / dt), max(0, (tx - prev["net_tx"]) / dt)
                else:
                    m["rx_rate"] = m["tx_rate"] = 0
                m["history"].append({"ts": now, "cpu": m["cpu_pct"], "mem": mem, "rx": m.get("rx_rate", 0), "tx": m.get("tx_rate", 0)})
                self.engine.metrics[name] = m
                self.engine.hub.publish({"type": "metrics", "metrics": {k: v for k, v in m.items() if k != "history"}})
            self.set_status("running", "monitoring", containers=len(self._targets()))
            await asyncio.sleep(max(2, float(self.config.get("interval") or 3)))

    # ---------- processes / filesystem / network ----------
    async def _sample_loop(self):
        tick = 0
        while True:
            for name in self._targets():
                try:
                    if self.config.get("process_monitor", True):
                        await self._sample_processes(name)
                    if self.config.get("fs_diff", True) and tick % 4 == 0:
                        await self._sample_fs(name)
                    if self.config.get("network_probe") and tick % 1 == 0:
                        await self._sample_network(name)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    self.log.debug("sample %s failed: %s", name, e)
            tick += 1
            await asyncio.sleep(max(2, float(self.config.get("interval") or 3)))

    async def _sample_processes(self, name: str):
        c = await self._x(lambda: self.client.containers.get(name))
        top = await self._x(lambda: c.top(ps_args="-eo user,pid,ppid,etimes,args"), timeout=15)
        titles = [t.lower() for t in top["Titles"]]
        rows = []
        for p in top["Processes"] or []:
            d = dict(zip(titles, p))
            try:
                rows.append({"user": d.get("user"), "pid": int(d["pid"]), "ppid": int(d["ppid"]), "age": int(d.get("elapsed", d.get("etimes", 0)) or 0),
                             "cmd": d.get("args") or d.get("command") or ""})
            except (KeyError, ValueError):
                continue
        now = time.time()
        known = self.procs.setdefault(name, {})
        first = name not in self.baselined
        seen: set[str] = set()
        for r in rows:
            key = f"{r['pid']}:{hashlib.sha1((r['cmd'] + str(int(now - r['age']) // 2)).encode()).hexdigest()[:8]}"
            seen.add(key)
            if key in known:
                continue
            started = now - r["age"]
            known[key] = {**r, "key": key, "started": started}
            await self._ev(name, event_id=f"proc:{name}:{key}", timestamp=_iso(started if not first else now), event_type="process",
                           action="process_spawn", target=r["cmd"][:500], tool=(r["cmd"].split() or [""])[0].rsplit("/", 1)[-1],
                           status="running",
                           metadata={"pid": r["pid"], "ppid": r["ppid"], "user": r["user"], "cmd": r["cmd"][:2000], "baseline": first,
                                     "pid_namespace": "docker host (as reported by `docker top`)",
                                     "attribution": "container only - which agent started this process is not observable",
                                     "exit_code": None, "exit_code_note": "not observable"})
        for key in [k for k in known if k not in seen]:
            r = known.pop(key)
            await self._ev(name, event_id=f"proc:{name}:{key}", timestamp=_iso(r["started"]), event_type="process", action="process_spawn",
                           target=r["cmd"][:500], tool=(r["cmd"].split() or [""])[0].rsplit("/", 1)[-1], status="completed",
                           duration_ms=(now - r["started"]) * 1000,
                           metadata={"pid": r["pid"], "ppid": r["ppid"], "user": r["user"], "cmd": r["cmd"][:2000], "ended": True,
                                     "baseline": False, "exit_code": None, "exit_code_note": "not observable",
                                     "duration_note": "approximate (sampling interval)",
                                     "attribution": "container only"})
        self.baselined.add(name)
        self.engine.process_snapshots[name] = {"ts": now, "container": name, "processes": rows,
                                               "linked_instance": self._instance_for(name)}
        self.engine.hub.publish({"type": "processes", "container": name, "ts": now, "count": len(rows)})

    def _ignored(self, path: str) -> bool:
        pats = self.config.get("ignore_paths") or DEFAULT_IGNORE
        return any(fnmatch.fnmatch(path, p) for p in pats)

    def _stat_sync(self, name: str, path: str):
        stream, stat = self.client.api.get_archive(name, path)
        try:
            stream.close()
        except Exception:
            pass
        return stat

    async def _sample_fs(self, name: str):
        c = await self._x(lambda: self.client.containers.get(name))
        diff = await self._x(lambda: c.diff(), timeout=45) or []
        kinds = {0: "file_modify", 1: "file_create", 2: "file_delete"}
        seen = self.fs_seen.setdefault(name, set())
        first = name not in self.fs_stat
        stats = self.fs_stat.setdefault(name, {})
        new = []
        for d in diff:
            path, kind = d["Path"], d["Kind"]
            if self._ignored(path):
                continue
            if (path, kind) not in seen:
                seen.add((path, kind))
                new.append((path, kind))
        watch = [w.rstrip("/") + "/" for w in (self.config.get("watch_paths") or [])]
        hot = [p for p, k in seen if k != 2 and any(p.startswith(w) for w in watch)][:150]
        for p in hot:  # detect re-modification of watched files via mtime/size
            try:
                st = await self._x(self._stat_sync, name, p, timeout=10)
            except Exception:
                continue
            sig = (st.get("mtime"), st.get("size"))
            old = stats.get(p)
            stats[p] = sig
            if old and old != sig and (p, 0) not in [(x, k) for x, k in new]:
                new.append((p, 0))
        if first:
            self.fs_stat[name] = stats
            self.log.info("docker diff baseline for %s: %d paths", name, len(new))
            for p, k in new:  # baseline: not reported as new activity
                pass
            return
        for path, kind in new[:300]:
            size = mtime = None
            if kind != 2:
                try:
                    st = stats.get(path) and None or await self._x(self._stat_sync, name, path, timeout=10)
                    size, mtime = st.get("size"), st.get("mtime")
                    stats[path] = (mtime, size)
                    is_dir = bool(int(st.get("mode", 0)) & (1 << 31))
                except Exception:
                    is_dir = False
            else:
                is_dir = False
            if is_dir and kind == 0:
                continue
            await self._ev(name, event_type="file_operation", action=kinds.get(kind, "file_modify"), target=path, status="completed",
                           metadata={"size": size, "mtime": mtime, "is_dir": is_dir, "detected_by": "docker diff (+ stat for mtime)",
                                     "attribution": "container only - the agent that did this is not observable here",
                                     "timestamp_note": "time of detection, not of the operation"})
        if len(new) > 300:
            await self._ev(name, event_type="system", action="status", target=f"{len(new) - 300} further filesystem changes not listed",
                           status="info", metadata={})

    async def _sample_network(self, name: str):
        if not self.engine.settings.get("allow_exec_probe"):
            self.set_status("running", "network probe enabled in integration but not permitted in Settings")
            return
        if name not in self.probe_audited:
            self.probe_audited.add(name)
            await self.engine.store.audit("system", "exec_probe", f"AgentScope runs `cat /proc/net/tcp /proc/net/tcp6` in {name} (opt-in)")
        c = await self._x(lambda: self.client.containers.get(name))
        res = await self._x(lambda: c.exec_run(["cat", "/proc/net/tcp", "/proc/net/tcp6"], demux=False), timeout=10)
        if res.exit_code != 0:
            return
        rows = parse_proc_net_tcp(res.output.decode(errors="replace"))
        listening = {r["local_port"] for r in rows if r["state"] == "LISTEN"}
        live: dict[tuple, dict] = {}
        for r in rows:
            if r["state"] not in ("ESTABLISHED", "SYN_SENT"):
                continue
            inbound = r["local_port"] in listening
            live[(r["local_ip"], r["local_port"], r["remote_ip"], r["remote_port"])] = {**r, "inbound": inbound}
        known = self.conns.setdefault(name, {})
        now = time.time()
        first = name not in self.baselined or f"{name}:net" not in self.baselined
        self.baselined.add(f"{name}:net")
        for key, r in live.items():
            if key in known:
                continue
            known[key] = {"started": now, **r}
            ip, port = r["remote_ip"], r["remote_port"]
            host = self.engine.net.reverse(ip)
            await self._ev(name, event_id=f"conn:{name}:{hash(key) & 0xffffffff:x}:{int(now)}", event_type="network",
                           action="network_connection", target=f"{ip}:{port}", tool="tcp", status="running",
                           metadata={"baseline": first, "network": {
                               "ip": ip, "port": port, "host": host or ip, "protocol": "tcp", "direction": "inbound" if r["inbound"] else "outbound",
                               "state": r["state"], "visibility": "connection_metadata", "host_source": "reverse of AgentScope DNS cache" if host else None,
                               "local": f"{r['local_ip']}:{r['local_port']}", "bytes_in": None, "bytes_out": None},
                               "attribution": "container only (process owner not resolved)"})
            known[key]["event_id"] = f"conn:{name}:{hash(key) & 0xffffffff:x}:{int(now)}"
        for key in [k for k in known if k not in live]:
            r = known.pop(key)
            ip, port = r["remote_ip"], r["remote_port"]
            await self._ev(name, event_id=r["event_id"], timestamp=_iso(r["started"]), event_type="network", action="network_connection",
                           target=f"{ip}:{port}", tool="tcp", status="completed", duration_ms=(now - r["started"]) * 1000,
                           metadata={"network": {"ip": ip, "port": port, "host": self.engine.net.reverse(ip) or ip, "protocol": "tcp",
                                                 "direction": "inbound" if r["inbound"] else "outbound", "state": "CLOSED",
                                                 "visibility": "connection_metadata"},
                                     "duration_note": "approximate (sampling interval)", "attribution": "container only"})

    # ---------- on-demand inspection (used by the API) ----------
    async def stat_path(self, container: str, path: str) -> dict:
        st = await self._x(self._stat_sync, container, path, timeout=15)
        return {"path": path, "name": st.get("name"), "size": st.get("size"), "mode": st.get("mode"), "mtime": st.get("mtime"),
                "is_dir": bool(int(st.get("mode", 0)) & (1 << 31)), "link": st.get("linkTarget")}

    async def read_file(self, container: str, path: str, limit: int = 200_000) -> dict:
        def op():
            stream, stat = self.client.api.get_archive(container, path)
            buf = io.BytesIO()
            for chunk in stream:
                buf.write(chunk)
                if buf.tell() > limit * 3 + 65536:
                    break
            buf.seek(0)
            try:
                with tarfile.open(fileobj=buf, mode="r|*") as tar:
                    for m in tar:
                        if m.isfile():
                            data = tar.extractfile(m).read(limit + 1)
                            return stat, data
            except Exception:
                pass
            return stat, b""
        stat, data = await self._x(op, timeout=30)
        truncated = len(data) > limit
        text = data[:limit]
        binary = b"\x00" in text[:4096]
        return {"path": path, "size": stat.get("size"), "truncated": truncated, "binary": binary,
                "content": None if binary else text.decode(errors="replace")}


def _iso(ts: float) -> str:
    from ..schema import ts_to_iso
    return ts_to_iso(ts)
