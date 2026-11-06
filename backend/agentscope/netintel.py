"""Network enrichment: scope classification, optional DNS resolution and optional IP geolocation."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from typing import Any

import httpx

from . import classify


class NetIntel:
    def __init__(self):
        self._dns: dict[str, tuple[float, list[str]]] = {}
        self._geo: dict[str, dict | None] = {}
        self._sem = asyncio.Semaphore(2)
        self._geo_last = 0.0

    # --------- synchronous, cheap, always on ---------
    @staticmethod
    def base_meta(target: str, existing: dict | None = None, evidence: str = "tool_call") -> dict:
        info = classify.parse_target(target)
        host, port, scheme = info["host"], info["port"], info["scheme"]
        ip = host if _is_ip(host) else None
        net: dict[str, Any] = {
            "host": host, "ip": ip, "port": port, "scheme": scheme,
            "protocol": scheme or classify.PORT_PROTO.get(port or 0) or "tcp",
            "scope": classify.classify_host(host, ip),
            "encrypted": scheme in ("https", "wss") or port == 443,
        }
        if info.get("path") and scheme in ("http", "https", "ws", "wss"):
            net["path"] = info["path"]
        # What can honestly be known about this record:
        if evidence == "execution":
            net["visibility"] = "connection_metadata"      # seen on the wire / in socket tables
        elif net["encrypted"]:
            net["visibility"] = "reported_url_only"        # URL came from the agent; TLS payload is NOT observable
        else:
            net["visibility"] = "reported_request"
        if existing:
            net.update({k: v for k, v in existing.items() if v is not None})
            if net.get("host") and not existing.get("scope"):
                net["scope"] = classify.classify_host(net["host"], net.get("ip"))
        return net

    def reverse(self, ip: str) -> str | None:
        for host, (_, ips) in self._dns.items():
            if ip in ips:
                return host
        return None

    # --------- async, optional ---------
    async def resolve(self, host: str) -> list[str]:
        cached = self._dns.get(host)
        if cached and time.time() - cached[0] < 600:
            return cached[1]
        try:
            infos = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM), 3)
            ips = list(dict.fromkeys(i[4][0] for i in infos))
        except Exception:
            ips = []
        self._dns[host] = (time.time(), ips)
        return ips

    async def geolocate(self, ip: str, provider: str) -> dict | None:
        if provider != "ipapi" or not ip:
            return None
        if ip in self._geo:
            return self._geo[ip]
        try:
            if ipaddress.ip_address(ip).is_private:
                return None
        except ValueError:
            return None
        async with self._sem:
            wait = 1.4 - (time.time() - self._geo_last)  # ip-api free tier: 45 req/min
            if wait > 0:
                await asyncio.sleep(wait)
            self._geo_last = time.time()
            try:
                async with httpx.AsyncClient(timeout=4) as c:
                    r = await c.get(f"http://ip-api.com/json/{ip}", params={"fields": "status,country,countryCode,city,lat,lon,isp,as"})
                d = r.json()
                geo = {"country": d.get("country"), "country_code": d.get("countryCode"), "city": d.get("city"),
                       "lat": d.get("lat"), "lon": d.get("lon"), "isp": d.get("isp"), "asn": d.get("as"),
                       "provider": "ip-api.com"} if d.get("status") == "success" else None
            except Exception:
                geo = None
        self._geo[ip] = geo
        return geo


def _is_ip(s: str | None) -> bool:
    try:
        ipaddress.ip_address((s or "").strip("[]"))
        return True
    except ValueError:
        return False
